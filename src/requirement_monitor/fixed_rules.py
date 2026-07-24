import re
from pathlib import Path
from typing import List, Match, Optional

from requirement_monitor.models import FixedRules


_SERVER_WEEKDAYS_PATTERN = re.compile(
    r"服务端[^。\n]*周二[^。\n]*(?:和|、|及)[^。\n]*周四"
)
_CHECKLIST_PATTERN = re.compile(r"前\s*一天[^。\n]*checklist", re.IGNORECASE)
_CUTOFF_PATTERN = re.compile(
    r"(?:17\s*[:：]\s*30|下午\s*5\s*点\s*30\s*分)"
)
_AT_DURATION_PATTERN = re.compile(r"AT[^。\n]*一\s*周\s*半", re.IGNORECASE)
_PV_DAYS_PATTERN = re.compile(
    r"PV\s*测试[^。\n]*?(?P<days>\d+)\s*天", re.IGNORECASE
)
_BUGFIX_DAYS_PATTERN = re.compile(
    r"(?:加上|预留)\s*(?P<days>\d+)\s*天\s*(?:解|修复)\s*Bug",
    re.IGNORECASE,
)
_REGRESSION_DAYS_PATTERN = re.compile(
    r"线上回归[^。\n]*?(?P<days>\d+)\s*天"
)


class FixedRuleParseError(ValueError):
    def __init__(self, missing_rules: List[str]):
        self.missing_rules = tuple(missing_rules)
        super().__init__(f"missing fixed rules: {', '.join(missing_rules)}")


def parse_fixed_rules(text: str) -> FixedRules:
    missing_rules: List[str] = []

    has_server_weekdays = _SERVER_WEEKDAYS_PATTERN.search(text) is not None
    if not has_server_weekdays:
        missing_rules.append("server_launch_weekdays")

    has_cutoff = _CUTOFF_PATTERN.search(text) is not None
    if not has_cutoff:
        missing_rules.append("server_launch_cutoff")

    has_checklist = _CHECKLIST_PATTERN.search(text) is not None
    if not has_checklist:
        missing_rules.append("checklist_days_before")

    has_at_duration = _AT_DURATION_PATTERN.search(text) is not None
    if not has_at_duration:
        missing_rules.extend(("at_workdays", "at_natural_days"))

    pv_days = _matched_days(_PV_DAYS_PATTERN.search(text))
    if pv_days is None:
        missing_rules.append("pv_days")

    bugfix_days = _matched_days(_BUGFIX_DAYS_PATTERN.search(text))
    if bugfix_days is None:
        missing_rules.append("bugfix_days")

    regression_days = _matched_days(_REGRESSION_DAYS_PATTERN.search(text))
    if regression_days is None:
        missing_rules.append("regression_days")

    if missing_rules:
        raise FixedRuleParseError(missing_rules)

    return FixedRules(
        server_launch_weekdays={1, 3},
        server_launch_cutoff="17:30",
        checklist_days_before=1,
        at_workdays=8,
        at_natural_days=11,
        pv_days=pv_days,
        bugfix_days=bugfix_days,
        regression_days=regression_days,
    )


def load_fixed_rules(path: Path) -> FixedRules:
    return parse_fixed_rules(path.read_text(encoding="utf-8"))


def _matched_days(match: Optional[Match[str]]) -> Optional[int]:
    if match is None:
        return None
    return int(match.group("days"))
