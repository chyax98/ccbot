"""集成测试：SchedulerService + AgentTeam 端到端流程。

验证：
- Scheduler 创建 job → 到期触发 → execute callback → notify callback
- Team.set_scheduler 注入 runtime tools
- /schedule 控制命令与真实 Scheduler 交互
- Job 持久化到文件系统
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from ccbot.config import AgentConfig
from ccbot.models.schedule import ScheduleSpec
from ccbot.scheduler import SchedulerService
from ccbot.team import AgentTeam
from ccbot.workspace import WorkspaceManager


class TestSchedulerTeamIntegration:
    """Scheduler + AgentTeam 集成。"""

    @pytest.mark.asyncio
    async def test_scheduler_execute_callback_invokes_team(
        self, workspace: WorkspaceManager
    ) -> None:
        """Scheduler 触发 job 时应通过 execute callback 调用 team.ask。"""
        execute_calls: list[str] = []
        notify_calls: list[tuple[str, str]] = []

        async def on_execute(job):
            execute_calls.append(job.prompt)
            return "任务执行完成"

        async def on_notify(job, content):
            notify_calls.append((job.name, content))

        scheduler = SchedulerService(
            workspace.path,
            on_execute=on_execute,
            on_notify=on_notify,
            poll_interval_s=1,
        )

        # 创建一个立即到期的 job
        spec = ScheduleSpec(
            name="测试任务",
            cron_expr="* * * * *",
            prompt="执行测试",
        )
        job = scheduler.create_job(
            spec,
            created_by="test",
            channel="test",
            notify_target="test-target",
            conversation_id="test-conv",
        )

        # 手动触发 _tick
        # 先将 next_run_at 设为过去
        job.next_run_at = "2020-01-01T00:00:00+00:00"
        scheduler._save_jobs()

        await scheduler._tick()

        # 等待异步 job 完成
        await asyncio.sleep(0.5)

        assert len(execute_calls) == 1
        assert execute_calls[0] == "执行测试"
        # notify 被调用（开始 + 完成）
        assert len(notify_calls) >= 1
        assert any("完成" in content for _, content in notify_calls)

    @pytest.mark.asyncio
    async def test_scheduler_job_persistence(self, workspace: WorkspaceManager) -> None:
        """Job 应持久化到文件系统，重新加载后保持一致。"""
        scheduler1 = SchedulerService(
            workspace.path,
            on_execute=AsyncMock(return_value="ok"),
            on_notify=AsyncMock(),
        )

        spec = ScheduleSpec(
            name="持久化测试",
            cron_expr="0 9 * * *",
            prompt="每日检查",
            timezone="Asia/Shanghai",
        )
        job = scheduler1.create_job(
            spec,
            created_by="test",
            channel="feishu",
            notify_target="target",
            conversation_id="conv",
        )

        # 创建新的 scheduler 实例，从文件加载
        scheduler2 = SchedulerService(
            workspace.path,
            on_execute=AsyncMock(return_value="ok"),
            on_notify=AsyncMock(),
        )

        loaded = scheduler2.get_job(job.job_id)
        assert loaded is not None
        assert loaded.name == "持久化测试"
        assert loaded.cron_expr == "0 9 * * *"
        assert loaded.prompt == "每日检查"
        assert loaded.timezone == "Asia/Shanghai"

    @pytest.mark.asyncio
    async def test_scheduler_run_job_now(self, workspace: WorkspaceManager) -> None:
        """run_job_now 应触发 job 异步执行并更新状态。"""
        execute_results: list[str] = []

        async def on_execute(job):
            execute_results.append(job.job_id)
            return "手动执行完成"

        scheduler = SchedulerService(
            workspace.path,
            on_execute=on_execute,
            on_notify=AsyncMock(),
        )

        spec = ScheduleSpec(name="手动任务", cron_expr="0 0 1 1 *", prompt="run")
        job = scheduler.create_job(
            spec,
            created_by="test",
            channel="test",
            notify_target="t",
            conversation_id="c",
        )

        result = await scheduler.run_job_now(job.job_id)
        assert result == "started"

        # run_job_now 异步触发，等待后台任务完成
        if scheduler._run_tasks:
            await asyncio.gather(*scheduler._run_tasks, return_exceptions=True)

        assert job.job_id in execute_results

        # job 状态应更新
        updated = scheduler.get_job(job.job_id)
        assert updated is not None
        assert updated.last_status == "succeeded"
        assert updated.last_result_summary == "手动执行完成"


class TestTeamSchedulerControlCommands:
    """Team /schedule 命令与真实 Scheduler 集成。"""

    @pytest.fixture
    def team_with_scheduler(
        self, workspace: WorkspaceManager
    ) -> tuple[AgentTeam, SchedulerService]:
        team = AgentTeam(AgentConfig(), workspace)
        scheduler = SchedulerService(
            workspace.path,
            on_execute=AsyncMock(return_value="ok"),
            on_notify=AsyncMock(),
        )
        team.set_scheduler(scheduler)
        return team, scheduler

    @pytest.mark.asyncio
    async def test_schedule_list_empty(
        self, team_with_scheduler: tuple[AgentTeam, SchedulerService]
    ) -> None:
        team, _scheduler = team_with_scheduler
        reply = await team.ask("chat1", "/schedule list")
        assert "没有" in reply or "定时任务" in reply

    @pytest.mark.asyncio
    async def test_schedule_list_with_jobs(
        self, team_with_scheduler: tuple[AgentTeam, SchedulerService]
    ) -> None:
        team, scheduler = team_with_scheduler
        spec = ScheduleSpec(name="日报", cron_expr="0 9 * * *", prompt="生成日报")
        scheduler.create_job(
            spec,
            created_by="test",
            channel="test",
            notify_target="t",
            conversation_id="c",
        )

        reply = await team.ask("chat1", "/schedule list")
        assert "日报" in reply

    @pytest.mark.asyncio
    async def test_schedule_pause_and_resume(
        self, team_with_scheduler: tuple[AgentTeam, SchedulerService]
    ) -> None:
        team, scheduler = team_with_scheduler
        spec = ScheduleSpec(name="可暂停任务", cron_expr="0 9 * * *", prompt="test")
        job = scheduler.create_job(
            spec,
            created_by="test",
            channel="test",
            notify_target="t",
            conversation_id="c",
        )

        # 暂停
        reply = await team.ask("chat1", f"/schedule pause {job.job_id}")
        assert "暂停" in reply
        assert not scheduler.get_job(job.job_id).enabled

        # 恢复
        reply = await team.ask("chat1", f"/schedule resume {job.job_id}")
        assert "恢复" in reply
        assert scheduler.get_job(job.job_id).enabled

    @pytest.mark.asyncio
    async def test_schedule_delete(
        self, team_with_scheduler: tuple[AgentTeam, SchedulerService]
    ) -> None:
        team, scheduler = team_with_scheduler
        spec = ScheduleSpec(name="待删除", cron_expr="0 9 * * *", prompt="test")
        job = scheduler.create_job(
            spec,
            created_by="test",
            channel="test",
            notify_target="t",
            conversation_id="c",
        )

        reply = await team.ask("chat1", f"/schedule delete {job.job_id}")
        assert "删除" in reply
        assert scheduler.get_job(job.job_id) is None

    @pytest.mark.asyncio
    async def test_schedule_run(
        self, team_with_scheduler: tuple[AgentTeam, SchedulerService]
    ) -> None:
        team, scheduler = team_with_scheduler
        spec = ScheduleSpec(name="立即执行", cron_expr="0 0 1 1 *", prompt="go")
        job = scheduler.create_job(
            spec,
            created_by="test",
            channel="test",
            notify_target="t",
            conversation_id="c",
        )

        reply = await team.ask("chat1", f"/schedule run {job.job_id}")
        assert "触发" in reply

    @pytest.mark.asyncio
    async def test_schedule_commands_without_scheduler(self, workspace: WorkspaceManager) -> None:
        """未启用 Scheduler 时，/schedule 命令应返回提示。"""
        team = AgentTeam(AgentConfig(), workspace)
        reply = await team.ask("chat1", "/schedule list")
        assert "未启用" in reply
