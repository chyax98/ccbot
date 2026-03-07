"""Tests for NanobotAgent (Claude Agent SDK integration)."""

from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_on_progress_called_on_tool_use(agent: NanobotAgent) -> None:
    """首次 TaskProgressMessage 触发 on_progress 回调。"""
    from claude_agent_sdk import AssistantMessage, TextBlock, TaskProgressMessage, TaskUsage

    progress_calls: list[str] = []

    async def on_progress(msg: str) -> None:
        progress_calls.append(msg)

    usage = TaskUsage(input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    task_msg = TaskProgressMessage(
        subtype="progress",
        data={},
        task_id="t1",
        description="running",
        usage=usage,
        uuid="u1",
        session_id="s1",
        last_tool_name="Bash",
    )
    text_msg = AssistantMessage(
        content=[TextBlock(text="done")],
        model="claude-sonnet-4-6",
    )

    client = MagicMock()
    client.connect = AsyncMock()
    client.query = AsyncMock()
    client.disconnect = AsyncMock()

    async def _receive():
        yield task_msg
        yield task_msg  # second TaskProgressMessage should NOT trigger again
        yield text_msg

    client.receive_response = _receive

    with patch("nanobot.agent.ClaudeSDKClient", return_value=client):
        reply = await agent.ask("chat1", "run something", on_progress=on_progress)

    assert reply == "done"
    assert len(progress_calls) == 1  # only first tool call notified
    assert "Bash" in progress_calls[0]


@pytest.mark.asyncio
async def test_concurrent_requests_serialized_per_chat_id(agent: NanobotAgent) -> None:
    """同一 chat_id 的并发请求被 Lock 串行化。"""
    order: list[str] = []

    async def slow_receive():
        from claude_agent_sdk import AssistantMessage, TextBlock
        await asyncio.sleep(0.05)
        order.append("first_done")
        yield AssistantMessage(content=[TextBlock(text="first")], model="m")

    async def fast_receive():
        from claude_agent_sdk import AssistantMessage, TextBlock
        order.append("second_done")
        yield AssistantMessage(content=[TextBlock(text="second")], model="m")

    client1 = MagicMock()
    client1.connect = AsyncMock()
    client1.query = AsyncMock()
    client1.disconnect = AsyncMock()
    client1.receive_response = slow_receive

    client2 = MagicMock()
    client2.connect = AsyncMock()
    client2.query = AsyncMock()
    client2.disconnect = AsyncMock()
    client2.receive_response = fast_receive

    call_count = 0

    def _factory(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return client1  # always same client

    with patch("nanobot.agent.ClaudeSDKClient", side_effect=_factory):
        # Inject client1 into the session pre-emptively
        agent._sessions["chat1"] = client1

        t1 = asyncio.create_task(agent.ask("chat1", "slow"))
        await asyncio.sleep(0)  # let t1 acquire the lock

        # Now change receive_response to fast for the second call
        client1.receive_response = fast_receive
        t2 = asyncio.create_task(agent.ask("chat1", "fast"))

        await asyncio.gather(t1, t2)

    # first_done must appear before second_done (serialized by lock)
    assert order.index("first_done") < order.index("second_done")
