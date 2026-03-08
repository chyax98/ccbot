"""Scheduled task models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import BaseModel, Field, field_validator


class ScheduleSpec(BaseModel):
    """Supervisor 生成的定时任务定义。"""

    name: str = Field(..., min_length=1, description="任务名称，简洁可读")
    cron_expr: str = Field(
        ...,
        min_length=1,
        description="标准 5 段 cron 表达式，例如 '0 9 * * *'",
    )
    timezone: str = Field(default="Asia/Shanghai", min_length=1, description="IANA 时区名")
    prompt: str = Field(..., min_length=1, description="到点后发给 Supervisor 的执行提示词")
    purpose: str = Field(default="", description="创建该定时任务的目的说明")

    @field_validator("name", "cron_expr", "timezone", "prompt", "purpose", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("cron_expr")
    @classmethod
    def validate_cron_expr(cls, value: str) -> str:
        if len(value.split()) != 5 or not croniter.is_valid(value):
            raise ValueError("cron_expr 必须是合法的 5 段 cron 表达式")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:  # pragma: no cover - depends on system tz db details
            raise ValueError("timezone 必须是合法的 IANA 时区名") from exc
        return value


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
