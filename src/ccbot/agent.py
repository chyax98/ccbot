"""CCBotAgent: per-chat-id ClaudeSDKClient sessions with workspace integration.

使用 AgentPool 作为底层 client 管理器，消除重复的生命周期管理代码。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from loguru import logger

from ccbot.config import AgentConfig
from ccbot.runtime import AgentPool
from ccbot.runtime.sdk_utils import query_and_collect
from ccbot.workspace import WorkspaceManager

_HELP_TEXT = """\
🐈 ccbot commands:
/new  — Start a new conversation (archives current context to memory)
/stop — Cancel the active task
/help — Show this help"""


class CCBotAgent:
    """
    Multi-turn agent backed by Claude Agent SDK.

    Each chat_id gets an independent ClaudeSDKClient (persistent subprocess).
    System prompt is built from workspace: identity + MEMORY.md + skills + bootstrap files.
    Pass system_prompt in AgentConfig to bypass workspace building (worker mode).

    底层使用 AgentPool 管理 client 生命周期，包括空闲自动释放。

    Slash commands:
      /new  — disconnect client → new client picks up updated MEMORY.md
      /stop — interrupt current query (non-blocking, does not wait for lock)
      /help — show available commands
    """

    def __init__(
        self,
        config: AgentConfig,
        workspace: WorkspaceManager | None = None,
        extra_system_prompt: str = "",
        idle_timeout: int | None = None,
    ) -> None:
        self._config = config
        self._workspace = workspace
        self._extra_system_prompt = extra_system_prompt
        self._idle_timeout = idle_timeout if idle_timeout is not None else config.idle_timeout
        self._pool = AgentPool(
            config=config,
            workspace=workspace,
            extra_system_prompt=extra_system_prompt,
            idle_timeout=self._idle_timeout,
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self.last_chat_id: str | None = None

    async def start(self) -> None:
        """启动 agent，启动底层的 AgentPool。"""
        await self._pool.start()

    async def stop(self) -> None:
        """停止 agent，关闭所有 client。"""
        await self._pool.stop()

    def _get_lock(self, chat_id: str) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def _close_session(self, chat_id: str) -> None:
        """关闭指定 chat_id 的会话。"""
        await self._pool.close(chat_id)
        self._locks.pop(chat_id, None)

    async def ask(
        self,
        chat_id: str,
        prompt: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """处理消息，返回回复文本。

        on_progress: 每次工具调用时触发，发送进度提示给用户。
        """
        self.last_chat_id = chat_id
        cmd = prompt.strip().lower()

        if cmd == "/help":
            return _HELP_TEXT

        if cmd == "/new":
            await self._close_session(chat_id)
            return "New session started. 🐈"

        # /stop 不进锁，直接中断当前正在运行的 query
        if cmd == "/stop":
            await self._pool.interrupt(chat_id)
            return "⏹ Stopped."

        logger.info("[{}] ← {}", chat_id, prompt[:120])

        async with self._get_lock(chat_id):
            try:
                client = await self._pool.acquire(chat_id)
                reply = await query_and_collect(
                    client,
                    prompt,
                    session_id=chat_id,
                    on_progress=on_progress,
                    log_prefix=f"[{chat_id}]",
                )
                logger.info("[{}] → {} chars", chat_id, len(reply))
                await self._pool.release(chat_id)
                return reply

            except Exception as e:
                logger.error("[{}] Agent 出错: {}", chat_id, e)
                await self._close_session(chat_id)
                return f"抱歉，处理消息时出现错误: {e}"
