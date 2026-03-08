"""Persistent scheduler service for supervisor-driven jobs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from croniter import croniter
from loguru import logger

from ccbot.models.schedule import ScheduledJob, ScheduleSpec

ExecuteCallback = Callable[[ScheduledJob], Awaitable[str]]
NotifyCallback = Callable[[ScheduledJob, str], Awaitable[None]]


class SchedulerService:
    """轻量持久化 scheduler。"""

    def __init__(
        self,
        workspace_path: Path,
        on_execute: ExecuteCallback,
        on_notify: NotifyCallback,
        poll_interval_s: int = 30,
    ) -> None:
        self._root = workspace_path / ".ccbot" / "schedules"
        self._root.mkdir(parents=True, exist_ok=True)
        self._jobs_file = self._root / "jobs.json"
        self._on_execute = on_execute
        self._on_notify = on_notify
        self._poll_interval = poll_interval_s
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._run_tasks: set[asyncio.Task[None]] = set()
        self._active_runs: set[str] = set()
        self._jobs: dict[str, ScheduledJob] = {}
        self._load_jobs()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="scheduler-loop")
        logger.info("Scheduler 已启动（poll={}s, jobs={}）", self._poll_interval, len(self._jobs))

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        for run_task in list(self._run_tasks):
            run_task.cancel()
        if self._run_tasks:
            await asyncio.gather(*self._run_tasks, return_exceptions=True)
        self._run_tasks.clear()
        self._save_jobs()
        logger.info("Scheduler 已停止")

    def create_job(
        self,
        spec: ScheduleSpec,
        *,
        created_by: str,
        channel: str,
        notify_target: str,
        conversation_id: str,
    ) -> ScheduledJob:
        job_id = uuid.uuid4().hex[:10]
        next_run_at = self._compute_next_run(spec.cron_expr, spec.timezone).isoformat()
        job = ScheduledJob(
            job_id=job_id,
            name=spec.name,
            cron_expr=spec.cron_expr,
            timezone=spec.timezone,
            prompt=spec.prompt,
            purpose=spec.purpose,
            created_by=created_by,
            channel=channel,
            notify_target=notify_target,
            conversation_id=conversation_id,
            next_run_at=next_run_at,
        )
        self._jobs[job.job_id] = job
        self._save_jobs()
        return job

    def list_jobs(self) -> list[ScheduledJob]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at)

    def get_job(self, job_id: str) -> ScheduledJob | None:
        return self._jobs.get(job_id)

    def delete_job(self, job_id: str) -> bool:
        removed = self._jobs.pop(job_id, None)
        if removed is None:
            return False
        self._save_jobs()
        return True

    def pause_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        job.enabled = False
        self._save_jobs()
        return True

    def resume_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        job.enabled = True
        job.next_run_at = self._compute_next_run(job.cron_expr, job.timezone).isoformat()
        self._save_jobs()
        return True

    async def run_job_now(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        await self._run_job(job)
        return True

    def format_status(self) -> str:
        if not self._jobs:
            return ""
        lines = ["[系统信息] 当前定时任务:"]
        for job in self.list_jobs():
            state = "启用" if job.enabled else "暂停"
            lines.append(
                f"- {job.job_id} {job.name} ({state}): cron={job.cron_expr}, next={job.next_run_at}"
            )
        return "\n".join(lines)

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._poll_interval)
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Scheduler 轮询失败: {}", exc)

    async def _tick(self) -> None:
        now = datetime.now(UTC)
        for job in self.list_jobs():
            if not job.enabled or job.job_id in self._active_runs:
                continue
            due_at = datetime.fromisoformat(job.next_run_at)
            if due_at <= now:
                self._launch_job(job)

    def _launch_job(self, job: ScheduledJob) -> None:
        if job.job_id in self._active_runs:
            return
        self._active_runs.add(job.job_id)
        task = asyncio.create_task(self._run_job(job), name=f"scheduler-job-{job.job_id}")
        self._run_tasks.add(task)

        def _cleanup(done_task: asyncio.Task[None]) -> None:
            self._run_tasks.discard(done_task)
            with contextlib.suppress(asyncio.CancelledError):
                exc = done_task.exception()
                if exc is not None:
                    logger.error("Scheduler job 异常退出 job_id={}: {}", job.job_id, exc)

        task.add_done_callback(_cleanup)

    async def _run_job(self, job: ScheduledJob) -> None:
        if job.job_id not in self._active_runs:
            self._active_runs.add(job.job_id)
        job.last_status = "running"
        self._save_jobs()

        try:
            start = datetime.now(UTC)
            await self._on_notify(job, f"⏰ 定时任务开始：{job.name} ({job.job_id})")
            result = await self._on_execute(job)
            job.last_run_at = start.isoformat()
            job.last_status = "succeeded"
            job.last_result_summary = result[:500]
            job.next_run_at = self._compute_next_run(job.cron_expr, job.timezone, start).isoformat()
            self._save_jobs()
            if result:
                await self._on_notify(job, f"✅ 定时任务完成：{job.name}\n\n{result}")
        except Exception as exc:
            job.last_status = "failed"
            job.last_result_summary = str(exc)[:500]
            job.next_run_at = self._compute_next_run(job.cron_expr, job.timezone).isoformat()
            self._save_jobs()
            logger.exception("定时任务执行失败 job_id={}: {}", job.job_id, exc)
            await self._on_notify(job, f"❌ 定时任务失败：{job.name}\n\n{exc}")
        finally:
            self._active_runs.discard(job.job_id)

    def _load_jobs(self) -> None:
        if not self._jobs_file.exists():
            self._jobs = {}
            return
        raw = json.loads(self._jobs_file.read_text(encoding="utf-8"))
        self._jobs = {
            item["job_id"]: ScheduledJob.model_validate(item)
            for item in raw.get("jobs", [])
        }

    def _save_jobs(self) -> None:
        payload = {"jobs": [job.model_dump() for job in self.list_jobs()]}
        self._jobs_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _compute_next_run(
        cron_expr: str,
        timezone: str,
        base_time: datetime | None = None,
    ) -> datetime:
        tz = ZoneInfo(timezone)
        localized_base = (base_time or datetime.now(UTC)).astimezone(tz)
        next_local = croniter(cron_expr, localized_base).get_next(datetime)
        if next_local.tzinfo is None:
            next_local = next_local.replace(tzinfo=tz)
        return next_local.astimezone(UTC)
