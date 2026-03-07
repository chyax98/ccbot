"""CcbotAgent: per-chat-id ClaudeSDKClient sessions with workspace integration.

使用 AgentPool 作为底层 client 管理器，消除重复的生命周期管理代码。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TaskProgressMessage,
    TextBlock,
    ToolUseBlock,
)
from loguru import logger

from ccbot.config import AgentConfig
from ccbot.runtime import AgentPool
from ccbot.workspace import WorkspaceManager

_HELP_TEXT = """\
🐈 ccbot commands:
/new  — Start a new conversation (archives current context to memory)
/stop — Cancel the active task
/help — Show this help"""


class NanobotAgent:
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
        idle_timeout: int = 1800,
    ) -> None:
        self._config = config
        self._workspace = workspace
        self._extra_system_prompt = extra_system_prompt
        self._pool = AgentPool(
            config=config,
            workspace=workspace,
            extra_system_prompt=extra_system_prompt,
            idle_timeout=idle_timeout,
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
            client = self._pool._clients.get(chat_id)  # type: ignore
            if client:
                try:
                    await client.interrupt()
                except Exception:
                    pass
            return "⏹ Stopped."

        logger.info("[{}] ← {}", chat_id, prompt[:120])

        async with self._get_lock(chat_id):
            try:
                client = await self._pool.acquire(chat_id)
                await client.query(prompt)
                parts: list[str] = []
                tool_count = 0

                async for msg in client.receive_response():
                    # TaskProgressMessage 是 SystemMessage 子类，需先匹配
                    if isinstance(msg, TaskProgressMessage):
                        tool = msg.last_tool_name or "tool"
                        desc = (msg.description or "").strip()
                        logger.info("[{}] 🔧 {} | {}", chat_id, tool, desc[:120])
                        tool_count += 1
                        if on_progress:
                            await on_progress(f"🔧 {tool}")

                    elif isinstance(msg, ResultMessage):
                        cost = f"${msg.total_cost_usd:.4f}" if msg.total_cost_usd else "n/a"
                        duration = f"{msg.duration_ms / 1000:.1f}s"
                        logger.info(
                            "[{}] ✅ 完成 | {} 轮 | {} 工具 | {} | {}",
                            chat_id,
                            msg.num_turns,
                            tool_count,
                            cost,
                            duration,
                        )
                        if msg.is_error:
                            logger.warning("[{}] stop_reason={}", chat_id, msg.stop_reason)

                    elif isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock) and block.text:
                                parts.append(block.text)
                            elif isinstance(block, ToolUseBlock):
                                # 工具调用参数（debug 级别，生产环境不刷屏）
                                logger.debug(
                                    "[{}] ⚡ {} | {}",
                                    chat_id,
                                    block.name,
                                    str(block.input)[:300],
                                )

                    elif isinstance(msg, SystemMessage):
                        logger.debug("[{}] sys subtype={}", chat_id, msg.subtype)

                reply = "\n".join(parts) or "（无响应）"
                logger.info("[{}] → {} chars", chat_id, len(reply))

                # 释放 client（更新最后使用时间）
                await self._pool.release(chat_id)

                return reply

            except Exception as e:
                logger.error("[{}] Agent 出错: {}", chat_id, e)
                await self._close_session(chat_id)
                return f"抱歉，处理消息时出现错误: {e}"
