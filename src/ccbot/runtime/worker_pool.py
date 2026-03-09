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
from typing import TYPE_CHECKING

from loguru import logger

from ccbot.config import AgentConfig
from ccbot.models import WorkerTask
from ccbot.observability import configure_langsmith_once
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
    max_turns: int = 30
    status: WorkerStatus = WorkerStatus.IDLE
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    task_count: int = 0


class WorkerPool:
    """持久化 Worker 池，直接管理 ClaudeSDKClient。"""

    def __init__(self, base_config: AgentConfig) -> None:
        self._base_config = base_config
        self._idle_timeout = base_config.worker_idle_timeout
        self._clients: dict[str, ClaudeSDKClient] = {}
        self._info: dict[str, WorkerInfo] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._stderr_captures: dict[str, object] = {}
        self._running = False
        self._max_pooled_workers = base_config.max_pooled_workers

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

        names = list(self._clients.keys())
        for name in names:
            await self._kill(name)
        logger.info("WorkerPool 已停止")

    async def get_or_create(self, task: WorkerTask) -> None:
        """获取或创建 Worker。"""
        if task.name in self._clients:
            logger.info("复用已有 Worker: name={}", task.name)
            return

        await self._evict_if_needed(task.name)

        client = await self._create_client(task)
        self._clients[task.name] = client
        self._info[task.name] = WorkerInfo(
            name=task.name,
            cwd=str(task.cwd),
            model=task.model or self._base_config.model or "default",
            max_turns=task.max_turns,
        )
        logger.info(
            "创建新 Worker: name={} cwd={} model={}",
            task.name,
            task.cwd,
            task.model or "default",
        )

    async def preload_workers(self, tasks: list[WorkerTask]) -> None:
        """预加载所有 Worker，确保它们在返回前都已就绪。

        用于防止后台派发时，消息队列认为处理已完成，导致并发创建 Worker。
        """
        for task in tasks:
            await self.get_or_create(task)
            # 短暂等待确保 Worker 完全初始化
            await asyncio.sleep(0.1)
        logger.info("预加载完成: {} 个 Worker", len(tasks))

    async def send(
        self,
        name: str,
        task: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        if name not in self._clients:
            raise KeyError(f"Worker '{name}' 不存在")

        initial_info = self._info[name]
        initial_info.status = WorkerStatus.RUNNING
        initial_info.last_used = time.time()
        try:
            for attempt in range(2):
                info = self._info[name]
                info.status = WorkerStatus.RUNNING
                info.last_used = time.time()
                client = self._clients[name]
                try:
                    result = await query_and_collect(
                        client,
                        task,
                        session_id=name,
                        on_progress=on_progress,
                        log_prefix=f"[worker:{name}]",
                    )
                    self._info[name].task_count += 1
                    return result
                except Exception as exc:
                    should_retry = attempt == 0 and is_retryable_sdk_error(exc)
                    if should_retry:
                        logger.warning(
                            "Worker 会话异常退出，正在重建后重试一次: name={} error={}",
                            name,
                            exc,
                        )
                        recreate_task = WorkerTask(
                            name=info.name,
                            task="resume worker session",
                            cwd=info.cwd,
                            model="" if info.model == "default" else info.model,
                            max_turns=info.max_turns,
                        )
                        await self._kill(name)
                        await self.get_or_create(recreate_task)
                        continue
                    raise
        finally:
            info = self._info.get(name)
            if info is not None:
                info.status = WorkerStatus.IDLE
                info.last_used = time.time()

    async def kill(self, name: str) -> None:
        await self._kill(name)

    async def interrupt(self, name: str) -> bool:
        """中断指定 Worker 当前任务。"""
        client = self._clients.get(name)
        if client is None:
            return False
        try:
            await client.interrupt()
            logger.info("中断 Worker: name={}", name)
            return True
        except Exception as e:
            logger.warning("中断 Worker 失败: name={} error={}", name, e)
            return False

    async def _kill(self, name: str) -> None:
        client = self._clients.pop(name, None)
        self._info.pop(name, None)
        self._stderr_captures.pop(name, None)
        if client:
            try:
                await client.disconnect()
                logger.info("销毁 Worker: name={}", name)
            except BaseException as e:
                logger.warning("销毁 Worker 出错: name={} error={}", name, e)

    async def _evict_if_needed(self, incoming_name: str) -> None:
        if incoming_name in self._clients or self._max_pooled_workers <= 0:
            return
        if len(self._clients) < self._max_pooled_workers:
            return

        idle_candidates = [
            info
            for info in self._info.values()
            if info.status == WorkerStatus.IDLE and info.name != incoming_name
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
        await self._kill(victim.name)

    def list_workers(self) -> list[WorkerInfo]:
        return list(self._info.values())

    def format_status(self) -> str:
        if not self._info:
            return ""
        lines = ["[系统信息] 当前活跃 Workers:"]
        for info in self._info.values():
            elapsed = int(time.time() - info.last_used)
            if info.status == WorkerStatus.RUNNING:
                status = "执行中"
            else:
                status = f"空闲 {elapsed}s"
            lines.append(
                f"- {info.name} ({status}): cwd={info.cwd}, 已执行 {info.task_count} 次任务"
            )
        lines.append("如需向已有 Worker 追加任务，使用相同 name 即可。")
        return "\n".join(lines)

    def has_worker(self, name: str) -> bool:
        return name in self._clients

    async def _create_client(self, task: WorkerTask) -> ClaudeSDKClient:
        """创建 ClaudeSDKClient。"""
        # 清除 CLAUDECODE 环境变量，避免 CLI 的嵌套会话检测阻止启动
        import os

        if "CLAUDECODE" in os.environ:
            del os.environ["CLAUDECODE"]

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

        stderr_capture = build_stderr_capture(f"[sdk:worker:{task.name}]")
        kwargs["stderr"] = stderr_capture.callback
        options = ClaudeAgentOptions(**kwargs)
        client = ClaudeSDKClient(options)
        await client.connect()
        self._stderr_captures[task.name] = stderr_capture
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
            await self._kill(name)


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
