"""WorkerPool: 持久化 Worker 池，直接管理 ClaudeSDKClient。

Worker 创建后保持存活，可接收多次任务。按 name 缓存，支持 idle 自动清理。
跳过 CCBotAgent+AgentPool，直接操作 SDK。
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

from loguru import logger

from ccbot.config import AgentConfig
from ccbot.models import WorkerTask
from ccbot.observability import configure_langsmith_once
from ccbot.runtime.pool import _sanitize_sdk_host_env
from ccbot.runtime.profiles import RuntimeRole, build_sdk_options
from ccbot.runtime.sdk_utils import build_stderr_capture, is_retryable_sdk_error, query_and_collect

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from claude_agent_sdk import ClaudeSDKClient

_CLEANUP_CHECK_INTERVAL = 120


class WorkerStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"


@dataclass
class WorkerInfo:
    """Worker 元数据。"""

    name: str
    cwd: str
    model: str
    owner_id: str = ""
    key: str = ""
    max_turns: int = 30
    status: WorkerStatus = WorkerStatus.IDLE
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    task_count: int = 0


if TYPE_CHECKING:
    ProgressCallback: TypeAlias = Callable[[str], Awaitable[None]] | None
else:
    ProgressCallback = object


@dataclass(slots=True)
class _ExecuteCommand:
    task: str
    future: asyncio.Future[str]
    on_progress: ProgressCallback


@dataclass(slots=True)
class _ShutdownCommand:
    pass


_WorkerCommand: TypeAlias = _ExecuteCommand | _ShutdownCommand


@dataclass(slots=True)
class _WorkerActor:
    info: WorkerInfo
    queue: asyncio.Queue[_WorkerCommand]
    task: asyncio.Task[None]
    ready: asyncio.Future[None]
    shutdown_requested: bool = False


class WorkerPool:
    """持久化 Worker 池，直接管理 ClaudeSDKClient。"""

    def __init__(self, base_config: AgentConfig) -> None:
        self._base_config = base_config
        self._idle_timeout = base_config.worker_idle_timeout
        self._clients: dict[str, ClaudeSDKClient] = {}
        self._info: dict[str, WorkerInfo] = {}
        self._actors: dict[str, _WorkerActor] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._stderr_captures: dict[str, object] = {}
        self._running = False
        self._max_pooled_workers = base_config.max_pooled_workers
        self._registry_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="worker-pool-cleanup")
        logger.info("WorkerPool 已启动，idle_timeout={}s", self._idle_timeout)

    async def stop(self) -> None:
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None

        names = list(self._actors.keys())
        for name in names:
            await self._kill_key(name)
        logger.info("WorkerPool 已停止")

    async def get_or_create(self, task: WorkerTask, *, owner_id: str = "") -> None:
        """获取或创建 Worker。"""
        key = self._worker_key(task.name, owner_id)

        async with self._registry_lock:
            actor = self._actors.get(key)
            if actor is not None and not actor.task.done():
                logger.info("复用已有 Worker: name={} owner={}", task.name, owner_id or "-")
                return

            await self._evict_if_needed(key)

            loop = asyncio.get_running_loop()
            ready: asyncio.Future[None] = loop.create_future()
            queue: asyncio.Queue[_WorkerCommand] = asyncio.Queue()
            info = WorkerInfo(
                name=task.name,
                cwd=str(task.cwd),
                model=task.model or self._base_config.model or "default",
                owner_id=owner_id,
                key=key,
                max_turns=task.max_turns,
            )
            actor_task = asyncio.create_task(
                self._worker_actor(task, info, queue, ready),
                name=f"worker-actor:{key}",
            )
            actor = _WorkerActor(info=info, queue=queue, task=actor_task, ready=ready)
            self._actors[key] = actor
            self._info[key] = info
            actor_task.add_done_callback(
                lambda done, worker_key=key: self._on_actor_done(worker_key, done)
            )

        await ready
        logger.info(
            "创建新 Worker: name={} owner={} cwd={} model={}",
            task.name,
            owner_id or "-",
            task.cwd,
            task.model or "default",
        )

    async def preload_workers(self, tasks: list[WorkerTask], *, owner_id: str = "") -> None:
        """预加载所有 Worker，确保它们在返回前都已就绪。

        用于防止后台派发时，消息队列认为处理已完成，导致并发创建 Worker。
        """
        for task in tasks:
            await self.get_or_create(task, owner_id=owner_id)
            # 短暂等待确保 Worker 完全初始化
            await asyncio.sleep(0.1)
        logger.info("预加载完成: {} 个 Worker (owner={})", len(tasks), owner_id or "-")

    async def send(
        self,
        name: str,
        task: str,
        *,
        owner_id: str = "",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        key = self._worker_key(name, owner_id)
        actor = self._actors.get(key)
        if actor is None:
            raise KeyError(f"Worker '{name}' 不存在")

        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        await actor.queue.put(_ExecuteCommand(task=task, future=future, on_progress=on_progress))
        return await future

    async def kill(self, name: str, *, owner_id: str = "") -> None:
        await self._kill_key(self._worker_key(name, owner_id))

    async def interrupt(self, name: str, *, owner_id: str = "") -> bool:
        """中断指定 Worker 当前任务。"""
        key = self._worker_key(name, owner_id)
        client = self._clients.get(key)
        if client is None:
            return False
        try:
            await client.interrupt()
            logger.info("中断 Worker: name={} owner={}", name, owner_id or "-")
            return True
        except Exception as e:
            logger.warning("中断 Worker 失败: name={} owner={} error={}", name, owner_id or "-", e)
            return False

    async def _kill_key(self, key: str) -> None:
        actor = self._actors.get(key)
        if actor is None:
            return
        if actor.shutdown_requested:
            await asyncio.gather(actor.task, return_exceptions=True)
            return

        actor.shutdown_requested = True

        if not actor.ready.done():
            actor.task.cancel()
            await asyncio.gather(actor.task, return_exceptions=True)
            return

        if actor.info.status == WorkerStatus.RUNNING:
            client = self._clients.get(key)
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.interrupt()

        await actor.queue.put(_ShutdownCommand())
        await asyncio.gather(actor.task, return_exceptions=True)

    async def _evict_if_needed(self, incoming_name: str) -> None:
        if incoming_name in self._actors or self._max_pooled_workers <= 0:
            return
        if len(self._actors) < self._max_pooled_workers:
            return

        idle_candidates = [
            info
            for info in self._info.values()
            if info.status == WorkerStatus.IDLE and info.key != incoming_name
        ]
        if not idle_candidates:
            raise RuntimeError(
                "Worker 池已满，且当前没有可回收的空闲 Worker；请稍后重试或复用已有 Worker name。"
            )

        victim = min(idle_candidates, key=lambda info: info.last_used)
        logger.info(
            "Worker 池达到上限={}，回收最久未使用的空闲 Worker: name={} (incoming={})",
            self._max_pooled_workers,
            victim.name,
            incoming_name,
        )
        await self._kill_key(victim.key)

    def list_workers(self, *, owner_id: str | None = None) -> list[WorkerInfo]:
        workers = list(self._info.values())
        if owner_id is not None:
            workers = [info for info in workers if info.owner_id == owner_id]
        return workers

    def format_status(self, *, owner_id: str | None = None) -> str:
        workers = self.list_workers(owner_id=owner_id)
        if not workers:
            return ""
        lines = ["[系统信息] 当前活跃 Workers:"]
        for info in workers:
            if info.status == WorkerStatus.RUNNING:
                status = "执行中"
            else:
                status = "空闲"
            label = info.name
            if owner_id is None and info.owner_id:
                label = f"{info.name} [owner={info.owner_id}]"
            lines.append(f"- {label} ({status}): cwd={info.cwd}, 已执行 {info.task_count} 次任务")
        lines.append("如需向已有 Worker 追加任务，使用相同 name 即可。")
        return "\n".join(lines)

    def has_worker(self, name: str, *, owner_id: str = "") -> bool:
        return self._worker_key(name, owner_id) in self._actors

    async def _create_client(
        self, task: WorkerTask, *, worker_key: str | None = None
    ) -> ClaudeSDKClient:
        """创建 ClaudeSDKClient。"""
        _sanitize_sdk_host_env()
        configure_langsmith_once(self._base_config)

        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        _setup_worker_workspace(task.cwd)

        model = task.model or self._base_config.model
        kwargs = build_sdk_options(
            self._base_config,
            role=RuntimeRole.WORKER,
            cwd=task.cwd,
            model=model,
            max_turns=task.max_turns,
        )

        capture_key = worker_key or task.name
        stderr_capture = build_stderr_capture(f"[sdk:worker:{capture_key}]")
        kwargs["stderr"] = stderr_capture.callback
        options = ClaudeAgentOptions(**kwargs)
        client = ClaudeSDKClient(options)
        self._stderr_captures[capture_key] = stderr_capture
        try:
            await client.connect()
        except Exception:
            raise
        return client

    async def _cleanup_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(_CLEANUP_CHECK_INTERVAL)
                await self._cleanup_idle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("WorkerPool 清理任务出错: {}", e)

    async def _cleanup_idle(self) -> None:
        if self._idle_timeout <= 0:
            return
        now = time.time()
        idle_names = [
            name
            for name, info in self._info.items()
            if info.status == WorkerStatus.IDLE and now - info.last_used > self._idle_timeout
        ]
        for name in idle_names:
            logger.info("清理空闲 Worker: name={} (idle > {}s)", name, self._idle_timeout)
            await self._kill_key(name)

    @staticmethod
    def _worker_key(name: str, owner_id: str = "") -> str:
        owner = owner_id.strip()
        return f"{owner}::{name}" if owner else name

    def _on_actor_done(self, key: str, task: asyncio.Task[None]) -> None:
        actor = self._actors.get(key)
        if actor is not None and actor.task is task:
            self._actors.pop(key, None)

        self._clients.pop(key, None)
        self._stderr_captures.pop(key, None)
        self._info.pop(key, None)

        with contextlib.suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                logger.error("Worker actor 异常退出: key={} error={}", key, exc)

    async def _worker_actor(
        self,
        task_def: WorkerTask,
        info: WorkerInfo,
        queue: asyncio.Queue[_WorkerCommand],
        ready: asyncio.Future[None],
    ) -> None:
        client: ClaudeSDKClient | None = None
        disconnected = False

        try:
            client = await self._create_client(task_def, worker_key=info.key)
            self._clients[info.key] = client
            if not ready.done():
                ready.set_result(None)

            while True:
                command = await queue.get()
                try:
                    if isinstance(command, _ExecuteCommand):
                        client, result = await self._execute_command(
                            client,
                            info,
                            command.task,
                            command.on_progress,
                        )
                        if not command.future.done():
                            command.future.set_result(result)
                        continue

                    await client.disconnect()
                    disconnected = True
                    return
                except Exception as exc:
                    if isinstance(command, _ExecuteCommand) and not command.future.done():
                        command.future.set_exception(exc)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
            raise
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
                return
            raise
        finally:
            while not queue.empty():
                pending = queue.get_nowait()
                if isinstance(pending, _ExecuteCommand) and not pending.future.done():
                    pending.future.set_exception(RuntimeError(f"Worker '{info.name}' 已停止"))
                queue.task_done()

            if client is not None and not disconnected:
                try:
                    await client.disconnect()
                except BaseException as exc:
                    logger.warning("销毁 Worker 出错: name={} error={}", info.name, exc)

    async def _execute_command(
        self,
        client: ClaudeSDKClient,
        info: WorkerInfo,
        task_text: str,
        on_progress: Callable[[str], Awaitable[None]] | None,
    ) -> tuple[ClaudeSDKClient, str]:
        info.status = WorkerStatus.RUNNING
        info.last_used = time.time()

        current_client = client
        try:
            for attempt in range(2):
                try:
                    result = await query_and_collect(
                        current_client,
                        task_text,
                        session_id=info.key,
                        on_progress=on_progress,
                        log_prefix=f"[worker:{info.key}]",
                    )
                    info.task_count += 1
                    return current_client, result
                except Exception as exc:
                    should_retry = attempt == 0 and is_retryable_sdk_error(exc)
                    if not should_retry:
                        raise

                    logger.warning(
                        "Worker 会话异常退出，正在重建后重试一次: name={} owner={} error={}",
                        info.name,
                        info.owner_id or "-",
                        exc,
                    )
                    with contextlib.suppress(BaseException):
                        await current_client.disconnect()

                    recreate_task = WorkerTask(
                        name=info.name,
                        task="resume worker session",
                        cwd=info.cwd,
                        model="" if info.model == "default" else info.model,
                        max_turns=info.max_turns,
                    )
                    current_client = await self._create_client(recreate_task, worker_key=info.key)
                    self._clients[info.key] = current_client
        finally:
            info.status = WorkerStatus.IDLE
            info.last_used = time.time()


_WORKER_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "worker"


def _setup_worker_workspace(cwd: Path | str) -> None:
    """在 worker cwd 中配置 .claude/ 环境（不覆盖已有文件）。"""
    cwd = Path(cwd)
    if not cwd.is_dir():
        return

    template_claude_dir = _WORKER_TEMPLATE_DIR / ".claude"
    if not template_claude_dir.exists():
        return

    try:
        claude_dir = cwd / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        for filename in ("CLAUDE.md", "settings.json"):
            dest = claude_dir / filename
            if dest.exists():
                continue
            src = template_claude_dir / filename
            if src.exists():
                shutil.copy2(src, dest)
    except OSError:
        pass
