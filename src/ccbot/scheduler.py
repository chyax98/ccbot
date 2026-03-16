"""Persistent scheduler service for supervisor-driven jobs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from croniter import croniter  # type: ignore[import-untyped]
from loguru import logger
from pydantic import ValidationError

from ccbot.models.schedule import ScheduledJob, ScheduleSpec

ExecuteCallback = Callable[[ScheduledJob], Awaitable[str]]
NotifyCallback = Callable[[ScheduledJob, str], Awaitable[None]]


class RunJobNowResult:
    MISSING = "missing"
    STARTED = "started"
    ALREADY_RUNNING = "already_running"


class SchedulerService:
    """轻量持久化 scheduler。"""

    def __init__(
        self,
        workspace_path: Path,
        on_execute: ExecuteCallback,
        on_notify: NotifyCallback,
        poll_interval_s: int = 30,
        job_timeout_s: int = 1800,
    ) -> None:
        self._root = workspace_path / ".ccbot" / "schedules"
        self._root.mkdir(parents=True, exist_ok=True)
        self._jobs_file = self._root / "jobs.json"
        self._on_execute = on_execute
        self._on_notify = on_notify
        self._poll_interval = poll_interval_s
        self._job_timeout = job_timeout_s
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._run_tasks: set[asyncio.Task[None]] = set()
        self._active_runs: set[str] = set()
        self._jobs: dict[str, ScheduledJob] = {}
        self._load_jobs()

    @property
    def active_runs(self) -> frozenset[str]:
        """当前正在执行的 job_id 集合（只读快照）。"""
        return frozenset(self._active_runs)

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
        system_key: str = "",
    ) -> ScheduledJob:
        job_id = uuid.uuid4().hex[:10]
        next_run_at = self._compute_next_run(spec.cron_expr, spec.timezone).isoformat()
        job = ScheduledJob(
            job_id=job_id,
            system_key=system_key,
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

    def get_job_by_system_key(self, system_key: str) -> ScheduledJob | None:
        if not system_key:
            return None
        for job in self._jobs.values():
            if job.system_key == system_key:
                return job
        return None

    def ensure_job(
        self,
        spec: ScheduleSpec,
        *,
        created_by: str,
        channel: str,
        notify_target: str,
        conversation_id: str,
        system_key: str,
    ) -> tuple[ScheduledJob, str]:
        existing = self.get_job_by_system_key(system_key)
        if existing is None:
            return (
                self.create_job(
                    spec,
                    created_by=created_by,
                    channel=channel,
                    notify_target=notify_target,
                    conversation_id=conversation_id,
                    system_key=system_key,
                ),
                "created",
            )

        changed = False
        previous_cron = existing.cron_expr
        previous_timezone = existing.timezone
        updates = {
            "name": spec.name,
            "cron_expr": spec.cron_expr,
            "timezone": spec.timezone,
            "prompt": spec.prompt,
            "purpose": spec.purpose,
            "created_by": created_by,
            "channel": channel,
            "notify_target": notify_target,
            "conversation_id": conversation_id,
            "system_key": system_key,
        }
        for field_name, value in updates.items():
            if getattr(existing, field_name) != value:
                setattr(existing, field_name, value)
                changed = True

        if changed and (
            previous_cron != existing.cron_expr or previous_timezone != existing.timezone
        ):
            existing.next_run_at = self._compute_next_run(
                existing.cron_expr, existing.timezone
            ).isoformat()

        if changed:
            self._save_jobs()
            return existing, "updated"
        return existing, "existing"

    def delete_job(self, job_id: str) -> bool:
        removed = self._jobs.pop(job_id, None)
        if removed is None:
            return False
        self._save_jobs()
        return True

    def delete_job_by_system_key(self, system_key: str) -> bool:
        job = self.get_job_by_system_key(system_key)
        if job is None:
            return False
        return self.delete_job(job.job_id)

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

    def update_job(
        self,
        job_id: str,
        *,
        name: str | None = None,
        cron_expr: str | None = None,
        timezone: str | None = None,
        prompt: str | None = None,
        purpose: str | None = None,
    ) -> ScheduledJob | None:
        """部分更新定时任务字段，返回更新后的 job；job 不存在返回 None。"""
        job = self._jobs.get(job_id)
        if job is None:
            return None

        need_recompute = False
        if name is not None:
            job.name = name
        if cron_expr is not None and cron_expr != job.cron_expr:
            job.cron_expr = cron_expr
            need_recompute = True
        if timezone is not None and timezone != job.timezone:
            job.timezone = timezone
            need_recompute = True
        if prompt is not None:
            job.prompt = prompt
        if purpose is not None:
            job.purpose = purpose

        if need_recompute:
            job.next_run_at = self._compute_next_run(job.cron_expr, job.timezone).isoformat()

        self._save_jobs()
        return job

    async def run_job_now(self, job_id: str) -> str:
        """立即触发定时任务（异步执行，不阻塞调用方）。"""
        job = self._jobs.get(job_id)
        if job is None:
            return RunJobNowResult.MISSING
        if job.job_id in self._active_runs:
            return RunJobNowResult.ALREADY_RUNNING
        self._launch_job(job)
        return RunJobNowResult.STARTED

    def format_jobs(self) -> str:
        """格式化所有定时任务为可读文本。无任务时返回空字符串。"""
        jobs = self.list_jobs()
        if not jobs:
            return ""
        lines = [f"共 {len(jobs)} 个定时任务："]
        for job in jobs:
            status = "enabled" if job.enabled else "paused"
            lines.append(
                f"- {job.job_id}  {job.name}  "
                f"cron={job.cron_expr}  tz={job.timezone}  "
                f"status={status}  next={job.next_run_at}"
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
            await self._safe_notify(job, f"⏰ 定时任务开始：{job.name} ({job.job_id})")
            result = await asyncio.wait_for(self._on_execute(job), timeout=self._job_timeout)
            job.last_run_at = start.isoformat()
            job.last_status = "succeeded"
            job.last_result_summary = result[:500]
            job.next_run_at = self._compute_next_run(job.cron_expr, job.timezone, start).isoformat()
            self._save_jobs()
            if result:
                await self._safe_notify(job, f"✅ 定时任务完成：{job.name}\n\n{result}")
        except asyncio.CancelledError:
            job.last_status = "idle"
            job.last_result_summary = "cancelled"
            self._save_jobs()
            raise
        except TimeoutError:
            job.last_status = "failed"
            job.last_result_summary = f"执行超时（>{self._job_timeout}s）"
            job.next_run_at = self._compute_next_run(job.cron_expr, job.timezone).isoformat()
            self._save_jobs()
            logger.error("定时任务执行超时 job_id={} timeout={}s", job.job_id, self._job_timeout)
            await self._safe_notify(job, f"⏱️ 定时任务超时：{job.name}（超过 {self._job_timeout}s）")
        except Exception as exc:
            job.last_status = "failed"
            job.last_result_summary = str(exc)[:500]
            job.next_run_at = self._compute_next_run(job.cron_expr, job.timezone).isoformat()
            self._save_jobs()
            logger.exception("定时任务执行失败 job_id={}: {}", job.job_id, exc)
            await self._safe_notify(job, f"❌ 定时任务失败：{job.name}\n\n{exc}")
        finally:
            self._active_runs.discard(job.job_id)

    async def _safe_notify(self, job: ScheduledJob, content: str) -> None:
        """发送通知，失败只记日志，不影响任务执行状态。"""
        try:
            await self._on_notify(job, content)
        except Exception as exc:
            logger.warning("定时任务通知发送失败 job_id={}: {}", job.job_id, exc)

    def _load_jobs(self) -> None:
        if not self._jobs_file.exists():
            self._jobs = {}
            return

        try:
            raw = json.loads(self._jobs_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Scheduler jobs 文件读取失败，已忽略: {}", exc)
            self._jobs = {}
            return

        if not isinstance(raw, dict):
            logger.warning("Scheduler jobs 文件格式无效，已忽略")
            self._jobs = {}
            return

        jobs: dict[str, ScheduledJob] = {}
        for item in raw.get("jobs", []):
            try:
                job = ScheduledJob.model_validate(item)
            except ValidationError as exc:
                logger.warning("跳过无效定时任务记录: {}", exc)
                continue
            jobs[job.job_id] = job
        self._jobs = jobs

    def _save_jobs(self) -> None:
        payload = {"jobs": [job.model_dump() for job in self.list_jobs()]}
        self._jobs_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

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
        return cast(datetime, next_local.astimezone(UTC))
