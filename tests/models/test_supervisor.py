"""Tests for structured supervisor contract."""

from ccbot.models import SupervisorResponse


def test_dispatch_requires_tasks() -> None:
    try:
        SupervisorResponse(mode="dispatch", user_message="will dispatch", tasks=[])
    except ValueError:
        return
    raise AssertionError("dispatch mode should require tasks")


def test_respond_disallows_tasks() -> None:
    try:
        SupervisorResponse(
            mode="respond",
            user_message="direct",
            tasks=[{"name": "fe", "cwd": "/fe", "task": "x"}],
        )
    except ValueError:
        return
    raise AssertionError("respond mode should disallow tasks")


def test_output_format_is_json_schema() -> None:
    fmt = SupervisorResponse.output_format()
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["type"] == "object"


def test_from_structured_output_validates_dict() -> None:
    structured = {
        "mode": "dispatch",
        "user_message": "安排任务",
        "tasks": [{"name": "fe", "cwd": "/fe", "task": "build"}],
    }
    result = SupervisorResponse.from_structured_output(structured)
    assert result is not None
    assert result.mode == "dispatch"
    assert result.dispatch_payload is not None
    assert result.dispatch_payload.worker_names == "fe"
