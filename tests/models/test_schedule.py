from __future__ import annotations

import pytest

from ccbot.models.schedule import ScheduleSpec


def test_schedule_spec_accepts_valid_values() -> None:
    spec = ScheduleSpec(
        name="  daily report  ",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        prompt="  生成日报  ",
        purpose="  日报  ",
    )

    assert spec.name == "daily report"
    assert spec.prompt == "生成日报"
    assert spec.purpose == "日报"


def test_schedule_spec_rejects_invalid_cron() -> None:
    with pytest.raises(ValueError):
        ScheduleSpec(
            name="bad",
            cron_expr="bad cron",
            timezone="Asia/Shanghai",
            prompt="x",
        )


def test_schedule_spec_rejects_invalid_timezone() -> None:
    with pytest.raises(ValueError):
        ScheduleSpec(
            name="bad",
            cron_expr="0 9 * * *",
            timezone="Mars/Olympus",
            prompt="x",
        )
