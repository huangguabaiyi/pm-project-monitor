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


def test_calendar_arithmetic_preserves_timezone_and_wall_time_across_dst():
    new_york = ZoneInfo("America/New_York")
    before_dst = datetime(2026, 3, 6, 9, 45, tzinfo=new_york)

    result = add_days(before_dst, 3, mode="natural")

    assert result == datetime(2026, 3, 9, 9, 45, tzinfo=new_york)
    assert result.tzinfo is new_york
    assert result.utcoffset() != before_dst.utcoffset()


def test_calendar_arithmetic_normalizes_dst_gap_to_first_valid_local_time():
    new_york = ZoneInfo("America/New_York")
    before_gap = datetime(2026, 3, 7, 2, 30, tzinfo=new_york)
    after_gap = datetime(2026, 3, 9, 2, 30, tzinfo=new_york)

    added = add_days(before_gap, 1, mode="natural")
    subtracted = subtract_days(after_gap, 1, mode="natural")

    expected = datetime(2026, 3, 8, 3, 0, tzinfo=new_york)
    assert added == expected
    assert subtracted == expected
    assert added.astimezone(timezone.utc).astimezone(new_york) == added
    assert subtracted.astimezone(timezone.utc).astimezone(new_york) == subtracted


def test_calendar_arithmetic_preserves_fold_for_ambiguous_local_time():
    new_york = ZoneInfo("America/New_York")
    before_fold = datetime(2026, 10, 31, 1, 30, tzinfo=new_york, fold=1)

    result = add_days(before_fold, 1, mode="natural")

    assert result == datetime(2026, 11, 1, 1, 30, tzinfo=new_york, fold=1)
    assert result.fold == 1
    assert result.utcoffset() == timedelta(hours=-5)


@pytest.mark.parametrize("function", (add_days, subtract_days))
def test_day_arithmetic_rejects_invalid_mode(function):
    value = datetime(2026, 7, 24, 18, 0, tzinfo=SHANGHAI)

    with pytest.raises(ValueError, match="workday.*natural"):
        function(value, 1, mode="business")


def test_days_available_rejects_invalid_mode():
    value = datetime(2026, 7, 24, 18, 0, tzinfo=SHANGHAI)

    with pytest.raises(ValueError, match="workday.*natural"):
        days_available(value, value, mode="business")
