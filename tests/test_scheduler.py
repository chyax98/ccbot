from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ccbot.models.schedule import ScheduleSpec
from ccbot.scheduler import SchedulerService


@pytest.mark.asyncio
async def test_scheduler_create_and_list_jobs(tmp_path: Path) -> None:
    async def on_execute(job):
        return f"done:{job.job_id}"

    seen = []

    async def on_notify(job, content: str) -> None:
        seen.append((job.job_id, content))

    scheduler = SchedulerService(tmp_path, on_execute, on_notify, poll_interval_s=1)
    job = scheduler.create_job(
        ScheduleSpec(
            name="daily",
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
            prompt="执行日报",
            purpose="日报",
        ),
        created_by="user-1",
        channel="feishu",
        notify_target="chat-1",
        conversation_id="chat-1",
    )

    jobs = scheduler.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].job_id == job.job_id
    assert scheduler.get_job(job.job_id) is not None


@pytest.mark.asyncio
async def test_scheduler_run_job_now(tmp_path: Path) -> None:
    async def on_execute(job):
        return f"result:{job.prompt}"

    notifications: list[str] = []

    async def on_notify(job, content: str) -> None:
        notifications.append(content)

    scheduler = SchedulerService(tmp_path, on_execute, on_notify, poll_interval_s=1)
    job = scheduler.create_job(
        ScheduleSpec(
            name="daily",
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
            prompt="执行日报",
            purpose="日报",
        ),
        created_by="user-1",
        channel="feishu",
        notify_target="chat-1",
        conversation_id="chat-1",
    )

    ran = await scheduler.run_job_now(job.job_id)

    assert ran is True
    saved = scheduler.get_job(job.job_id)
    assert saved is not None
    assert saved.last_status == "succeeded"
    assert notifications[0].startswith("⏰ 定时任务开始")
    assert notifications[-1].startswith("✅ 定时任务完成")


@pytest.mark.asyncio
async def test_scheduler_tick_runs_due_jobs(tmp_path: Path) -> None:
    executed: list[str] = []

    async def on_execute(job):
        executed.append(job.job_id)
        return "ok"

    async def on_notify(job, content: str) -> None:
        return None

    scheduler = SchedulerService(tmp_path, on_execute, on_notify, poll_interval_s=1)
    job = scheduler.create_job(
        ScheduleSpec(
            name="daily",
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
            prompt="执行日报",
            purpose="日报",
        ),
        created_by="user-1",
        channel="feishu",
        notify_target="chat-1",
        conversation_id="chat-1",
    )
    saved = scheduler.get_job(job.job_id)
    assert saved is not None
    saved.next_run_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()

    await scheduler._tick()
    await scheduler._tick()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert executed == [job.job_id]
