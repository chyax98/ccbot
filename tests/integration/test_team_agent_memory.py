"""集成测试：AgentTeam + CCBotAgent + MemoryStore 交互。

验证：
- Team 创建的 Supervisor 正确使用 MemoryStore
- 多轮对话的记忆持久化与恢复
- /new 命令清除 memory + runtime session
- 错误后 stale session_id 清除
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.agent import CCBotAgent
from ccbot.config import AgentConfig
from ccbot.memory import MemoryStore
from ccbot.runtime.profiles import RuntimeRole
from ccbot.runtime.sdk_utils import AgentRunResult
from ccbot.team import AgentTeam
from ccbot.workspace import WorkspaceManager

from .conftest import make_error_sdk_client, make_mock_sdk_client


class TestAgentMemoryIntegration:
    """CCBotAgent + MemoryStore 跨组件集成。"""

    @pytest.mark.asyncio
    async def test_multi_turn_memory_persistence(self, workspace: WorkspaceManager) -> None:
        """多轮对话应持久化到 MemoryStore 文件系统。"""
        store = MemoryStore(workspace.path)
        agent = CCBotAgent(
            AgentConfig(),
            workspace,
            role=RuntimeRole.SUPERVISOR,
            memory_store=store,
        )

        # 第一轮
        client1 = make_mock_sdk_client("你好！", session_id="sess-1")
        with patch("claude_agent_sdk.ClaudeSDKClient", return_value=client1):
            reply1 = await agent.ask("chat1", "你好")

        assert reply1 == "你好！"
        mem = store.load("chat1")
        assert mem.runtime_session_id == "sess-1"
        assert len(mem.short_term) == 2  # user + assistant
        assert mem.short_term[0].role == "user"
        assert mem.short_term[1].role == "assistant"

        # 第二轮复用 session
        client1.receive_response = make_mock_sdk_client(
            "很好！", session_id="sess-1"
        ).receive_response
        reply2 = await agent.ask("chat1", "今天天气怎样")

        assert reply2 == "很好！"
        mem2 = store.load("chat1")
        assert len(mem2.short_term) == 4  # 两轮各 2 条

    @pytest.mark.asyncio
    async def test_new_command_clears_memory_and_session(self, workspace: WorkspaceManager) -> None:
        """/new 命令应同时清除 MemoryStore 和 runtime session。"""
        store = MemoryStore(workspace.path)
        agent = CCBotAgent(
            AgentConfig(),
            workspace,
            role=RuntimeRole.SUPERVISOR,
            memory_store=store,
        )

        # 先建立 session 和记忆
        store.set_runtime_session("chat1", "sess-old")
        store.remember_turn("chat1", "test", "reply")

        # 注入 fake client 以供 /new 关闭
        mock_client = MagicMock()
        mock_client.disconnect = AsyncMock()
        agent._pool._clients["chat1"] = mock_client

        reply = await agent.ask("chat1", "/new")

        assert "🐈" in reply or "new" in reply.lower()
        assert not store.conversation_file("chat1").exists()
        assert "chat1" not in agent._pool._clients

    @pytest.mark.asyncio
    async def test_error_clears_stale_session(self, workspace: WorkspaceManager) -> None:
        """SDK 错误后应清除 stale runtime_session_id，防止下次永久失败。"""
        store = MemoryStore(workspace.path)
        store.set_runtime_session("chat1", "stale-session")

        agent = CCBotAgent(
            AgentConfig(),
            workspace,
            role=RuntimeRole.SUPERVISOR,
            memory_store=store,
        )

        # 两次都失败（非 retryable 错误）
        broken = make_error_sdk_client(RuntimeError("unknown"))
        with patch("claude_agent_sdk.ClaudeSDKClient", return_value=broken):
            reply = await agent.ask("chat1", "hello")

        # session_id 应被清除
        assert store.load("chat1").runtime_session_id == ""
        assert "错误" in reply or "error" in reply.lower()

    @pytest.mark.asyncio
    async def test_different_chat_ids_isolate_memory(self, workspace: WorkspaceManager) -> None:
        """不同 chat_id 的记忆应完全隔离。"""
        store = MemoryStore(workspace.path)
        agent = CCBotAgent(
            AgentConfig(),
            workspace,
            role=RuntimeRole.SUPERVISOR,
            memory_store=store,
        )

        clients = [
            make_mock_sdk_client("回复A", session_id="sess-a"),
            make_mock_sdk_client("回复B", session_id="sess-b"),
        ]
        idx = 0

        def _factory(*args, **kwargs):
            nonlocal idx
            c = clients[idx]
            idx += 1
            return c

        with patch("claude_agent_sdk.ClaudeSDKClient", side_effect=_factory):
            await agent.ask("chatA", "消息A")
            await agent.ask("chatB", "消息B")

        mem_a = store.load("chatA")
        mem_b = store.load("chatB")
        assert mem_a.runtime_session_id == "sess-a"
        assert mem_b.runtime_session_id == "sess-b"
        assert len(mem_a.short_term) == 2
        assert len(mem_b.short_term) == 2
        assert "消息A" in mem_a.short_term[0].content
        assert "消息B" in mem_b.short_term[0].content


class TestTeamMemoryIntegration:
    """AgentTeam 编排层 + MemoryStore 集成。"""

    @pytest.mark.asyncio
    async def test_team_supervisor_uses_real_memory_store(
        self, workspace: WorkspaceManager
    ) -> None:
        """AgentTeam 创建的 Supervisor 应使用真实 MemoryStore。"""
        team = AgentTeam(AgentConfig(), workspace)

        # 验证内部 memory_store 被正确创建
        assert team._memory_store is not None
        assert isinstance(team._memory_store, MemoryStore)

        # mock supervisor 的 ask_run 以避免 SDK 调用
        team._supervisor.ask_run = AsyncMock(
            return_value=AgentRunResult("直接回复"),
        )
        team._worker_pool = MagicMock()
        team._worker_pool.format_status = MagicMock(return_value="")
        team._worker_pool.start = AsyncMock()
        team._worker_pool.stop = AsyncMock()

        reply = await team.ask("chat1", "你好")
        assert reply == "直接回复"

    @pytest.mark.asyncio
    async def test_team_new_clears_memory_and_workers(self, workspace: WorkspaceManager) -> None:
        """Team /new 命令应清除 memory + supervisor session。"""
        team = AgentTeam(AgentConfig(), workspace)
        store = team._memory_store
        store.set_runtime_session("chat1", "old-sess")
        store.remember_turn("chat1", "old msg", "old reply")

        # 注入 mock supervisor client
        mock_client = MagicMock()
        mock_client.disconnect = AsyncMock()
        team._supervisor._pool._clients["chat1"] = mock_client

        reply = await team.ask("chat1", "/new")
        assert "新" in reply or "new" in reply.lower()
        assert not store.conversation_file("chat1").exists()

    @pytest.mark.asyncio
    async def test_team_memory_show_returns_stored_content(
        self, workspace: WorkspaceManager
    ) -> None:
        """/memory show 应返回实际存储的记忆内容。"""
        team = AgentTeam(AgentConfig(), workspace)
        store = team._memory_store
        store.remember_turn("chat1", "测试输入", "测试输出")

        reply = await team.ask("chat1", "/memory show")
        assert "测试输入" in reply
        assert "测试输出" in reply

    @pytest.mark.asyncio
    async def test_team_memory_clear_removes_all(self, workspace: WorkspaceManager) -> None:
        """/memory clear 应清空所有记忆并重置 session。"""
        team = AgentTeam(AgentConfig(), workspace)
        store = team._memory_store
        store.set_runtime_session("chat1", "sess-x")
        store.remember_turn("chat1", "msg", "reply")

        # 注入 mock supervisor client
        mock_client = MagicMock()
        mock_client.disconnect = AsyncMock()
        team._supervisor._pool._clients["chat1"] = mock_client

        reply = await team.ask("chat1", "/memory clear")
        assert "清空" in reply
        assert not store.conversation_file("chat1").exists()
