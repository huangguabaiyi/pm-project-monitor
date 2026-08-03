from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Literal
from zoneinfo import ZoneInfo


DayMode = Literal["workday", "natural"]
_VALID_MODES = ("workday", "natural")
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def add_days(dt: datetime, days: int, mode: DayMode) -> datetime:
    _validate_mode(mode)
    dt = _as_shanghai(dt)
    if mode == "natural":
        target_date = dt.date() + timedelta(days=days)
        return _at_local_date(dt, target_date)

    target_date = dt.date()
    direction = 1 if days >= 0 else -1
    remaining = abs(days)
    while remaining:
        target_date += timedelta(days=direction)
        if target_date.weekday() < 5:
            remaining -= 1
    return _at_local_date(dt, target_date)


def subtract_days(dt: datetime, days: int, mode: DayMode) -> datetime:
    return add_days(dt, -days, mode)


def days_available(start: datetime, end: datetime, mode: DayMode) -> int:
    _validate_mode(mode)
    start = _as_shanghai(start)
    end = _as_shanghai(end)
    if mode == "natural":
        return (end.date() - start.date()).days

    if start.date() == end.date():
        return 0

    direction = 1
    if end.date() < start.date():
        start, end = end, start
        direction = -1

    current = start
    available = 0
    while current.date() != end.date():
        current += timedelta(days=1)
        if current.weekday() < 5:
            available += 1
    return available * direction


def _validate_mode(mode: str) -> None:
    if mode not in _VALID_MODES:
        raise ValueError("mode must be 'workday' or 'natural'")


def _as_shanghai(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=_SHANGHAI)
    return dt.astimezone(_SHANGHAI)


def _at_local_date(dt: datetime, target_date: date) -> datetime:
    candidate = dt.replace(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        fold=dt.fold,
    )
    return _normalize_local_time(candidate)


def _normalize_local_time(candidate: datetime) -> datetime:
    if candidate.tzinfo is None or candidate.utcoffset() is None:
        return candidate
    if _round_trip_preserves_wall_time(candidate):
        return candidate

    wall_time = candidate.replace(tzinfo=None)
    future_wall_times = []
    for fold in (0, 1):
        round_tripped = _round_trip(candidate.replace(fold=fold))
        round_tripped_wall_time = round_tripped.replace(tzinfo=None)
        if round_tripped_wall_time > wall_time:
            future_wall_times.append(round_tripped_wall_time)

    if not future_wall_times:
        raise ValueError("unable to normalize nonexistent local time")

    first_invalid = wall_time
    first_valid = min(future_wall_times)
    while first_valid - first_invalid > timedelta(microseconds=1):
        midpoint = first_invalid + (first_valid - first_invalid) / 2
        if _is_valid_wall_time(midpoint, candidate.tzinfo, candidate.fold):
            first_valid = midpoint
        else:
            first_invalid = midpoint

    return first_valid.replace(tzinfo=candidate.tzinfo, fold=candidate.fold)


def _is_valid_wall_time(
    wall_time: datetime, timezone_info: tzinfo, fold: int
) -> bool:
    candidate = wall_time.replace(tzinfo=timezone_info, fold=fold)
    return _round_trip_preserves_wall_time(candidate)


def _round_trip_preserves_wall_time(value: datetime) -> bool:
    return _round_trip(value).replace(tzinfo=None) == value.replace(tzinfo=None)


def _round_trip(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).astimezone(value.tzinfo)
