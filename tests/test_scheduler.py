from __future__ import annotations

import asyncio
import json
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

    assert ran == "started"
    saved = scheduler.get_job(job.job_id)
    assert saved is not None
    assert saved.last_status == "succeeded"
    assert notifications[0].startswith("⏰ 定时任务开始")
    assert notifications[-1].startswith("✅ 定时任务完成")


@pytest.mark.asyncio
async def test_scheduler_run_job_now_rejects_active_job(tmp_path: Path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def on_execute(job):
        started.set()
        await release.wait()
        return f"result:{job.prompt}"

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

    task = asyncio.create_task(scheduler.run_job_now(job.job_id))
    await started.wait()

    result = await scheduler.run_job_now(job.job_id)
    assert result == "already_running"

    release.set()
    first = await task
    assert first == "started"


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


@pytest.mark.asyncio
async def test_scheduler_stop_resets_cancelled_running_job(tmp_path: Path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def on_execute(job):
        started.set()
        await release.wait()
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

    scheduler._launch_job(job)
    await started.wait()
    await asyncio.sleep(0)

    assert scheduler.get_job(job.job_id) is not None
    assert scheduler.get_job(job.job_id).last_status == "running"

    await scheduler.stop()

    saved = scheduler.get_job(job.job_id)
    assert saved is not None
    assert saved.last_status == "idle"
    assert saved.last_result_summary == "cancelled"


def test_scheduler_load_jobs_tolerates_invalid_json(tmp_path: Path) -> None:
    jobs_file = tmp_path / ".ccbot" / "schedules" / "jobs.json"
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    jobs_file.write_text("not json", encoding="utf-8")

    scheduler = SchedulerService(tmp_path, lambda job: None, lambda job, content: None)  # type: ignore[arg-type]

    assert scheduler.list_jobs() == []


def test_scheduler_load_jobs_skips_invalid_records(tmp_path: Path) -> None:
    jobs_file = tmp_path / ".ccbot" / "schedules" / "jobs.json"
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    jobs_file.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "job_id": "ok-1",
                        "name": "daily",
                        "cron_expr": "0 9 * * *",
                        "timezone": "Asia/Shanghai",
                        "prompt": "执行日报",
                        "next_run_at": datetime.now(UTC).isoformat(),
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                    {
                        "job_id": "bad-1",
                        "name": "bad",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    scheduler = SchedulerService(tmp_path, lambda job: None, lambda job, content: None)  # type: ignore[arg-type]

    jobs = scheduler.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].job_id == "ok-1"


def test_scheduler_ensure_job_creates_updates_and_reuses_system_key(tmp_path: Path) -> None:
    scheduler = SchedulerService(
        tmp_path,
        lambda job: None,  # type: ignore[arg-type]
        lambda job, content: None,  # type: ignore[arg-type]
    )
    spec = ScheduleSpec(
        name="日报",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        prompt="生成日报",
        purpose="日报",
    )

    created, created_state = scheduler.ensure_job(
        spec,
        created_by="system",
        channel="feishu",
        notify_target="chat-1",
        conversation_id="chat-1",
        system_key="reports.daily",
    )
    assert created_state == "created"
    assert created.system_key == "reports.daily"
    assert scheduler.get_job_by_system_key("reports.daily") is not None

    existing, existing_state = scheduler.ensure_job(
        spec,
        created_by="system",
        channel="feishu",
        notify_target="chat-1",
        conversation_id="chat-1",
        system_key="reports.daily",
    )
    assert existing_state == "existing"
    assert existing.job_id == created.job_id

    updated, updated_state = scheduler.ensure_job(
        ScheduleSpec(
            name="日报",
            cron_expr="30 10 * * *",
            timezone="UTC",
            prompt="生成新的日报",
            purpose="更新后的日报",
        ),
        created_by="system",
        channel="email",
        notify_target="ops@example.com",
        conversation_id="chat-2",
        system_key="reports.daily",
    )
    assert updated_state == "updated"
    assert updated.job_id == created.job_id
    assert updated.cron_expr == "30 10 * * *"
    assert updated.timezone == "UTC"
    assert updated.prompt == "生成新的日报"
    assert updated.notify_target == "ops@example.com"
    assert len(scheduler.list_jobs()) == 1


def test_scheduler_delete_job_by_system_key(tmp_path: Path) -> None:
    scheduler = SchedulerService(
        tmp_path,
        lambda job: None,  # type: ignore[arg-type]
        lambda job, content: None,  # type: ignore[arg-type]
    )
    job = scheduler.create_job(
        ScheduleSpec(
            name="weekly",
            cron_expr="0 9 * * 1",
            timezone="Asia/Shanghai",
            prompt="执行周报",
            purpose="周报",
        ),
        created_by="system",
        channel="feishu",
        notify_target="chat-1",
        conversation_id="chat-1",
        system_key="reports.weekly",
    )

    assert scheduler.delete_job_by_system_key("reports.weekly") is True
    assert scheduler.get_job(job.job_id) is None
    assert scheduler.delete_job_by_system_key("reports.weekly") is False
