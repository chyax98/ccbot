"""Tests for AgentTeam worker 生命周期管理和并发控制（持久化 Worker 架构）。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ccbot.agent import CCBotAgent
from ccbot.config import AgentConfig
from ccbot.runtime.sdk_utils import AgentRunResult
from ccbot.runtime.worker_pool import WorkerPool
from ccbot.team import AgentTeam
from ccbot.workspace import WorkspaceManager


@pytest.fixture
def ws(tmp_path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "workspace")


def _mock_worker_pool(worker_replies: dict[str, str] | None = None) -> WorkerPool:
    """创建 mock WorkerPool。"""
    pool = MagicMock(spec=WorkerPool)
    pool.start = AsyncMock()
    pool.stop = AsyncMock()
    pool.format_status = MagicMock(return_value="")
    pool.get_or_create = AsyncMock(return_value=MagicMock(spec=CCBotAgent))
    pool.kill = AsyncMock()
    pool.list_workers = MagicMock(return_value=[])
    pool.has_worker = MagicMock(return_value=False)

    if worker_replies:
        async def fake_send(name: str, task: str, on_progress=None) -> str:
            return worker_replies.get(name, "default result")
        pool.send = AsyncMock(side_effect=fake_send)
    else:
        pool.send = AsyncMock(return_value="worker done")

    return pool


class TestWorkerLifecycle:
    """验证持久化 Worker 的生命周期管理。"""

    @pytest.mark.asyncio
    async def test_worker_created_via_pool(self, ws: WorkspaceManager) -> None:
        """Worker 通过 WorkerPool.get_or_create 创建。"""
        team = AgentTeam(AgentConfig(), ws)

        dispatch_plan = '<dispatch>[{"name": "w1", "cwd": "/x", "task": "do stuff"}]</dispatch>'
        supervisor = MagicMock(spec=CCBotAgent)
        supervisor.ask_run = AsyncMock(side_effect=[AgentRunResult(dispatch_plan), AgentRunResult("综合结果")])
        supervisor.last_chat_id = None
        team._supervisor = supervisor

        pool = _mock_worker_pool({"w1": "worker done"})
        team._worker_pool = pool

        await team.ask("chat1", "任务")

        pool.get_or_create.assert_awaited_once()
        pool.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_worker_not_destroyed_after_task(self, ws: WorkspaceManager) -> None:
        """持久化 Worker 在任务完成后不被销毁。"""
        team = AgentTeam(AgentConfig(), ws)

        dispatch_plan = '<dispatch>[{"name": "w1", "cwd": "/x", "task": "do stuff"}]</dispatch>'
        supervisor = MagicMock(spec=CCBotAgent)
        supervisor.ask_run = AsyncMock(side_effect=[AgentRunResult(dispatch_plan), AgentRunResult("综合结果")])
        supervisor.last_chat_id = None
        team._supervisor = supervisor

        pool = _mock_worker_pool({"w1": "worker done"})
        team._worker_pool = pool

        await team.ask("chat1", "任务")

        # Worker 不应被 kill（持久化架构）
        pool.kill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_worker_failure_does_not_kill(self, ws: WorkspaceManager) -> None:
        """Worker 失败后也保持存活（不自动销毁）。"""
        team = AgentTeam(AgentConfig(), ws)

        dispatch_plan = '<dispatch>[{"name": "w1", "cwd": "/x", "task": "fail"}]</dispatch>'
        supervisor = MagicMock(spec=CCBotAgent)
        supervisor.ask_run = AsyncMock(side_effect=[AgentRunResult(dispatch_plan), AgentRunResult("ok")])
        supervisor.last_chat_id = None
        team._supervisor = supervisor

        pool = _mock_worker_pool()
        pool.send = AsyncMock(side_effect=RuntimeError("crash"))
        team._worker_pool = pool

        await team.ask("chat1", "任务")

        # 失败后也不销毁 Worker
        pool.kill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_multiple_workers_all_via_pool(self, ws: WorkspaceManager) -> None:
        """多个 worker 全部通过 pool 管理。"""
        team = AgentTeam(AgentConfig(), ws)

        dispatch_plan = """<dispatch>[
            {"name": "ok", "cwd": "/a", "task": "succeed"},
            {"name": "fail", "cwd": "/b", "task": "fail"}
        ]</dispatch>"""
        supervisor = MagicMock(spec=CCBotAgent)
        supervisor.ask_run = AsyncMock(side_effect=[AgentRunResult(dispatch_plan), AgentRunResult("综合")])
        supervisor.last_chat_id = None
        team._supervisor = supervisor

        send_calls = []

        async def fake_send(name, task, on_progress=None):
            send_calls.append(name)
            if name == "fail":
                raise RuntimeError("boom")
            return "success"

        pool = _mock_worker_pool()
        pool.send = AsyncMock(side_effect=fake_send)
        team._worker_pool = pool

        await team.ask("chat1", "multi")

        assert pool.get_or_create.await_count == 2
        assert set(send_calls) == {"ok", "fail"}


class TestWorkerConcurrencyLimit:
    """验证 max_workers 并发控制。"""

    @pytest.mark.asyncio
    async def test_max_workers_limits_concurrency(self, ws: WorkspaceManager) -> None:
        """并发 worker 数不应超过 max_workers。"""
        config = AgentConfig(max_workers=2)
        team = AgentTeam(config, ws)

        dispatch_plan = """<dispatch>[
            {"name": "w1", "cwd": "/a", "task": "t1"},
            {"name": "w2", "cwd": "/b", "task": "t2"},
            {"name": "w3", "cwd": "/c", "task": "t3"}
        ]</dispatch>"""

        supervisor = MagicMock(spec=CCBotAgent)
        supervisor.ask_run = AsyncMock(side_effect=[AgentRunResult(dispatch_plan), AgentRunResult("done")])
        supervisor.last_chat_id = None
        team._supervisor = supervisor

        max_concurrent = [0]
        current_concurrent = [0]

        async def slow_send(name, task, on_progress=None):
            current_concurrent[0] += 1
            max_concurrent[0] = max(max_concurrent[0], current_concurrent[0])
            await asyncio.sleep(0.05)
            current_concurrent[0] -= 1
            return f"done: {task}"

        pool = _mock_worker_pool()
        pool.send = AsyncMock(side_effect=slow_send)
        team._worker_pool = pool

        await team.ask("chat1", "并行任务")

        assert max_concurrent[0] <= 2


class TestAgentConfigMaxWorkers:
    """验证 AgentConfig.max_workers 字段。"""

    def test_default_max_workers(self) -> None:
        config = AgentConfig()
        assert config.max_workers == 4

    def test_custom_max_workers(self) -> None:
        config = AgentConfig(max_workers=8)
        assert config.max_workers == 8

    def test_max_workers_min_bound(self) -> None:
        with pytest.raises(ValueError):
            AgentConfig(max_workers=0)

    def test_max_workers_max_bound(self) -> None:
        with pytest.raises(ValueError):
            AgentConfig(max_workers=17)
