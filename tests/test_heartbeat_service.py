"""Tests for HeartbeatService and _has_active_tasks helper."""

import asyncio

import pytest

from ccbot.heartbeat import HeartbeatService, _has_active_tasks

# ---- _has_active_tasks ----


def test_no_active_section_returns_false() -> None:
    assert not _has_active_tasks("# Notes\n\nsome text")


def test_empty_active_section_returns_false() -> None:
    content = "## Active Tasks\n\n## Done\n\nfoo"
    assert not _has_active_tasks(content)


def test_active_section_with_content_returns_true() -> None:
    content = "## Active Tasks\n\n- [ ] do something\n\n## Done"
    assert _has_active_tasks(content)


def test_active_section_comment_only_returns_false() -> None:
    content = "## Active Tasks\n\n<!-- nothing here -->\n\n## Done"
    assert not _has_active_tasks(content)


def test_case_insensitive_active_heading() -> None:
    content = "## ACTIVE TASK\n\n- [ ] job"
    assert _has_active_tasks(content)


# ---- HeartbeatService ----


@pytest.mark.asyncio
async def test_tick_skips_when_file_missing(tmp_path) -> None:
    executed: list[str] = []

    async def on_execute(prompt: str) -> str:
        executed.append(prompt)
        return "done"

    async def on_notify(content: str) -> None:
        pass

    service = HeartbeatService(
        heartbeat_file=tmp_path / "HEARTBEAT.md",
        on_execute=on_execute,
        on_notify=on_notify,
    )
    await service._tick()
    assert executed == []


@pytest.mark.asyncio
async def test_tick_skips_when_no_active_tasks(tmp_path) -> None:
    hb = tmp_path / "HEARTBEAT.md"
    hb.write_text("## Active Tasks\n\n<!-- empty -->\n", encoding="utf-8")

    executed: list[str] = []

    async def on_execute(prompt: str) -> str:
        executed.append(prompt)
        return "done"

    async def on_notify(content: str) -> None:
        pass

    service = HeartbeatService(
        heartbeat_file=hb,
        on_execute=on_execute,
        on_notify=on_notify,
    )
    await service._tick()
    assert executed == []


@pytest.mark.asyncio
async def test_tick_executes_and_notifies_when_active_tasks(tmp_path) -> None:
    hb = tmp_path / "HEARTBEAT.md"
    hb.write_text("## Active Tasks\n\n- [ ] check news\n", encoding="utf-8")

    executed: list[str] = []
    notified: list[str] = []

    async def on_execute(prompt: str) -> str:
        executed.append(prompt)
        return "report ready"

    async def on_notify(content: str) -> None:
        notified.append(content)

    service = HeartbeatService(
        heartbeat_file=hb,
        on_execute=on_execute,
        on_notify=on_notify,
    )
    await service._tick()

    assert len(executed) == 1
    assert "HEARTBEAT.md" in executed[0] or "check news" in executed[0]
    assert notified == ["report ready"]


@pytest.mark.asyncio
async def test_start_creates_background_task(tmp_path) -> None:
    service = HeartbeatService(
        heartbeat_file=tmp_path / "HEARTBEAT.md",
        on_execute=lambda p: asyncio.coroutine(lambda: "")(),
        on_notify=lambda c: asyncio.coroutine(lambda: None)(),
        interval_s=9999,
    )
    await service.start()
    assert service._task is not None
    assert not service._task.done()
    service.stop()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_stop_cancels_task(tmp_path) -> None:
    service = HeartbeatService(
        heartbeat_file=tmp_path / "HEARTBEAT.md",
        on_execute=lambda p: asyncio.coroutine(lambda: "")(),
        on_notify=lambda c: asyncio.coroutine(lambda: None)(),
        interval_s=9999,
    )
    await service.start()
    service.stop()
    await asyncio.sleep(0.05)
    assert service._task is None or service._task.cancelled() or service._task.done()
