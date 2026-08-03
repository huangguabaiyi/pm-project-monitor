from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from requirement_monitor.calendar import add_days, days_available, subtract_days


SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_add_workday_skips_weekend():
    friday = datetime(2026, 7, 24, 18, 0, tzinfo=SHANGHAI)

    result = add_days(friday, 1, mode="workday")

    assert result == datetime(2026, 7, 27, 18, 0, tzinfo=SHANGHAI)


def test_subtract_workday_skips_weekend():
    monday = datetime(2026, 7, 27, 9, 15, tzinfo=SHANGHAI)

    result = subtract_days(monday, 1, mode="workday")

    assert result == datetime(2026, 7, 24, 9, 15, tzinfo=SHANGHAI)


def test_add_workdays_supports_zero_and_negative_values():
    monday = datetime(2026, 7, 27, 12, 30, tzinfo=SHANGHAI)

    assert add_days(monday, 0, mode="workday") == monday
    assert add_days(monday, -1, mode="workday") == datetime(
        2026, 7, 24, 12, 30, tzinfo=SHANGHAI
    )


def test_natural_days_include_weekend():
    friday = datetime(2026, 7, 24, 18, 0, tzinfo=SHANGHAI)
    monday = datetime(2026, 7, 27, 18, 0, tzinfo=SHANGHAI)

    assert days_available(friday, monday, mode="natural") == 3


def test_natural_day_arithmetic_supports_negative_values():
    monday = datetime(2026, 7, 27, 18, 0, tzinfo=SHANGHAI)

    assert add_days(monday, -3, mode="natural") == datetime(
        2026, 7, 24, 18, 0, tzinfo=SHANGHAI
    )
    assert subtract_days(monday, -3, mode="natural") == datetime(
        2026, 7, 30, 18, 0, tzinfo=SHANGHAI
    )


def test_natural_days_use_shanghai_date_boundaries_at_midnight():
    before_midnight = datetime(2026, 7, 27, 23, 30, tzinfo=SHANGHAI)
    after_midnight = datetime(2026, 7, 27, 16, 30, tzinfo=timezone.utc)

    assert days_available(before_midnight, after_midnight, mode="natural") == 1
    assert days_available(after_midnight, after_midnight, mode="natural") == 0


def test_natural_day_arithmetic_returns_shanghai_local_date():
    midnight = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)

    assert add_days(midnight, 1, mode="natural") == datetime(
        2026, 7, 29, 0, 0, tzinfo=SHANGHAI
    )
    assert subtract_days(midnight, 1, mode="natural") == datetime(
        2026, 7, 27, 0, 0, tzinfo=SHANGHAI
    )


def test_natural_days_cross_weekend_by_calendar_date():
    friday_midnight = datetime(2026, 7, 24, 0, 0, tzinfo=SHANGHAI)
    monday_midnight = datetime(2026, 7, 27, 0, 0, tzinfo=SHANGHAI)

    assert days_available(friday_midnight, monday_midnight, mode="natural") == 3


def test_workdays_available_are_signed_and_support_zero():
    friday = datetime(2026, 7, 24, 18, 0, tzinfo=SHANGHAI)
    monday = datetime(2026, 7, 27, 18, 0, tzinfo=SHANGHAI)

    assert days_available(friday, monday, mode="workday") == 1
    assert days_available(monday, friday, mode="workday") == -1
    assert days_available(friday, friday, mode="workday") == 0


def test_workdays_available_are_symmetric_with_weekend_endpoint():
    friday = datetime(2026, 7, 24, 18, 0, tzinfo=SHANGHAI)
    sunday = datetime(2026, 7, 26, 18, 0, tzinfo=SHANGHAI)

    assert days_available(friday, sunday, mode="workday") == 0
    assert days_available(sunday, friday, mode="workday") == 0


@pytest.mark.parametrize("function", (add_days, subtract_days))
def test_day_arithmetic_rejects_invalid_mode(function):
    value = datetime(2026, 7, 24, 18, 0, tzinfo=SHANGHAI)

    with pytest.raises(ValueError, match="workday.*natural"):
        function(value, 1, mode="business")


def test_days_available_rejects_invalid_mode():
    value = datetime(2026, 7, 24, 18, 0, tzinfo=SHANGHAI)

    with pytest.raises(ValueError, match="workday.*natural"):
        days_available(value, value, mode="business")
