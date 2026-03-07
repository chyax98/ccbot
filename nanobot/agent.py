"""NanobotAgent: per-chat-id ClaudeSDKClient sessions with workspace integration."""

from __future__ import annotations

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock
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
      /stop — interrupt current query
      /help — show available commands
    """

    def __init__(self, config: AgentConfig, workspace: WorkspaceManager) -> None:
        self._config = config
        self._workspace = workspace
        self._sessions: dict[str, ClaudeSDKClient] = {}
        self.last_chat_id: str | None = None

    def _make_options(self) -> ClaudeAgentOptions:
        kwargs: dict = {
            "system_prompt": self._workspace.build_system_prompt(),
            "cwd": str(self._workspace.path),
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
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def ask(self, chat_id: str, prompt: str) -> str:
        """处理消息，返回回复文本。"""
        self.last_chat_id = chat_id
        cmd = prompt.strip().lower()

        if cmd == "/help":
            return _HELP_TEXT

        if cmd == "/new":
            await self._close_session(chat_id)
            return "New session started. 🐈"

        if cmd == "/stop":
            client = self._sessions.get(chat_id)
            if client:
                try:
                    await client.interrupt()
                except Exception:
                    pass
            return "⏹ Stopped."

        logger.info("处理来自 {}: {}", chat_id, prompt[:80])
        try:
            client = await self._get_client(chat_id)
            await client.query(prompt)
            parts: list[str] = []
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)
            return "\n".join(parts) or "（无响应）"
        except Exception as e:
            logger.error("Agent 出错 chat_id={}: {}", chat_id, e)
            await self._close_session(chat_id)
            return f"抱歉，处理消息时出现错误: {e}"
