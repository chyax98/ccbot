"""Structured supervisor decision contract."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, ValidationError, model_validator

from ccbot.models.dispatch import DispatchPayload, WorkerTask
from ccbot.models.schedule import ScheduleSpec


class SupervisorResponse(BaseModel):
    """Supervisor 的结构化输出。"""

    mode: Literal["respond", "dispatch", "schedule_create"] = Field(
        ...,
        description="respond=直接回复用户；dispatch=派发给 workers；schedule_create=创建定时任务",
    )
    user_message: str = Field(default="", description="返回给用户的自然语言说明")
    tasks: list[WorkerTask] = Field(default_factory=list, description="待派发的 worker 任务")
    schedule: ScheduleSpec | None = Field(default=None, description="待创建的定时任务")

    @model_validator(mode="after")
    def validate_mode_payload(self) -> Self:
        if self.mode == "dispatch":
            if not self.tasks:
                raise ValueError("dispatch 模式必须包含至少一个 task")
            if self.schedule is not None:
                raise ValueError("dispatch 模式不能包含 schedule")
        elif self.mode == "respond":
            if self.tasks:
                raise ValueError("respond 模式不能包含 tasks")
            if self.schedule is not None:
                raise ValueError("respond 模式不能包含 schedule")
        elif self.mode == "schedule_create":
            if self.tasks:
                raise ValueError("schedule_create 模式不能包含 tasks")
            if self.schedule is None:
                raise ValueError("schedule_create 模式必须包含 schedule")
        return self

    @property
    def dispatch_payload(self) -> DispatchPayload | None:
        if self.mode != "dispatch":
            return None
        return DispatchPayload(tasks=self.tasks)

    @classmethod
    def output_format(cls) -> dict[str, Any]:
        """Claude Agent SDK 的 JSON Schema 输出格式。"""
        return {
            "type": "json_schema",
            "schema": cls.model_json_schema(),
        }

    @classmethod
    def from_structured_output(cls, data: Any) -> Self | None:
        if data is None:
            return None
        try:
            if isinstance(data, cls):
                return data
            if isinstance(data, dict):
                return cls.model_validate(data)
        except ValidationError:
            return None
        return None
