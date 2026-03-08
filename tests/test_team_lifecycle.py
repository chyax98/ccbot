"""Tests for AgentTeam worker 生命周期管理和并发控制。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.agent import CCBotAgent
from ccbot.config import AgentConfig
from ccbot.team import AgentTeam
from ccbot.workspace import WorkspaceManager


@pytest.fixture
def ws(tmp_path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "workspace")


class TestWorkerLifecycle:
    """验证 worker 的 start/stop 生命周期管理。"""

    @pytest.mark.asyncio
    async def test_worker_start_and_stop_called(self, ws: WorkspaceManager) -> None:
        """每个 worker 必须调用 start() 和 stop()。"""
        team = AgentTeam(AgentConfig(), ws)

        dispatch_plan = '<dispatch>[{"name": "w1", "cwd": "/x", "task": "do stuff"}]</dispatch>'
        supervisor = MagicMock(spec=CCBotAgent)
        supervisor.ask = AsyncMock(side_effect=[dispatch_plan, "综合结果"])
        supervisor.last_chat_id = None
        team._supervisor = supervisor

        worker_mock = MagicMock(spec=CCBotAgent)
        worker_mock.start = AsyncMock()
        worker_mock.stop = AsyncMock()
        worker_mock.ask = AsyncMock(return_value="worker done")

        with patch("ccbot.team.CCBotAgent", return_value=worker_mock):
            await team.ask("chat1", "任务")

        worker_mock.start.assert_awaited_once()
        worker_mock.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_worker_stop_called_on_failure(self, ws: WorkspaceManager) -> None:
        """即使 worker 失败，stop() 也必须被调用（finally 保证）。"""
        team = AgentTeam(AgentConfig(), ws)

        dispatch_plan = '<dispatch>[{"name": "w1", "cwd": "/x", "task": "fail"}]</dispatch>'
        supervisor = MagicMock(spec=CCBotAgent)
        supervisor.ask = AsyncMock(side_effect=[dispatch_plan, "ok"])
        supervisor.last_chat_id = None
        team._supervisor = supervisor

        worker_mock = MagicMock(spec=CCBotAgent)
        worker_mock.start = AsyncMock()
        worker_mock.stop = AsyncMock()
        worker_mock.ask = AsyncMock(side_effect=RuntimeError("crash"))

        with patch("ccbot.team.CCBotAgent", return_value=worker_mock):
            await team.ask("chat1", "任务")

        worker_mock.start.assert_awaited_once()
        worker_mock.stop.assert_awaited_once()  # 关键：失败时也要 stop

    @pytest.mark.asyncio
    async def test_multiple_workers_all_stopped(self, ws: WorkspaceManager) -> None:
        """多个 worker 全部都要 stop()，即使部分失败。"""
        team = AgentTeam(AgentConfig(), ws)

        dispatch_plan = """<dispatch>[
            {"name": "ok", "cwd": "/a", "task": "succeed"},
            {"name": "fail", "cwd": "/b", "task": "fail"}
        ]</dispatch>"""
        supervisor = MagicMock(spec=CCBotAgent)
        supervisor.ask = AsyncMock(side_effect=[dispatch_plan, "综合"])
        supervisor.last_chat_id = None
        team._supervisor = supervisor

        workers_created: list[MagicMock] = []

        def create_worker(*args, **kwargs):
            w = MagicMock(spec=CCBotAgent)
            w.start = AsyncMock()
            w.stop = AsyncMock()
            if len(workers_created) == 0:
                w.ask = AsyncMock(return_value="success")
            else:
                w.ask = AsyncMock(side_effect=RuntimeError("boom"))
            workers_created.append(w)
            return w

        with patch("ccbot.team.CCBotAgent", side_effect=create_worker):
            await team.ask("chat1", "multi")

        assert len(workers_created) == 2
        for w in workers_created:
            w.start.assert_awaited_once()
            w.stop.assert_awaited_once()


class TestWorkerConcurrencyLimit:
    """验证 max_workers 并发控制。"""

    @pytest.mark.asyncio
    async def test_max_workers_limits_concurrency(self, ws: WorkspaceManager) -> None:
        """并发 worker 数不应超过 max_workers。"""
        config = AgentConfig(max_workers=2)
        team = AgentTeam(config, ws)

        # 3 个 worker，但 max_workers=2，所以同时运行的不超过 2 个
        dispatch_plan = """<dispatch>[
            {"name": "w1", "cwd": "/a", "task": "t1"},
            {"name": "w2", "cwd": "/b", "task": "t2"},
            {"name": "w3", "cwd": "/c", "task": "t3"}
        ]</dispatch>"""

        supervisor = MagicMock(spec=CCBotAgent)
        supervisor.ask = AsyncMock(side_effect=[dispatch_plan, "done"])
        supervisor.last_chat_id = None
        team._supervisor = supervisor

        max_concurrent = [0]
        current_concurrent = [0]

        async def slow_ask(chat_id, task, on_progress=None):
            current_concurrent[0] += 1
            max_concurrent[0] = max(max_concurrent[0], current_concurrent[0])
            await asyncio.sleep(0.05)  # 模拟工作
            current_concurrent[0] -= 1
            return f"done: {task}"

        def create_worker(*args, **kwargs):
            w = MagicMock(spec=CCBotAgent)
            w.start = AsyncMock()
            w.stop = AsyncMock()
            w.ask = AsyncMock(side_effect=slow_ask)
            return w

        with patch("ccbot.team.CCBotAgent", side_effect=create_worker):
            await team.ask("chat1", "并行任务")

        # 并发数不应超过 max_workers
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
        """max_workers 最小值为 1。"""
        with pytest.raises(ValueError):
            AgentConfig(max_workers=0)

    def test_max_workers_max_bound(self) -> None:
        """max_workers 最大值为 16。"""
        with pytest.raises(ValueError):
            AgentConfig(max_workers=17)
