from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


FIELD_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Asia/Shanghai")
    except ZoneInfoNotFoundError as error:
        raise ValueError("invalid timezone") from error


def parse_cron_field(value: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            raise ValueError("invalid cron expression")
        if "/" in token:
            base, step_text = token.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError("invalid cron step")
        else:
            base = token
            step = 1
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(base)
        if start < minimum or end > maximum or start > end:
            raise ValueError("cron value out of range")
        values.update(range(start, end + 1, step))
    if maximum == 7:
        if 7 in values:
            values.add(0)
        values.discard(7)
    return values


def parse_cron(expression: str) -> list[set[int]]:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("cron expression must contain five fields")
    return [
        parse_cron_field(part, minimum, maximum)
        for part, (minimum, maximum) in zip(parts, FIELD_RANGES)
    ]


def validate_cron(expression: str, timezone_name: str) -> None:
    _zone(timezone_name)
    parse_cron(expression)


def next_cron_run(expression: str, timezone_name: str, after: Optional[datetime] = None) -> datetime:
    minute, hour, day, month, weekday = parse_cron(expression)
    zone = _zone(timezone_name)
    current = after or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local = current.astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
    deadline = local + timedelta(days=366 * 2)
    while local <= deadline:
        cron_weekday = (local.weekday() + 1) % 7
        if (
            local.minute in minute
            and local.hour in hour
            and local.day in day
            and local.month in month
            and cron_weekday in weekday
        ):
            return local.astimezone(timezone.utc)
        local += timedelta(minutes=1)
    raise ValueError("cron expression has no run time within two years")
