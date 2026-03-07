"""Tests for Dispatch schema."""

import pytest

from ccbot.models.dispatch import DispatchPayload, DispatchResult, WorkerResult, WorkerTask


class TestWorkerTask:
    """Test cases for WorkerTask."""

    def test_valid_task(self):
        """Create a valid worker task."""
        task = WorkerTask(name="backend", task="Implement API", cwd="/workspace", model="claude-sonnet-4-6")
        assert task.name == "backend"
        assert task.task == "Implement API"
        assert task.cwd == "/workspace"
        assert task.model == "claude-sonnet-4-6"
        assert task.max_turns == 30

    def test_defaults(self):
        """Default values should be applied."""
        task = WorkerTask(name="reviewer", task="Review code")
        assert task.cwd == "."
        assert task.model == ""
        assert task.max_turns == 30

    def test_task_required(self):
        """Task is required."""
        with pytest.raises(ValueError):
            WorkerTask(name="test", task="")

    def test_name_required(self):
        """Name is required."""
        with pytest.raises(ValueError):
            WorkerTask(name="", task="test task")

    def test_max_turns_bounds(self):
        """Max turns must be 1-100."""
        with pytest.raises(ValueError):
            WorkerTask(name="test", task="task", max_turns=0)
        with pytest.raises(ValueError):
            WorkerTask(name="test", task="task", max_turns=101)


class TestDispatchPayload:
    """Test cases for DispatchPayload."""

    def test_from_text_valid(self):
        """Parse valid dispatch block from text."""
        text = '''
Some explanation here.

<dispatch>
[
  {"name": "frontend", "task": "Build UI", "cwd": "/app"},
  {"name": "backend", "task": "Build API"}
]
</dispatch>

More text.
'''
        payload = DispatchPayload.from_text(text)
        assert payload is not None
        assert len(payload.tasks) == 2
        assert payload.tasks[0].name == "frontend"
        assert payload.tasks[1].name == "backend"

    def test_from_text_no_dispatch(self):
        """Return None when no dispatch block."""
        text = "Just some regular text without dispatch."
        assert DispatchPayload.from_text(text) is None

    def test_from_text_invalid_json(self):
        """Return None for invalid JSON."""
        text = "<dispatch>not valid json</dispatch>"
        assert DispatchPayload.from_text(text) is None

    def test_from_text_not_a_list(self):
        """Return None when dispatch content is not a list."""
        text = "<dispatch>{\"name\": \"test\"}</dispatch>"
        assert DispatchPayload.from_text(text) is None

    def test_worker_names(self):
        """Get comma-separated worker names."""
        tasks = [
            WorkerTask(name="a", task="task a"),
            WorkerTask(name="b", task="task b"),
        ]
        payload = DispatchPayload(tasks=tasks)
        assert payload.worker_names == "a, b"

    def test_to_json(self):
        """Serialize to JSON."""
        tasks = [WorkerTask(name="test", task="test task")]
        payload = DispatchPayload(tasks=tasks)
        json_str = payload.to_json()
        assert '"name": "test"' in json_str
        assert '"task": "test task"' in json_str


class TestWorkerResult:
    """Test cases for WorkerResult."""

    def test_success_result(self):
        """Create successful result."""
        result = WorkerResult.from_result("worker1", "Task completed successfully")
        assert result.name == "worker1"
        assert result.success is True
        assert result.result == "Task completed successfully"
        assert result.error == ""

    def test_failure_result(self):
        """Create failure result from exception."""
        exc = ValueError("Something went wrong")
        result = WorkerResult.from_exception("worker2", exc)
        assert result.name == "worker2"
        assert result.success is False
        assert result.error == "Something went wrong"
        assert result.result == ""


class TestDispatchResult:
    """Test cases for DispatchResult."""

    def test_all_succeeded(self):
        """Check if all workers succeeded."""
        workers = [
            WorkerResult.from_result("a", "done"),
            WorkerResult.from_result("b", "done"),
        ]
        result = DispatchResult(workers=workers)
        assert result.all_succeeded is True
        assert result.failed_workers == []

    def test_some_failed(self):
        """Check failed workers detection."""
        workers = [
            WorkerResult.from_result("a", "done"),
            WorkerResult.from_exception("b", ValueError("fail")),
        ]
        result = DispatchResult(workers=workers)
        assert result.all_succeeded is False
        assert result.failed_workers == ["b"]

    def test_to_synthesis_prompt(self):
        """Generate synthesis prompt."""
        workers = [
            WorkerResult.from_result("frontend", "UI built"),
            WorkerResult.from_exception("backend", RuntimeError("DB error")),
        ]
        result = DispatchResult(workers=workers)
        prompt = result.to_synthesis_prompt()
        assert "frontend" in prompt
        assert "UI built" in prompt
        assert "backend" in prompt
        assert "DB error" in prompt
        assert "❌" in prompt
