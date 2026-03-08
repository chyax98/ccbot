"""Tests for AgentTeam supervisor-worker dispatch protocol (持久化 Worker 架构)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.agent import CCBotAgent
from ccbot.config import AgentConfig
from ccbot.runtime.worker_pool import WorkerPool
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


def _mock_worker_pool(worker_replies: dict[str, str] | None = None) -> WorkerPool:
    """创建 mock WorkerPool，可配置每个 worker 的回复。"""
    pool = MagicMock(spec=WorkerPool)
    pool.start = AsyncMock()
    pool.stop = AsyncMock()
    pool.format_status = MagicMock(return_value="")

    # get_or_create 返回 mock worker
    mock_worker = MagicMock(spec=CCBotAgent)
    pool.get_or_create = AsyncMock(return_value=mock_worker)

    if worker_replies:
        async def fake_send(name: str, task: str, on_progress=None) -> str:
            return worker_replies.get(name, "default result")
        pool.send = AsyncMock(side_effect=fake_send)
    else:
        pool.send = AsyncMock(return_value="worker output")

    pool.kill = AsyncMock()
    pool.list_workers = MagicMock(return_value=[])
    pool.has_worker = MagicMock(return_value=False)
    return pool


# ---- 无 dispatch：Supervisor 直接处理 ----


@pytest.mark.asyncio
async def test_no_dispatch_returns_supervisor_reply(team: AgentTeam) -> None:
    team._supervisor = _mock_agent("直接回答")
    team._worker_pool = _mock_worker_pool()
    reply = await team.ask("chat1", "你好")
    assert reply == "直接回答"
    team._supervisor.ask.assert_awaited_once()


# ---- dispatch 解析与并行执行 ----


@pytest.mark.asyncio
async def test_dispatch_runs_workers_and_synthesizes(team: AgentTeam) -> None:
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

    # WorkerPool mock
    pool = _mock_worker_pool({"frontend": "前端完成", "backend": "后端完成"})
    team._worker_pool = pool

    reply = await team.ask("chat1", "同时开发前后端登录")

    assert reply == "综合结果：前后端已完成"
    assert supervisor.ask.await_count == 2  # 1次分析 + 1次综合
    assert pool.send.await_count == 2  # 2个 worker
    assert pool.get_or_create.await_count == 2  # 2个 worker 被 get_or_create


@pytest.mark.asyncio
async def test_dispatch_synthesis_contains_worker_results(team: AgentTeam) -> None:
    dispatch_plan = '<dispatch>[{"name": "w1", "cwd": "/x", "task": "t1"}]</dispatch>'
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask = AsyncMock(side_effect=[dispatch_plan, "综合完毕"])
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    pool = _mock_worker_pool({"w1": "worker output"})
    team._worker_pool = pool

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

    pool = _mock_worker_pool()
    pool.send = AsyncMock(side_effect=RuntimeError("boom"))
    pool.get_or_create = AsyncMock(return_value=MagicMock(spec=CCBotAgent))
    team._worker_pool = pool

    await team.ask("chat1", "task")

    synthesis_prompt = supervisor.ask.call_args_list[1].args[1]
    assert "❌" in synthesis_prompt
    assert "boom" in synthesis_prompt


@pytest.mark.asyncio
async def test_dispatch_invalid_json_falls_back(team: AgentTeam) -> None:
    bad_dispatch = "<dispatch>not json</dispatch>"
    team._supervisor = _mock_agent(bad_dispatch)
    team._worker_pool = _mock_worker_pool()
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

    # send 调用 on_progress
    async def fake_send(name, task, on_progress=None):
        if on_progress:
            await on_progress("🔧 Bash")
        return "result"

    pool = _mock_worker_pool()
    pool.send = AsyncMock(side_effect=fake_send)
    pool.get_or_create = AsyncMock(return_value=MagicMock(spec=CCBotAgent))
    team._worker_pool = pool

    await team.ask("chat1", "task", on_progress=on_progress)

    # worker progress 前缀 "[fe] "
    worker_progress = [m for m in progress_msgs if m.startswith("[fe]")]
    assert worker_progress
    assert "🔧 Bash" in worker_progress[0]


# ---- Worker 状态注入 ----


@pytest.mark.asyncio
async def test_worker_status_injected_into_supervisor_prompt(team: AgentTeam) -> None:
    """活跃 Worker 状态应被注入到 Supervisor 收到的 prompt 中。"""
    team._supervisor = _mock_agent("直接回答")
    pool = _mock_worker_pool()
    pool.format_status = MagicMock(return_value="[系统信息] 当前活跃 Workers:\n- fe (空闲 30s): cwd=/fe")
    team._worker_pool = pool

    await team.ask("chat1", "你好")

    # Supervisor 收到的 prompt 应包含 worker 状态
    actual_prompt = team._supervisor.ask.call_args.args[1]
    assert "当前活跃 Workers" in actual_prompt
    assert "fe" in actual_prompt


@pytest.mark.asyncio
async def test_no_worker_status_when_pool_empty(team: AgentTeam) -> None:
    """没有活跃 Worker 时不注入状态。"""
    team._supervisor = _mock_agent("直接回答")
    pool = _mock_worker_pool()
    pool.format_status = MagicMock(return_value="")
    team._worker_pool = pool

    await team.ask("chat1", "你好")

    actual_prompt = team._supervisor.ask.call_args.args[1]
    assert actual_prompt == "你好"


# ---- Worker 复用 ----


@pytest.mark.asyncio
async def test_worker_reuse_via_same_name(team: AgentTeam) -> None:
    """相同 name 的 dispatch 应复用已有 Worker。"""
    dispatch1 = '<dispatch>[{"name": "fe", "cwd": "/fe", "task": "task1"}]</dispatch>'
    dispatch2 = '<dispatch>[{"name": "fe", "cwd": "/fe", "task": "task2"}]</dispatch>'

    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask = AsyncMock(side_effect=[dispatch1, "综合1", dispatch2, "综合2"])
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    pool = _mock_worker_pool({"fe": "result"})
    team._worker_pool = pool

    await team.ask("chat1", "first task")
    await team.ask("chat1", "second task")

    # get_or_create 被调用两次（由 WorkerPool 内部决定是复用还是创建）
    assert pool.get_or_create.await_count == 2
    assert pool.send.await_count == 2


# ---- last_chat_id 委托 ----


def test_last_chat_id_delegates_to_supervisor(team: AgentTeam) -> None:
    team._supervisor = _mock_agent("x")
    team._supervisor.last_chat_id = "room42"
    assert team.last_chat_id == "room42"


# ---- worker_pool 属性 ----


def test_worker_pool_property(team: AgentTeam) -> None:
    assert isinstance(team.worker_pool, WorkerPool)
