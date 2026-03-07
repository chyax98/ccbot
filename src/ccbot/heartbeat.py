"""Heartbeat service: periodic HEARTBEAT.md check and task execution."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger


def _has_active_tasks(content: str) -> bool:
    """Return True if HEARTBEAT.md has non-empty active tasks section."""
    in_active = False
    for line in content.splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,3}\s*active\s*tasks?", stripped, re.IGNORECASE):
            in_active = True
            continue
        if re.match(r"^#{1,3}", stripped) and in_active:
            in_active = False
            continue
        if in_active and stripped and not stripped.startswith("<!--"):
            return True
    return False


class HeartbeatService:
    """
    Periodically reads HEARTBEAT.md and triggers the agent when active tasks exist.

    on_execute(prompt) → reply text (runs through agent)
    on_notify(reply)   → sends reply to last active chat
    """

    def __init__(
        self,
        heartbeat_file: Path,
        on_execute: Callable[[str], Awaitable[str]],
        on_notify: Callable[[str], Awaitable[None]],
        interval_s: int = 1800,
    ) -> None:
        self._file = heartbeat_file
        self._on_execute = on_execute
        self._on_notify = on_notify
        self._interval = interval_s
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Heartbeat 已启动（每 {}s）", self._interval)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat 出错: {}", e)

    async def _tick(self) -> None:
        if not self._file.exists():
            return
        content = self._file.read_text("utf-8")
        if not _has_active_tasks(content):
            logger.debug("Heartbeat: 无活跃任务，跳过")
            return

        logger.info("Heartbeat: 发现任务，开始执行...")
        try:
            prompt = (
                "[Heartbeat Check]\n\n"
                "请检查以下 HEARTBEAT.md 内容，执行其中的活跃任务，完成后汇报结果。\n\n"
                f"{content}"
            )
            reply = await self._on_execute(prompt)
            if reply:
                await self._on_notify(reply)
        except Exception as e:
            logger.error("Heartbeat 执行失败: {}", e)
