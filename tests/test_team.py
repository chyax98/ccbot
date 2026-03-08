"""Tests for AgentTeam supervisor-worker dispatch protocol."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.agent import CCBotAgent
from ccbot.config import AgentConfig
from ccbot.team import AgentTeam
from ccbot.workspace import WorkspaceManager


@pytest.fixture
def ws(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "workspace")


@pytest.fixture
def team(ws: WorkspaceManager) -> AgentTeam:
    return AgentTeam(AgentConfig(), ws)


def _mock_agent(reply: str) -> CCBotAgent:
    agent = MagicMock(spec=CCBotAgent)
    agent.ask = AsyncMock(return_value=reply)
    agent.last_chat_id = None
    return agent


# ---- 无 dispatch：Supervisor 直接处理 ----


@pytest.mark.asyncio
async def test_no_dispatch_returns_supervisor_reply(team: AgentTeam) -> None:
    team._supervisor = _mock_agent("直接回答")
    reply = await team.ask("chat1", "你好")
    assert reply == "直接回答"
    team._supervisor.ask.assert_awaited_once()


# ---- dispatch 解析与并行执行 ----


@pytest.mark.asyncio
async def test_dispatch_runs_workers_and_synthesizes(team: AgentTeam, ws: WorkspaceManager) -> None:
    dispatch_plan = """
好的，我会派发任务。
<dispatch>
[
  {"name": "frontend", "cwd": "/fe", "task": "写登录页"},
  {"name": "backend",  "cwd": "/be", "task": "写登录 API"}
]
</dispatch>
"""
    # Supervisor: 第一次返回 dispatch 计划，第二次返回综合回复
    supervisor_replies = [dispatch_plan, "综合结果：前后端已完成"]
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask = AsyncMock(side_effect=supervisor_replies)
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    worker_replies = {"frontend": "前端完成", "backend": "后端完成"}

    async def fake_worker_ask(chat_id: str, task: str, on_progress=None) -> str:
        name = chat_id.split(":")[-1]
        return worker_replies[name]

    worker_mock = MagicMock(spec=CCBotAgent)
    worker_mock.ask = AsyncMock(side_effect=fake_worker_ask)

    with patch("ccbot.team.CCBotAgent", return_value=worker_mock):
        reply = await team.ask("chat1", "同时开发前后端登录")

    assert reply == "综合结果：前后端已完成"
    assert supervisor.ask.await_count == 2  # 1次分析 + 1次综合
    assert worker_mock.ask.await_count == 2  # 2个 worker


@pytest.mark.asyncio
async def test_dispatch_synthesis_contains_worker_results(team: AgentTeam) -> None:
    dispatch_plan = '<dispatch>[{"name": "w1", "cwd": "/x", "task": "t1"}]</dispatch>'
    supervisor = MagicMock(spec=CCBotAgent)
    # 第一次返回 dispatch 计划，第二次返回综合结果
    supervisor.ask = AsyncMock(side_effect=[dispatch_plan, "综合完毕"])
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    worker_mock = MagicMock(spec=CCBotAgent)
    worker_mock.ask = AsyncMock(return_value="worker output")

    with patch("ccbot.team.CCBotAgent", return_value=worker_mock):
        await team.ask("chat1", "task")

    # 第二次调用 Supervisor 的 prompt 是综合 prompt
    synthesis_prompt = supervisor.ask.call_args_list[1].args[1]
    assert "[w1]" in synthesis_prompt
    assert "worker output" in synthesis_prompt


@pytest.mark.asyncio
async def test_dispatch_worker_failure_marked_in_synthesis(team: AgentTeam) -> None:
    dispatch_plan = '<dispatch>[{"name": "bad", "cwd": "/x", "task": "t"}]</dispatch>'
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask = AsyncMock(side_effect=[dispatch_plan, "ok"])
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    worker_mock = MagicMock(spec=CCBotAgent)
    worker_mock.ask = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("ccbot.team.CCBotAgent", return_value=worker_mock):
        await team.ask("chat1", "task")

    synthesis_prompt = supervisor.ask.call_args_list[1].args[1]
    assert "❌" in synthesis_prompt
    assert "boom" in synthesis_prompt


@pytest.mark.asyncio
async def test_dispatch_invalid_json_falls_back(team: AgentTeam) -> None:
    bad_dispatch = "<dispatch>not json</dispatch>"
    team._supervisor = _mock_agent(bad_dispatch)
    reply = await team.ask("chat1", "task")
    # 解析失败时原样返回 Supervisor 回复，不崩溃
    assert reply == bad_dispatch


# ---- on_progress 回调 ----


@pytest.mark.asyncio
async def test_on_progress_tagged_with_worker_name(team: AgentTeam) -> None:
    dispatch_plan = '<dispatch>[{"name": "fe", "cwd": "/fe", "task": "t"}]</dispatch>'
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask = AsyncMock(side_effect=[dispatch_plan, "done"])
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    progress_msgs: list[str] = []

    async def on_progress(msg: str) -> None:
        progress_msgs.append(msg)

    async def worker_ask(chat_id, task, on_progress=None):
        if on_progress:
            await on_progress("🔧 Bash")
        return "result"

    worker_mock = MagicMock(spec=CCBotAgent)
    worker_mock.ask = AsyncMock(side_effect=worker_ask)

    with patch("ccbot.team.CCBotAgent", return_value=worker_mock):
        await team.ask("chat1", "task", on_progress=on_progress)

    # worker progress 前缀 "[fe] "
    worker_progress = [m for m in progress_msgs if m.startswith("[fe]")]
    assert worker_progress
    assert "🔧 Bash" in worker_progress[0]


# ---- last_chat_id 委托 ----


def test_last_chat_id_delegates_to_supervisor(team: AgentTeam) -> None:
    team._supervisor = _mock_agent("x")
    team._supervisor.last_chat_id = "room42"
    assert team.last_chat_id == "room42"
