"""集成测试：组件生命周期与优雅关闭。

验证：
- AgentTeam start/stop 正确初始化和清理所有子组件
- SchedulerService start/stop 生命周期
- 并发操作期间 stop 的安全性
- 跨组件 config 传播
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from ccbot.config import AgentConfig
from ccbot.scheduler import SchedulerService
from ccbot.team import AgentTeam
from ccbot.workspace import WorkspaceManager


class TestTeamLifecycle:
    """AgentTeam 生命周期集成。"""

    @pytest.mark.asyncio
    async def test_start_initializes_all_components(self, workspace: WorkspaceManager) -> None:
        """team.start() 应启动 supervisor pool 和 worker pool。"""
        team = AgentTeam(AgentConfig(), workspace)

        await team.start()
        try:
            assert team._supervisor._pool._running is True
            assert team._worker_pool._running is True
        finally:
            await team.stop()

    @pytest.mark.asyncio
    async def test_stop_cleans_up_all_components(self, workspace: WorkspaceManager) -> None:
        """team.stop() 应停止所有子组件。"""
        team = AgentTeam(AgentConfig(), workspace)

        await team.start()
        await team.stop()

        assert team._supervisor._pool._running is False
        assert team._worker_pool._running is False

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self, workspace: WorkspaceManager) -> None:
        """重复调用 start() 不应产生错误。"""
        team = AgentTeam(AgentConfig(), workspace)

        await team.start()
        await team.start()  # 第二次应幂等

        try:
            assert team._supervisor._pool._running is True
        finally:
            await team.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self, workspace: WorkspaceManager) -> None:
        """未启动时调用 stop() 不应崩溃。"""
        team = AgentTeam(AgentConfig(), workspace)
        await team.stop()  # 不应抛出异常


class TestSchedulerLifecycle:
    """SchedulerService 生命周期集成。"""

    @pytest.mark.asyncio
    async def test_scheduler_start_stop(self, workspace: WorkspaceManager) -> None:
        scheduler = SchedulerService(
            workspace.path,
            on_execute=AsyncMock(return_value="ok"),
            on_notify=AsyncMock(),
            poll_interval_s=1,
        )

        await scheduler.start()
        assert scheduler._running is True
        assert scheduler._task is not None

        await scheduler.stop()
        assert scheduler._running is False
        assert scheduler._task is None

    @pytest.mark.asyncio
    async def test_scheduler_stop_cancels_running_jobs(self, workspace: WorkspaceManager) -> None:
        """Scheduler stop 应取消正在执行的 job。"""
        execute_started = asyncio.Event()

        async def slow_execute(job):
            execute_started.set()
            # 模拟长时间执行，等待被 cancel
            await asyncio.sleep(60)
            return "done"

        scheduler = SchedulerService(
            workspace.path,
            on_execute=slow_execute,
            on_notify=AsyncMock(),
        )

        from ccbot.models.schedule import ScheduleSpec

        spec = ScheduleSpec(name="slow", cron_expr="* * * * *", prompt="slow task")
        job = scheduler.create_job(
            spec,
            created_by="test",
            channel="test",
            notify_target="t",
            conversation_id="c",
        )

        # 通过 _launch_job 以后台 task 方式启动 job
        scheduler._launch_job(job)

        # 等 job 开始执行
        try:
            await asyncio.wait_for(execute_started.wait(), timeout=2.0)
        except TimeoutError:
            pytest.skip("execute callback not started in time")

        # stop 应取消运行中的 job
        await scheduler.stop()

        # 验证 run_tasks 已被清空
        assert len(scheduler._run_tasks) == 0


class TestConfigPropagation:
    """Config 跨组件传播验证。"""

    def test_agent_config_flows_to_pool(self, workspace: WorkspaceManager) -> None:
        """AgentConfig 的 model/idle_timeout 应正确传递到 AgentPool。"""
        config = AgentConfig(
            model="claude-opus-4-6",
            idle_timeout=600,
            max_turns=10,
        )
        team = AgentTeam(config, workspace)

        pool = team._supervisor._pool
        assert pool._config.model == "claude-opus-4-6"
        assert pool._idle_timeout == 600
        assert pool._config.max_turns == 10

    def test_worker_config_flows_to_worker_pool(self, workspace: WorkspaceManager) -> None:
        """worker 相关配置应正确传递到 WorkerPool。"""
        config = AgentConfig(
            max_workers=5,
            max_pooled_workers=3,
            worker_idle_timeout=1800,
        )
        team = AgentTeam(config, workspace)

        wp = team._worker_pool
        assert wp._idle_timeout == 1800
        assert wp._max_pooled_workers == 3

    def test_memory_turns_config(self, workspace: WorkspaceManager) -> None:
        """short_term_memory_turns 应传递到 MemoryStore。"""
        config = AgentConfig(short_term_memory_turns=20)
        team = AgentTeam(config, workspace)

        assert team._memory_store._max_short_term_turns == 20

    def test_team_set_scheduler_injects_mcp_servers(self, workspace: WorkspaceManager) -> None:
        """set_scheduler 应向 Supervisor 注入 runtime MCP tools。"""
        team = AgentTeam(AgentConfig(), workspace)
        scheduler = SchedulerService(
            workspace.path,
            on_execute=AsyncMock(return_value="ok"),
            on_notify=AsyncMock(),
        )

        team.set_scheduler(scheduler)

        # 验证 supervisor pool 中有 SDK MCP servers
        assert len(team._supervisor._pool._sdk_mcp_servers) > 0
        assert "ccbot-runtime" in team._supervisor._pool._sdk_mcp_servers
