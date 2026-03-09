from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ccbot.config import AgentConfig
from ccbot.memory import MemoryStore
from ccbot.runtime.pool import AgentPool
from ccbot.runtime.profiles import RuntimeRole
from ccbot.workspace import WorkspaceManager


@pytest.mark.asyncio
async def test_create_client_uses_resume_session_for_supervisor(
    monkeypatch, tmp_path: Path
) -> None:
    ws = WorkspaceManager(tmp_path / "workspace")
    store = MemoryStore(ws.path)
    store.set_runtime_session("chat-1", "sess-123")

    options_seen = {}

    class FakeOptions:
        def __init__(self, **kwargs):
            options_seen.update(kwargs)

    class FakeClient:
        def __init__(self, options):
            self.options = options

        async def connect(self):
            return None

    fake_module = MagicMock()
    fake_module.ClaudeAgentOptions = FakeOptions
    fake_module.ClaudeSDKClient = FakeClient
    monkeypatch.setitem(__import__("sys").modules, "claude_agent_sdk", fake_module)

    pool = AgentPool(
        AgentConfig(supervisor_resume_enabled=True),
        ws,
        role=RuntimeRole.SUPERVISOR,
        memory_store=store,
    )
    await pool._create_client("chat-1")

    assert options_seen["resume"] == "sess-123"
    assert options_seen["continue_conversation"] is True
    assert "## 短期记忆（最近对话）" not in options_seen["system_prompt"].get("append", "")


@pytest.mark.asyncio
async def test_create_client_injects_memory_prompt(monkeypatch, tmp_path: Path) -> None:
    ws = WorkspaceManager(tmp_path / "workspace")
    store = MemoryStore(ws.path)
    store.remember_turn("chat-1", "你好", "您好")

    options_seen = {}

    class FakeOptions:
        def __init__(self, **kwargs):
            options_seen.update(kwargs)

    class FakeClient:
        def __init__(self, options):
            self.options = options

        async def connect(self):
            return None

    fake_module = MagicMock()
    fake_module.ClaudeAgentOptions = FakeOptions
    fake_module.ClaudeSDKClient = FakeClient
    monkeypatch.setitem(__import__("sys").modules, "claude_agent_sdk", fake_module)

    pool = AgentPool(
        AgentConfig(),
        ws,
        role=RuntimeRole.SUPERVISOR,
        memory_store=store,
    )
    await pool._create_client("chat-1")

    append = options_seen["system_prompt"]["append"]
    assert "ccbot Memory Context" in append
    assert "你好" in append
