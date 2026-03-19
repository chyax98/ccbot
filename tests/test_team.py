"""Tests for AgentTeam supervisor-worker dispatch protocol (持久化 Worker 架构)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ccbot.agent import CCBotAgent
from ccbot.config import AgentConfig
from ccbot.runtime.sdk_utils import AgentRunResult
from ccbot.runtime.worker_pool import WorkerPool
from ccbot.team import AgentTeam
from ccbot.workspace import WorkspaceManager


@pytest.fixture
def ws(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "workspace")


@pytest.fixture
def team(ws: WorkspaceManager) -> AgentTeam:
    return AgentTeam(AgentConfig(), ws)


def _mock_agent(reply: str, structured_output=None) -> CCBotAgent:
    agent = MagicMock(spec=CCBotAgent)
    agent.ask = AsyncMock(return_value=reply)
    agent.ask_run = AsyncMock(return_value=AgentRunResult(reply, structured_output))
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

        async def fake_send(
            name: str,
            task: str,
            *,
            owner_id: str = "",
            on_progress=None,
        ) -> str:
            return worker_replies.get(name, "default result")

        pool.send = AsyncMock(side_effect=fake_send)
    else:
        pool.send = AsyncMock(return_value="worker output")

    pool.kill = AsyncMock()
    pool.interrupt = AsyncMock(return_value=False)
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
    team._supervisor.ask_run.assert_awaited_once()


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
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask_run = AsyncMock(
        side_effect=[AgentRunResult(dispatch_plan), AgentRunResult("综合结果：前后端已完成")]
    )
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    # WorkerPool mock
    pool = _mock_worker_pool({"frontend": "前端完成", "backend": "后端完成"})
    team._worker_pool = pool

    reply = await team.ask("chat1", "同时开发前后端登录")

    assert reply == "综合结果：前后端已完成"
    assert supervisor.ask_run.await_count == 2  # 1次分析 + 1次综合
    assert pool.send.await_count == 2  # 2个 worker
    assert pool.get_or_create.await_count == 2  # 2个 worker 被 get_or_create


@pytest.mark.asyncio
async def test_dispatch_synthesis_contains_worker_results(team: AgentTeam) -> None:
    dispatch_plan = '<dispatch>[{"name": "w1", "cwd": "/x", "task": "t1"}]</dispatch>'
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask_run = AsyncMock(
        side_effect=[AgentRunResult(dispatch_plan), AgentRunResult("综合完毕")]
    )
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    pool = _mock_worker_pool({"w1": "worker output"})
    team._worker_pool = pool

    await team.ask("chat1", "task")

    # 第二次调用 Supervisor 的 prompt 是综合 prompt
    synthesis_prompt = supervisor.ask_run.call_args_list[1].args[1]
    assert "[w1]" in synthesis_prompt
    assert "worker output" in synthesis_prompt


@pytest.mark.asyncio
async def test_dispatch_worker_failure_marked_in_synthesis(team: AgentTeam) -> None:
    dispatch_plan = '<dispatch>[{"name": "bad", "cwd": "/x", "task": "t"}]</dispatch>'
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask_run = AsyncMock(
        side_effect=[AgentRunResult(dispatch_plan), AgentRunResult("ok")]
    )
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    pool = _mock_worker_pool()
    pool.send = AsyncMock(side_effect=RuntimeError("boom"))
    pool.get_or_create = AsyncMock(return_value=MagicMock(spec=CCBotAgent))
    team._worker_pool = pool

    await team.ask("chat1", "task")

    synthesis_prompt = supervisor.ask_run.call_args_list[1].args[1]
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
    supervisor.ask_run = AsyncMock(
        side_effect=[AgentRunResult(dispatch_plan), AgentRunResult("done")]
    )
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    progress_msgs: list[str] = []

    async def on_progress(msg: str) -> None:
        progress_msgs.append(msg)

    # send 调用 on_progress
    async def fake_send(name, task, *, owner_id="", on_progress=None):
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
    pool.format_status = MagicMock(
        return_value="[系统信息] 当前活跃 Workers:\n- fe (空闲): cwd=/fe"
    )
    team._worker_pool = pool

    await team.ask("chat1", "你好")

    # Supervisor 收到的 prompt 应包含 worker 状态
    actual_prompt = team._supervisor.ask_run.call_args.args[1]
    assert "当前活跃 Workers" in actual_prompt
    assert "fe" in actual_prompt


@pytest.mark.asyncio
async def test_no_worker_status_when_pool_empty(team: AgentTeam) -> None:
    """没有活跃 Worker 时不注入 runtime_context（日期已在 system prompt 中）。"""
    team._supervisor = _mock_agent("直接回答")
    pool = _mock_worker_pool()
    pool.format_status = MagicMock(return_value="")
    team._worker_pool = pool

    await team.ask("chat1", "你好")

    actual_prompt = team._supervisor.ask_run.call_args.args[1]
    assert "<runtime_context>" not in actual_prompt
    assert "你好" in actual_prompt


# ---- Worker 复用 ----


@pytest.mark.asyncio
async def test_worker_reuse_via_same_name(team: AgentTeam) -> None:
    """相同 name 的 dispatch 应复用已有 Worker。"""
    dispatch1 = '<dispatch>[{"name": "fe", "cwd": "/fe", "task": "task1"}]</dispatch>'
    dispatch2 = '<dispatch>[{"name": "fe", "cwd": "/fe", "task": "task2"}]</dispatch>'

    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask_run = AsyncMock(
        side_effect=[
            AgentRunResult(dispatch1),
            AgentRunResult("综合1"),
            AgentRunResult(dispatch2),
            AgentRunResult("综合2"),
        ]
    )
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


@pytest.mark.asyncio
async def test_async_dispatch_task_is_tracked_and_stopped(team: AgentTeam) -> None:
    """异步派发任务应被跟踪，并在 stop() 时正确清理。"""
    dispatch_plan = '<dispatch>[{"name": "fe", "cwd": "/fe", "task": "t"}]</dispatch>'
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask_run = AsyncMock(return_value=AgentRunResult(dispatch_plan))
    supervisor.last_chat_id = None
    supervisor.stop = AsyncMock()
    team._supervisor = supervisor

    pool = _mock_worker_pool()
    pool.stop = AsyncMock()
    team._worker_pool = pool

    started = asyncio.Event()
    released = asyncio.Event()

    async def fake_run_workers_async(chat_id, prompt, dispatch, on_progress, on_worker_result):
        started.set()
        try:
            await asyncio.Future()
        finally:
            released.set()

    team._run_workers_async = fake_run_workers_async  # type: ignore[method-assign]

    async def on_worker_result(name: str, result: str) -> None:
        return None

    reply = await team.ask("chat1", "task", on_worker_result=on_worker_result)
    assert "已派发" in reply

    await started.wait()
    assert len(team._background_tasks) == 1

    await team.stop()

    await released.wait()
    assert len(team._background_tasks) == 0
    pool.stop.assert_awaited_once()
    supervisor.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_structured_supervisor_response_skips_text_dispatch_parsing(team: AgentTeam) -> None:
    """优先使用 structured_output，而不是依赖 <dispatch> 文本协议。"""
    structured = {
        "mode": "dispatch",
        "user_message": "我会并行安排前后端任务。",
        "tasks": [
            {"name": "frontend", "cwd": "/fe", "task": "写登录页"},
            {"name": "backend", "cwd": "/be", "task": "写登录 API"},
        ],
    }
    supervisor = _mock_agent("纯文本说明，不含 dispatch 标签", structured_output=structured)
    supervisor.ask_run = AsyncMock(
        side_effect=[
            AgentRunResult("ignored", structured),
            AgentRunResult("final", {"mode": "respond", "user_message": "综合完成"}),
        ]
    )
    team._supervisor = supervisor
    team._worker_pool = _mock_worker_pool({"frontend": "前端完成", "backend": "后端完成"})

    reply = await team.ask("chat1", "同时开发前后端登录")

    assert reply == "综合完成"
    assert team._worker_pool.send.await_count == 2


@pytest.mark.asyncio
async def test_structured_supervisor_response_returns_direct_reply(team: AgentTeam) -> None:
    structured = {"mode": "respond", "user_message": "直接结构化回复"}
    team._supervisor = _mock_agent("fallback text", structured_output=structured)
    team._worker_pool = _mock_worker_pool()

    reply = await team.ask("chat1", "你好")

    assert reply == "直接结构化回复"


@pytest.mark.asyncio
async def test_control_command_workers_returns_status(team: AgentTeam) -> None:
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask_run = AsyncMock(return_value=AgentRunResult("should not run"))
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    pool = _mock_worker_pool()
    pool.format_status = MagicMock(return_value="[系统信息] 当前活跃 Workers:\n- fe")
    team._worker_pool = pool

    reply = await team.ask("chat1", "/workers")

    assert "当前活跃 Workers" in reply
    pool.format_status.assert_called_once_with(owner_id="chat1")
    supervisor.ask_run.assert_not_called()


@pytest.mark.asyncio
async def test_control_command_help_returns_summary(team: AgentTeam) -> None:
    reply = await team.ask("chat1", "/help")

    assert "/new" in reply
    assert "/schedule list" in reply


@pytest.mark.asyncio
async def test_control_command_new_resets_supervisor(team: AgentTeam) -> None:
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.reset_conversation = AsyncMock()
    supervisor.ask_run = AsyncMock()
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    reply = await team.ask("chat1", "/new")

    assert reply == "已开始新的 Supervisor 会话。"
    supervisor.reset_conversation.assert_awaited_once_with("chat1")
    supervisor.ask_run.assert_not_called()


@pytest.mark.asyncio
async def test_control_command_stop_interrupts_supervisor(team: AgentTeam) -> None:
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.interrupt = AsyncMock(return_value=True)
    supervisor.ask_run = AsyncMock()
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    reply = await team.ask("chat1", "/stop")

    assert reply == "已中断当前任务（Supervisor）。"
    supervisor.interrupt.assert_awaited_once_with("chat1")
    supervisor.ask_run.assert_not_called()


@pytest.mark.asyncio
async def test_control_command_stop_handles_idle_supervisor(team: AgentTeam) -> None:
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.interrupt = AsyncMock(return_value=False)
    supervisor.ask_run = AsyncMock()
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    reply = await team.ask("chat1", "/stop")

    assert reply == "当前没有可中断的任务。"


@pytest.mark.asyncio
async def test_control_command_schedule_run_handles_active_job(team: AgentTeam) -> None:
    scheduler = MagicMock()
    scheduler.run_job_now = AsyncMock(return_value="already_running")
    team.set_scheduler(scheduler)

    reply = await team.ask("chat1", "/schedule run job-1")

    assert reply == "定时任务正在执行中: job-1"
    scheduler.run_job_now.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_stop_cancels_background_dispatch_and_interrupts_workers(team: AgentTeam) -> None:
    structured = {
        "mode": "dispatch",
        "user_message": "我会并行处理。",
        "tasks": [
            {"name": "frontend", "cwd": "/fe", "task": "写登录页"},
            {"name": "backend", "cwd": "/be", "task": "写登录 API"},
        ],
    }
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask_run = AsyncMock(return_value=AgentRunResult("ignored", structured))
    supervisor.interrupt = AsyncMock(return_value=False)
    supervisor.last_chat_id = None
    team._supervisor = supervisor

    pool = _mock_worker_pool()
    pool.interrupt = AsyncMock(return_value=True)
    team._worker_pool = pool

    started = asyncio.Event()
    released = asyncio.Event()

    async def fake_run_workers_async(chat_id, prompt, dispatch, on_progress, on_worker_result):
        started.set()
        try:
            await asyncio.Future()
        finally:
            released.set()

    team._run_workers_async = fake_run_workers_async  # type: ignore[method-assign]

    async def on_worker_result(name: str, result: str) -> None:
        return None

    reply = await team.ask("chat1", "task", on_worker_result=on_worker_result)
    assert reply == "我会并行处理。"

    await started.wait()
    stop_reply = await team.ask("chat1", "/stop")

    assert stop_reply == "已中断当前任务（1 个后台派发，2 个 Worker）。"
    assert pool.interrupt.await_count == 2
    await released.wait()


async def test_async_dispatch_emits_final_synthesis(team: AgentTeam) -> None:
    structured = {
        "mode": "dispatch",
        "user_message": "我会并行处理。",
        "tasks": [{"name": "frontend", "cwd": "/fe", "task": "写登录页"}],
    }
    supervisor = MagicMock(spec=CCBotAgent)
    supervisor.ask_run = AsyncMock(
        side_effect=[
            AgentRunResult("ignored", structured),
            AgentRunResult("综合完成", {"mode": "respond", "user_message": "综合完成"}),
        ]
    )
    supervisor.last_chat_id = None
    team._supervisor = supervisor
    team._worker_pool = _mock_worker_pool({"frontend": "前端完成"})

    worker_results: list[tuple[str, str]] = []

    async def on_worker_result(name: str, result: str) -> None:
        worker_results.append((name, result))

    reply = await team.ask("chat1", "同时开发前后端登录", on_worker_result=on_worker_result)
    assert reply == "我会并行处理。"

    for _ in range(20):
        if worker_results and worker_results[-1][0] == "🤖 综合":
            break
        await asyncio.sleep(0)

    assert worker_results[0] == ("frontend", "前端完成")
    assert worker_results[-1] == ("🤖 综合", "综合完成")
    assert supervisor.ask_run.await_count == 2


@pytest.mark.asyncio
async def test_control_command_worker_kill(team: AgentTeam) -> None:
    pool = _mock_worker_pool()
    pool.has_worker = MagicMock(return_value=True)
    pool.kill = AsyncMock()
    team._worker_pool = pool

    reply = await team.ask("chat1", "/worker kill fe")

    assert reply == "已销毁 Worker: fe"
    pool.has_worker.assert_called_once_with("fe", owner_id="chat1")
    pool.kill.assert_awaited_once_with("fe", owner_id="chat1")


@pytest.mark.asyncio
async def test_control_command_worker_stop(team: AgentTeam) -> None:
    pool = _mock_worker_pool()
    pool.has_worker = MagicMock(return_value=True)
    pool.interrupt = AsyncMock(return_value=True)
    team._worker_pool = pool

    reply = await team.ask("chat1", "/worker stop fe")

    assert reply == "已中断 Worker: fe"
    pool.has_worker.assert_called_once_with("fe", owner_id="chat1")
    pool.interrupt.assert_awaited_once_with("fe", owner_id="chat1")
