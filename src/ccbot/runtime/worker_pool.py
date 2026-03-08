"""WorkerPool: 持久化 Worker 池，直接管理 ClaudeSDKClient。

Worker 创建后保持存活，可接收多次任务。按 name 缓存，支持 idle 自动清理。
跳过 CCBotAgent+AgentPool，直接操作 SDK。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from ccbot.config import AgentConfig
from ccbot.models import WorkerTask
from ccbot.runtime.sdk_utils import query_and_collect

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from claude_agent_sdk import ClaudeSDKClient

# 清理循环检查间隔（秒）
_CLEANUP_CHECK_INTERVAL = 120


class WorkerStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"


@dataclass
class WorkerInfo:
    """Worker 元数据。"""

    name: str
    cwd: str
    model: str
    status: WorkerStatus = WorkerStatus.IDLE
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    task_count: int = 0


class WorkerPool:
    """持久化 Worker 池，直接管理 ClaudeSDKClient。

    每个 Worker 是一个 ClaudeSDKClient 持久子进程，通过 name 索引。
    多次 send() 是多轮对话，Worker 保留完整上下文。

    生命周期：
        pool = WorkerPool(base_config)
        await pool.start()
        await pool.get_or_create(task)
        result = await pool.send(task.name, task.task)
        await pool.stop()
    """

    def __init__(self, base_config: AgentConfig, idle_timeout: int = 3600) -> None:
        self._base_config = base_config
        self._idle_timeout = idle_timeout
        self._clients: dict[str, ClaudeSDKClient] = {}
        self._info: dict[str, WorkerInfo] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """启动清理循环。"""
        if self._running:
            return
        self._running = True
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="worker-pool-cleanup"
        )
        logger.info("WorkerPool 已启动，idle_timeout={}s", self._idle_timeout)

    async def stop(self) -> None:
        """关闭所有 Worker。"""
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
        """按 name 获取已有 Worker，不存在则创建。"""
        if task.name in self._clients:
            logger.info("复用已有 Worker: name={}", task.name)
            return

        client = await self._create_client(task)
        self._clients[task.name] = client
        self._info[task.name] = WorkerInfo(
            name=task.name,
            cwd=str(task.cwd),
            model=task.model or self._base_config.model or "default",
        )
        logger.info(
            "创建新 Worker: name={} cwd={} model={}",
            task.name,
            task.cwd,
            task.model or "default",
        )

    async def send(
        self,
        name: str,
        task: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """向 Worker 发送任务（多轮对话）。"""
        if name not in self._clients:
            raise KeyError(f"Worker '{name}' 不存在")

        client = self._clients[name]
        info = self._info[name]
        info.status = WorkerStatus.RUNNING
        info.last_used = time.time()
        try:
            result = await query_and_collect(
                client,
                task,
                session_id=name,
                on_progress=on_progress,
                log_prefix=f"[worker:{name}]",
            )
            info.task_count += 1
            return result
        finally:
            info.status = WorkerStatus.IDLE
            info.last_used = time.time()

    async def kill(self, name: str) -> None:
        """销毁指定 Worker。"""
        await self._kill(name)

    async def _kill(self, name: str) -> None:
        """销毁指定 Worker（内部）。"""
        client = self._clients.pop(name, None)
        self._info.pop(name, None)
        if client:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=3.0)
                logger.info("销毁 Worker: name={}", name)
            except Exception as e:
                logger.warning("销毁 Worker 出错: name={} error={}", name, e)

    def list_workers(self) -> list[WorkerInfo]:
        """列出所有活跃 Worker。"""
        return list(self._info.values())

    def format_status(self) -> str:
        """格式化 Worker 状态，供注入 Supervisor prompt。"""
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
        """检查 Worker 是否存在。"""
        return name in self._clients

    # ---- 内部方法 ----

    async def _create_client(self, task: WorkerTask) -> ClaudeSDKClient:
        """创建 ClaudeSDKClient。"""
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        _setup_worker_workspace(task.cwd)

        system_prompt = _WORKER_PROMPT.format(cwd=task.cwd)
        cwd = str(task.cwd)

        kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
            "cwd": cwd,
            "permission_mode": "bypassPermissions",
        }

        model = task.model or self._base_config.model
        if model:
            kwargs["model"] = model
        if task.max_turns:
            kwargs["max_turns"] = task.max_turns
        if self._base_config.mcp_servers:
            kwargs["mcp_servers"] = self._base_config.mcp_servers
        if self._base_config.env:
            kwargs["settings"] = json.dumps({"env": self._base_config.env})
            kwargs["setting_sources"] = ["project", "local"]

        options = ClaudeAgentOptions(**kwargs)
        client = ClaudeSDKClient(options)
        await client.connect()
        return client

    async def _cleanup_loop(self) -> None:
        """后台任务：定期清理空闲 Worker。"""
        while self._running:
            try:
                await asyncio.sleep(_CLEANUP_CHECK_INTERVAL)
                await self._cleanup_idle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("WorkerPool 清理任务出错: {}", e)

    async def _cleanup_idle(self) -> None:
        """清理超时空闲 Worker。"""
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


# ---- Worker 配置 ----

_WORKER_PROMPT = """\
You are a focused AI coding assistant.
Working directory: {cwd}
Complete the assigned task thoroughly and autonomously.
"""

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
