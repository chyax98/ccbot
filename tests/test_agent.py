"""Tests for NanobotAgent (Claude Agent SDK integration)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent import NanobotAgent
from nanobot.config import AgentConfig
from nanobot.workspace import WorkspaceManager


@pytest.fixture
def ws(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "workspace")


@pytest.fixture
def agent(ws: WorkspaceManager) -> NanobotAgent:
    return NanobotAgent(AgentConfig(), ws)


# ---- Slash commands (no SDK call needed) ----

@pytest.mark.asyncio
async def test_help_command_returns_text(agent: NanobotAgent) -> None:
    reply = await agent.ask("chat1", "/help")
    assert "/new" in reply
    assert "/stop" in reply


@pytest.mark.asyncio
async def test_new_command_closes_session(agent: NanobotAgent, ws: WorkspaceManager) -> None:
    # Inject a fake session
    mock_client = MagicMock()
    mock_client.disconnect = AsyncMock()
    agent._sessions["chat1"] = mock_client

    reply = await agent.ask("chat1", "/new")

    mock_client.disconnect.assert_awaited_once()
    assert "chat1" not in agent._sessions
    assert "new session" in reply.lower() or "🐈" in reply


@pytest.mark.asyncio
async def test_stop_command_calls_interrupt(agent: NanobotAgent) -> None:
    mock_client = MagicMock()
    mock_client.interrupt = AsyncMock()
    agent._sessions["chat1"] = mock_client

    reply = await agent.ask("chat1", "/stop")

    mock_client.interrupt.assert_awaited_once()
    assert "stop" in reply.lower() or "⏹" in reply


@pytest.mark.asyncio
async def test_stop_command_no_active_session(agent: NanobotAgent) -> None:
    # No crash when no session exists
    reply = await agent.ask("chat1", "/stop")
    assert reply  # some response returned


# ---- SDK interaction (mocked) ----

def _make_mock_client(text_reply: str = "Hello!"):
    """Build a mock ClaudeSDKClient that yields one AssistantMessage with text."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    msg = AssistantMessage(content=[TextBlock(text=text_reply)], model="claude-sonnet-4-6")

    client = MagicMock()
    client.connect = AsyncMock()
    client.query = AsyncMock()
    client.disconnect = AsyncMock()

    async def _receive():
        yield msg

    client.receive_response = _receive
    return client


@pytest.mark.asyncio
async def test_ask_returns_assistant_text(agent: NanobotAgent) -> None:
    mock_client = _make_mock_client("Hi there!")

    with patch("nanobot.agent.ClaudeSDKClient", return_value=mock_client):
        reply = await agent.ask("chat1", "Hello")

    assert reply == "Hi there!"


@pytest.mark.asyncio
async def test_ask_reuses_session_for_same_chat_id(agent: NanobotAgent) -> None:
    mock_client = _make_mock_client("ok")

    with patch("nanobot.agent.ClaudeSDKClient", return_value=mock_client) as MockCls:
        await agent.ask("chat1", "first")
        await agent.ask("chat1", "second")

    # ClaudeSDKClient constructed only once
    assert MockCls.call_count == 1
    assert mock_client.query.await_count == 2


@pytest.mark.asyncio
async def test_ask_creates_separate_sessions_per_chat_id(agent: NanobotAgent) -> None:
    clients = [_make_mock_client("a"), _make_mock_client("b")]
    idx = 0

    def _factory(*args, **kwargs):
        nonlocal idx
        c = clients[idx]
        idx += 1
        return c

    with patch("nanobot.agent.ClaudeSDKClient", side_effect=_factory):
        await agent.ask("chat1", "msg")
        await agent.ask("chat2", "msg")

    assert "chat1" in agent._sessions
    assert "chat2" in agent._sessions
    assert agent._sessions["chat1"] is clients[0]
    assert agent._sessions["chat2"] is clients[1]


@pytest.mark.asyncio
async def test_ask_closes_session_on_error(agent: NanobotAgent) -> None:
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.query = AsyncMock(side_effect=RuntimeError("boom"))
    mock_client.disconnect = AsyncMock()

    with patch("nanobot.agent.ClaudeSDKClient", return_value=mock_client):
        reply = await agent.ask("chat1", "Hello")

    assert "boom" in reply or "错误" in reply
    assert "chat1" not in agent._sessions


@pytest.mark.asyncio
async def test_last_chat_id_updated(agent: NanobotAgent) -> None:
    mock_client = _make_mock_client("ok")

    with patch("nanobot.agent.ClaudeSDKClient", return_value=mock_client):
        await agent.ask("room42", "ping")

    assert agent.last_chat_id == "room42"
