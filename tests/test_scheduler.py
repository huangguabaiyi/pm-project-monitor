from datetime import datetime, timezone

import pytest

from requirement_monitor.scheduler import next_cron_run, parse_cron


def test_next_cron_run_supports_workday_schedule():
    after = datetime(2026, 8, 21, 9, 59, tzinfo=timezone.utc)

    next_run = next_cron_run("0 10 * * 1-5", "Asia/Shanghai", after)

    assert next_run.isoformat() == "2026-08-24T02:00:00+00:00"


def test_parse_cron_supports_ranges_lists_and_steps():
    minute, hour, _, _, weekday = parse_cron("*/15 9,18 * * 1-5")

    assert minute == {0, 15, 30, 45}
    assert hour == {9, 18}
    assert weekday == {1, 2, 3, 4, 5}


def test_parse_cron_rejects_invalid_values():
    with pytest.raises(ValueError):
        parse_cron("90 10 * * 1-5")
