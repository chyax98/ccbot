from __future__ import annotations

from ccbot.models import SupervisorResponse


def test_dispatch_requires_tasks() -> None:
    try:
        SupervisorResponse(mode="dispatch", user_message="x", tasks=[])
    except ValueError:
        return
    raise AssertionError("expected validation error")


def test_respond_disallows_tasks() -> None:
    try:
        SupervisorResponse(mode="respond", tasks=[{"name": "a", "cwd": "/x", "task": "t"}])
    except ValueError:
        return
    raise AssertionError("expected validation error")


def test_schedule_create_requires_schedule() -> None:
    try:
        SupervisorResponse(mode="schedule_create", user_message="x")
    except ValueError:
        return
    raise AssertionError("expected validation error")


def test_schedule_manage_requires_schedule_control() -> None:
    try:
        SupervisorResponse(mode="schedule_manage", user_message="x")
    except ValueError:
        return
    raise AssertionError("expected validation error")


def test_output_format_is_json_schema() -> None:
    output = SupervisorResponse.output_format()
    assert output["type"] == "json_schema"
    assert "schema" in output


def test_from_structured_output_validates_dict() -> None:
    structured = {
        "mode": "schedule_create",
        "user_message": "已安排",
        "schedule": {
            "name": "daily-review",
            "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "prompt": "每天检查一次",
            "purpose": "日报",
        },
    }
    result = SupervisorResponse.from_structured_output(structured)
    assert result is not None
    assert result.mode == "schedule_create"
    assert result.schedule is not None
    assert result.schedule.cron_expr == "0 9 * * *"


def test_from_structured_output_validates_schedule_manage() -> None:
    structured = {
        "mode": "schedule_manage",
        "user_message": "我来删除它",
        "schedule_control": {
            "action": "delete",
            "target": "job-1",
        },
    }
    result = SupervisorResponse.from_structured_output(structured)
    assert result is not None
    assert result.mode == "schedule_manage"
    assert result.schedule_control is not None
    assert result.schedule_control.action == "delete"
