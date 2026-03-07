"""NanobotAgent: per-chat-id ClaudeSDKClient sessions with workspace integration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TaskProgressMessage, TextBlock
from loguru import logger

from nanobot.config import AgentConfig
from nanobot.workspace import WorkspaceManager

_HELP_TEXT = """\
🐈 nanobot commands:
/new  — Start a new conversation (archives current context to memory)
/stop — Cancel the active task
/help — Show this help"""


class NanobotAgent:
    """
    Multi-turn agent backed by Claude Agent SDK.

    Each chat_id gets an independent ClaudeSDKClient (persistent subprocess).
    System prompt is built from workspace: identity + MEMORY.md + skills + bootstrap files.

    Slash commands:
      /new  — disconnect client → new client picks up updated MEMORY.md
      /stop — interrupt current query (non-blocking, does not wait for lock)
      /help — show available commands
    """

    def __init__(self, config: AgentConfig, workspace: WorkspaceManager) -> None:
        self._config = config
        self._workspace = workspace
        self._sessions: dict[str, ClaudeSDKClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.last_chat_id: str | None = None

    def _get_lock(self, chat_id: str) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    def _make_options(self) -> ClaudeAgentOptions:
        kwargs: dict = {
            "system_prompt": self._workspace.build_system_prompt(),
            "cwd": str(self._workspace.path),
            # 无人值守 bot：不弹权限确认框，否则 subprocess 会无声挂起
            "permission_mode": "bypassPermissions",
        }
        if self._config.max_turns:
            kwargs["max_turns"] = self._config.max_turns
        if self._config.allowed_tools:
            kwargs["allowed_tools"] = self._config.allowed_tools
        if self._config.mcp_servers:
            kwargs["mcp_servers"] = self._config.mcp_servers
        return ClaudeAgentOptions(**kwargs)

    async def _get_client(self, chat_id: str) -> ClaudeSDKClient:
        if chat_id not in self._sessions:
            client = ClaudeSDKClient(self._make_options())
            await client.connect()
            self._sessions[chat_id] = client
            logger.info("新会话: chat_id={}", chat_id)
        return self._sessions[chat_id]

    async def _close_session(self, chat_id: str) -> None:
        client = self._sessions.pop(chat_id, None)
        self._locks.pop(chat_id, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def ask(
        self,
        chat_id: str,
        prompt: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """处理消息，返回回复文本。

        on_progress: 可选回调，首次检测到工具调用时触发（工具名），用于发送进度提示。
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
            client = self._sessions.get(chat_id)
            if client:
                try:
                    await client.interrupt()
                except Exception:
                    pass
            return "⏹ Stopped."

        logger.info("处理来自 {}: {}", chat_id, prompt[:80])

        async with self._get_lock(chat_id):
            try:
                client = await self._get_client(chat_id)
                await client.query(prompt)
                parts: list[str] = []
                progress_sent = False

                async for msg in client.receive_response():
                    if isinstance(msg, TaskProgressMessage):
                        # 首次工具调用时通知一次（避免刷屏）
                        if on_progress and not progress_sent:
                            tool = msg.last_tool_name or "tool"
                            await on_progress(f"🔧 {tool}…")
                            progress_sent = True
                    elif isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                parts.append(block.text)

                return "\n".join(parts) or "（无响应）"
            except Exception as e:
                logger.error("Agent 出错 chat_id={}: {}", chat_id, e)
                await self._close_session(chat_id)
                return f"抱歉，处理消息时出现错误: {e}"
