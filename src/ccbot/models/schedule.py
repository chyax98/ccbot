"""Scheduled task models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ScheduleSpec(BaseModel):
    """Supervisor 生成的定时任务定义。"""

    name: str = Field(..., description="任务名称，简洁可读")
    cron_expr: str = Field(
        ..., description="标准 5 段 cron 表达式，例如 '0 9 * * *'"
    )
    timezone: str = Field(default="Asia/Shanghai", description="IANA 时区名")
    prompt: str = Field(..., description="到点后发给 Supervisor 的执行提示词")
    purpose: str = Field(default="", description="创建该定时任务的目的说明")


class ScheduledJob(BaseModel):
    """持久化后的定时任务。"""

    job_id: str
    name: str
    cron_expr: str
    timezone: str = "Asia/Shanghai"
    prompt: str
    purpose: str = ""
    created_by: str = ""
    channel: str = ""
    notify_target: str = ""
    conversation_id: str = ""
    enabled: bool = True
    next_run_at: str
    last_run_at: str = ""
    last_status: Literal["idle", "running", "succeeded", "failed"] = "idle"
    last_result_summary: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def runtime_chat_id(self) -> str:
        return f"schedule:{self.job_id}"
