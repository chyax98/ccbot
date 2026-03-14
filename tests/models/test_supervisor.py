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


def test_output_format_is_json_schema() -> None:
    output = SupervisorResponse.output_format()
    assert output["type"] == "json_schema"
    assert "schema" in output


def test_from_structured_output_validates_respond() -> None:
    structured = {
        "mode": "respond",
        "user_message": "直接回复",
    }
    result = SupervisorResponse.from_structured_output(structured)
    assert result is not None
    assert result.mode == "respond"
    assert result.user_message == "直接回复"
    assert result.tasks == []


def test_from_structured_output_validates_dispatch() -> None:
    structured = {
        "mode": "dispatch",
        "user_message": "派发任务",
        "tasks": [
            {"name": "frontend", "cwd": "/fe", "task": "写登录页"},
        ],
    }
    result = SupervisorResponse.from_structured_output(structured)
    assert result is not None
    assert result.mode == "dispatch"
    assert len(result.tasks) == 1
    assert result.tasks[0].name == "frontend"


def test_from_structured_output_rejects_invalid() -> None:
    result = SupervisorResponse.from_structured_output({"mode": "nonexistent"})
    assert result is None


def test_from_structured_output_none() -> None:
    assert SupervisorResponse.from_structured_output(None) is None
