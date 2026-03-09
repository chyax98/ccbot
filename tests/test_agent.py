"""Tests for CCBotAgent (Claude Agent SDK integration)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.agent import CCBotAgent
from ccbot.config import AgentConfig
from ccbot.workspace import WorkspaceManager


@pytest.fixture
def ws(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "workspace")


@pytest.fixture
def agent(ws: WorkspaceManager) -> CCBotAgent:
    return CCBotAgent(AgentConfig(), ws)


# ---- _make_options 配置项 (通过 AgentPool 测试) ----


def test_make_options_uses_model(ws: WorkspaceManager) -> None:
    """AgentPool 应该正确传递 model 配置。"""
    cfg = AgentConfig(model="claude-opus-4-6")
    agent = CCBotAgent(cfg, ws)
    # 通过内部 pool 验证配置
    assert agent._pool._config.model == "claude-opus-4-6"


def test_make_options_no_model_is_none(ws: WorkspaceManager) -> None:
    """未设置 model 时应该为 None 或空字符串。"""
    agent = CCBotAgent(AgentConfig(), ws)
    assert not agent._pool._config.model


def test_make_options_system_prompt_override(ws: WorkspaceManager) -> None:
    """直接指定 system_prompt 时应该优先使用。"""
    cfg = AgentConfig(system_prompt="custom prompt", cwd="/tmp")
    agent = CCBotAgent(cfg)
    assert agent._pool._config.system_prompt == "custom prompt"
    assert agent._pool._config.cwd == "/tmp"


def test_make_options_extra_system_prompt_appended(ws: WorkspaceManager) -> None:
    """extra_system_prompt 应该被传递到 AgentPool。"""
    agent = CCBotAgent(AgentConfig(), ws, extra_system_prompt="## Extra")
    assert agent._pool._extra_system_prompt == "## Extra"


def test_make_options_workspace_optional() -> None:
    """system_prompt 直接指定时，workspace 可以为 None。"""
    cfg = AgentConfig(system_prompt="worker prompt", cwd="/tmp", max_turns=5)
    agent = CCBotAgent(cfg)
    assert agent._pool._config.system_prompt == "worker prompt"
    assert agent._pool._config.cwd == "/tmp"


def test_make_options_cwd_fallback_to_dot() -> None:
    """workspace=None 且 cwd 未设置时，cwd 默认为空字符串（由 AgentPool 处理）。"""
    cfg = AgentConfig(system_prompt="x")
    agent = CCBotAgent(cfg)
    assert agent._pool._config.cwd == ""


# ---- Slash commands (no SDK call needed) ----


@pytest.mark.asyncio
async def test_help_command_returns_text(agent: CCBotAgent) -> None:
    reply = await agent.ask("chat1", "/help")
    assert "/new" in reply
    assert "/stop" in reply


@pytest.mark.asyncio
async def test_new_command_closes_session(agent: CCBotAgent, ws: WorkspaceManager) -> None:
    """/new 命令应该关闭 session。"""
    # 注入一个 fake client
    mock_client = MagicMock()
    mock_client.disconnect = AsyncMock()
    agent._pool._clients["chat1"] = mock_client

    reply = await agent.ask("chat1", "/new")

    mock_client.disconnect.assert_awaited_once()
    assert "chat1" not in agent._pool._clients
    assert "new session" in reply.lower() or "🐈" in reply


@pytest.mark.asyncio
async def test_stop_command_calls_interrupt(agent: CCBotAgent) -> None:
    """/stop 命令应该中断当前任务。"""
    mock_client = MagicMock()
    mock_client.interrupt = AsyncMock()
    agent._pool._clients["chat1"] = mock_client

    reply = await agent.ask("chat1", "/stop")

    mock_client.interrupt.assert_awaited_once()
    assert "stop" in reply.lower() or "⏹" in reply


@pytest.mark.asyncio
async def test_stop_command_no_active_session(agent: CCBotAgent) -> None:
    """没有活跃 session 时 /stop 不应该崩溃。"""
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
async def test_ask_returns_assistant_text(agent: CCBotAgent) -> None:
    mock_client = _make_mock_client("Hi there!")

    with patch("claude_agent_sdk.ClaudeSDKClient", return_value=mock_client):
        reply = await agent.ask("chat1", "Hello")

    assert reply == "Hi there!"


@pytest.mark.asyncio
async def test_ask_reuses_session_for_same_chat_id(agent: CCBotAgent) -> None:
    mock_client = _make_mock_client("ok")

    with patch("claude_agent_sdk.ClaudeSDKClient", return_value=mock_client) as mock_cls:
        await agent.ask("chat1", "first")
        await agent.ask("chat1", "second")

    # ClaudeSDKClient constructed only once
    assert mock_cls.call_count == 1
    assert mock_client.query.await_count == 2


@pytest.mark.asyncio
async def test_ask_creates_separate_sessions_per_chat_id(agent: CCBotAgent) -> None:
    clients = [_make_mock_client("a"), _make_mock_client("b")]
    idx = 0

    def _factory(*args, **kwargs):
        nonlocal idx
        c = clients[idx]
        idx += 1
        return c

    with patch("claude_agent_sdk.ClaudeSDKClient", side_effect=_factory):
        await agent.ask("chat1", "msg")
        await agent.ask("chat2", "msg")

    assert "chat1" in agent._pool._clients
    assert "chat2" in agent._pool._clients
    assert agent._pool._clients["chat1"] is clients[0]
    assert agent._pool._clients["chat2"] is clients[1]


@pytest.mark.asyncio
async def test_ask_closes_session_on_error(agent: CCBotAgent) -> None:
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.query = AsyncMock(side_effect=RuntimeError("boom"))
    mock_client.disconnect = AsyncMock()

    with patch("claude_agent_sdk.ClaudeSDKClient", return_value=mock_client):
        reply = await agent.ask("chat1", "Hello")

    assert "boom" in reply or "错误" in reply
    assert "chat1" not in agent._pool._clients


@pytest.mark.asyncio
async def test_last_chat_id_updated(agent: CCBotAgent) -> None:
    mock_client = _make_mock_client("ok")

    with patch("claude_agent_sdk.ClaudeSDKClient", return_value=mock_client):
        await agent.ask("room42", "ping")

    assert agent.last_chat_id == "room42"


@pytest.mark.asyncio
async def test_on_progress_called_per_tool(agent: CCBotAgent) -> None:
    """每次 TaskProgressMessage 都触发 on_progress（per-tool 通知）。"""
    from claude_agent_sdk import AssistantMessage, TaskProgressMessage, TaskUsage, TextBlock

    progress_calls: list[str] = []

    async def on_progress(msg: str) -> None:
        progress_calls.append(msg)

    usage = TaskUsage(
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )

    def make_task(tool_name: str) -> TaskProgressMessage:
        return TaskProgressMessage(
            subtype="progress",
            data={},
            task_id="t1",
            description="running",
            usage=usage,
            uuid="u1",
            session_id="s1",
            last_tool_name=tool_name,
        )

    text_msg = AssistantMessage(content=[TextBlock(text="done")], model="claude-sonnet-4-6")

    client = MagicMock()
    client.connect = AsyncMock()
    client.query = AsyncMock()
    client.disconnect = AsyncMock()

    async def _receive():
        yield make_task("Bash")
        yield make_task("Read")  # different tool — also notified
        yield make_task("Bash")  # same tool again — also notified
        yield text_msg

    client.receive_response = _receive

    with patch("claude_agent_sdk.ClaudeSDKClient", return_value=client):
        reply = await agent.ask("chat1", "run something", on_progress=on_progress)

    assert reply == "done"
    assert len(progress_calls) == 3  # one notification per TaskProgressMessage
    assert "Bash" in progress_calls[0]
    assert "Read" in progress_calls[1]


@pytest.mark.asyncio
async def test_concurrent_requests_serialized_per_chat_id(agent: CCBotAgent) -> None:
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

    with patch("claude_agent_sdk.ClaudeSDKClient", side_effect=_factory):
        # Inject client1 into the session pre-emptively
        agent._pool._clients["chat1"] = client1

        t1 = asyncio.create_task(agent.ask("chat1", "slow"))
        await asyncio.sleep(0)  # let t1 acquire the lock

        # Now change receive_response to fast for the second call
        client1.receive_response = fast_receive
        t2 = asyncio.create_task(agent.ask("chat1", "fast"))

        await asyncio.gather(t1, t2)

    # first_done must appear before second_done (serialized by lock)
    assert order.index("first_done") < order.index("second_done")


def test_supervisor_new_clears_memory(ws: WorkspaceManager) -> None:
    from ccbot.memory import MemoryStore

    store = MemoryStore(ws.path)
    store.set_runtime_session("chat1", "sess-123")
    agent = CCBotAgent(AgentConfig(), ws, memory_store=store)

    asyncio.run(agent.ask_run("chat1", "/new"))

    assert not store.conversation_file("chat1").exists()


@pytest.mark.asyncio
async def test_supervisor_result_persists_runtime_session_and_turns(ws: WorkspaceManager) -> None:
    from ccbot.memory import MemoryStore
    from ccbot.runtime.sdk_utils import AgentRunResult

    store = MemoryStore(ws.path)
    agent = CCBotAgent(AgentConfig(), ws, memory_store=store)
    agent._pool.acquire = AsyncMock(return_value=MagicMock())
    agent._pool.release = AsyncMock()
    agent._pool.close = AsyncMock()

    with patch(
        "ccbot.agent.query_and_collect_result",
        AsyncMock(return_value=AgentRunResult("ok", runtime_session_id="sess-123")),
    ):
        result = await agent.ask_run("chat1", "hello")

    assert result.runtime_session_id == "sess-123"
    memory = store.load("chat1")
    assert memory.runtime_session_id == "sess-123"
    assert [turn.role for turn in memory.short_term] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_ask_process_error_includes_recent_stderr(agent: CCBotAgent) -> None:
    from claude_agent_sdk._errors import ProcessError

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.query = AsyncMock(
        side_effect=ProcessError(
            "Command failed with exit code 1",
            exit_code=1,
            stderr="Check stderr output for details",
        )
    )
    mock_client.disconnect = AsyncMock()

    with patch("claude_agent_sdk.ClaudeSDKClient", return_value=mock_client):
        agent._pool.get_recent_stderr = MagicMock(return_value="fatal: boom")
        reply = await agent.ask("chat1", "Hello")

    assert "exit code: 1" in reply
    assert "fatal: boom" in reply
    assert "chat1" not in agent._pool._clients


@pytest.mark.asyncio
async def test_ask_retries_once_after_process_exit(agent: CCBotAgent) -> None:
    from claude_agent_sdk import AssistantMessage, TextBlock
    from claude_agent_sdk._errors import ProcessError

    broken_client = MagicMock()
    broken_client.connect = AsyncMock()
    broken_client.query = AsyncMock(side_effect=ProcessError("boom", exit_code=1))
    broken_client.disconnect = AsyncMock()

    healthy_client = MagicMock()
    healthy_client.connect = AsyncMock()
    healthy_client.query = AsyncMock()
    healthy_client.disconnect = AsyncMock()

    async def _receive():
        yield AssistantMessage(content=[TextBlock(text="ok after retry")], model="m")

    healthy_client.receive_response = _receive

    with patch("claude_agent_sdk.ClaudeSDKClient", side_effect=[broken_client, healthy_client]):
        reply = await agent.ask("chat1", "Hello")

    assert reply == "ok after retry"
    broken_client.disconnect.assert_awaited_once()
