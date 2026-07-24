from datetime import datetime, timedelta
from typing import Literal


DayMode = Literal["workday", "natural"]
_VALID_MODES = ("workday", "natural")


def add_days(dt: datetime, days: int, mode: DayMode) -> datetime:
    _validate_mode(mode)
    if mode == "natural":
        return dt + timedelta(days=days)

    result = dt
    direction = 1 if days >= 0 else -1
    remaining = abs(days)
    while remaining:
        result += timedelta(days=direction)
        if result.weekday() < 5:
            remaining -= 1
    return result


def subtract_days(dt: datetime, days: int, mode: DayMode) -> datetime:
    return add_days(dt, -days, mode)


def days_available(start: datetime, end: datetime, mode: DayMode) -> int:
    _validate_mode(mode)
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
