import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Pattern

from requirement_monitor.models import FixedRules


_CLAUSE_SPLIT_PATTERN = re.compile(r"([,，。；;\n])")
_HARD_BOUNDARIES = {"。", "；", ";", "\n"}
_NON_SERVER_DOMAIN_PATTERN = re.compile(
    r"(?:客户端|车辆|中枢平台|嵌入式|插件|助手)"
)
_AT_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z])AT(?![A-Za-z])", re.IGNORECASE)
_PV_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z])PV(?![A-Za-z])", re.IGNORECASE)
_BUG_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z])Bug(?![A-Za-z])", re.IGNORECASE)
_WEEKDAY_MENTION_PATTERN = re.compile(r"周[一二三四五六日天]")
_SERVER_WEEKDAYS_PATTERN = re.compile(
    r"服务端(?:的)?上线时间\s*固定为\s*每周二\s*(?:和|及|、)\s*"
    r"(?:每)?周四"
)
_CUTOFF_TIME_REGEX = r"(?:17\s*[:：]\s*30|下午\s*5\s*点\s*30\s*分)"
_CUTOFF_PATTERN = re.compile(
    rf"(?:且\s*)?(?:服务端(?:的)?\s*)?"
    rf"(?:{_CUTOFF_TIME_REGEX}\s*后\s*禁止上线"
    rf"|上线截止(?:时间)?\s*(?:为|是|[:：])?\s*{_CUTOFF_TIME_REGEX}"
    rf"|{_CUTOFF_TIME_REGEX}\s*(?:为|是)\s*上线截止(?:时间)?)"
)
_CHECKLIST_PATTERN = re.compile(
    r"(?:服务端(?:上线)?\s*)?(?:需|需要)\s*在\s*前\s*一天\s*提交"
    r"[^,，。；;\n]*checklist[^,，。；;\n]*",
    re.IGNORECASE,
)
_AT_DURATION_PATTERN = re.compile(
    r"AT[^,，。；;\n]*测试周期\s*(?:(?:一般\s*)?需要\s*(?:至少\s*)?"
    r"|至少\s*(?:需要\s*)?)一\s*周\s*半\s*以上",
    re.IGNORECASE,
)
_PV_DAYS_PATTERN = re.compile(
    r"PV\s*(?:测试\s*一般\s*在|测试周期\s*在)\s*"
    r"(?P<days>\d+)\s*天\s*左右",
    re.IGNORECASE,
)
_BUGFIX_DAYS_PATTERN = re.compile(
    r"(?:加上|预留)\s*(?P<days>\d+)\s*天\s*"
    r"(?:(?:解|修复)\s*Bug|Bug\s*修复)(?:\s*的时间)?",
    re.IGNORECASE,
)
_REGRESSION_DAYS_PATTERN = re.compile(
    r"线上回归\s*(?:一般\s*在|测试周期\s*在)\s*"
    r"(?P<days>\d+)\s*天\s*左右"
)


@dataclass(frozen=True)
class _Clause:
    text: str
    server_launch_context: bool


class FixedRuleParseError(ValueError):
    def __init__(self, missing_rules: List[str]):
        self.missing_rules = tuple(missing_rules)
        super().__init__(f"missing fixed rules: {', '.join(missing_rules)}")


def parse_fixed_rules(text: str) -> FixedRules:
    clauses = _split_clauses(text)
    missing_rules: List[str] = []

    has_server_weekdays = _has_single_valid_clause(
        clauses, _is_server_weekday_candidate, _SERVER_WEEKDAYS_PATTERN
    )
    if not has_server_weekdays:
        missing_rules.append("server_launch_weekdays")

    has_cutoff = _has_single_valid_clause(
        clauses, _is_cutoff_candidate, _CUTOFF_PATTERN
    )
    if not has_cutoff:
        missing_rules.append("server_launch_cutoff")

    has_checklist = _has_single_valid_clause(
        clauses, _is_checklist_candidate, _CHECKLIST_PATTERN
    )
    if not has_checklist:
        missing_rules.append("checklist_days_before")

    has_at_duration = _has_single_valid_clause(
        clauses, _is_at_candidate, _AT_DURATION_PATTERN
    )
    if not has_at_duration:
        missing_rules.extend(("at_workdays", "at_natural_days"))

    pv_days = _single_matched_days(clauses, _is_pv_candidate, _PV_DAYS_PATTERN)
    if pv_days is None:
        missing_rules.append("pv_days")

    bugfix_days = _single_matched_days(
        clauses, _is_bugfix_candidate, _BUGFIX_DAYS_PATTERN
    )
    if bugfix_days is None:
        missing_rules.append("bugfix_days")

    regression_days = _single_matched_days(
        clauses, _is_regression_candidate, _REGRESSION_DAYS_PATTERN
    )
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


def _split_clauses(text: str) -> List[_Clause]:
    clauses: List[_Clause] = []
    server_launch_active = False

    for token in _CLAUSE_SPLIT_PATTERN.split(text):
        if token in _HARD_BOUNDARIES:
            server_launch_active = False
            continue
        if token in {",", "，"}:
            continue

        clause_text = token.strip()
        if not clause_text:
            continue

        has_non_server_domain = (
            _NON_SERVER_DOMAIN_PATTERN.search(clause_text) is not None
        )
        if has_non_server_domain:
            server_launch_active = False

        has_explicit_server = "服务端" in clause_text and not has_non_server_domain
        belongs_to_server = has_explicit_server or server_launch_active
        clauses.append(
            _Clause(
                text=clause_text,
                server_launch_context=belongs_to_server,
            )
        )

        if has_explicit_server and _is_server_weekday_text(clause_text):
            server_launch_active = True

    return clauses


def _has_single_valid_clause(
    clauses: List[_Clause],
    is_candidate: Callable[[_Clause], bool],
    pattern: Pattern[str],
) -> bool:
    candidates = [clause for clause in clauses if is_candidate(clause)]
    return (
        len(candidates) == 1
        and pattern.fullmatch(candidates[0].text) is not None
    )


def _single_matched_days(
    clauses: List[_Clause],
    is_candidate: Callable[[_Clause], bool],
    pattern: Pattern[str],
) -> Optional[int]:
    candidates = [clause for clause in clauses if is_candidate(clause)]
    if len(candidates) != 1:
        return None
    match = pattern.fullmatch(candidates[0].text)
    if match is None:
        return None
    return int(match.group("days"))


def _is_server_weekday_candidate(clause: _Clause) -> bool:
    return _is_server_weekday_text(clause.text)


def _is_server_weekday_text(text: str) -> bool:
    return (
        "服务端" in text
        and "上线" in text
        and _WEEKDAY_MENTION_PATTERN.search(text) is not None
    )


def _is_cutoff_candidate(clause: _Clause) -> bool:
    return clause.server_launch_context and (
        "禁止上线" in clause.text or "上线截止" in clause.text
    )


def _is_checklist_candidate(clause: _Clause) -> bool:
    return clause.server_launch_context and "checklist" in clause.text.lower()


def _is_at_candidate(clause: _Clause) -> bool:
    return (
        _AT_TOKEN_PATTERN.search(clause.text) is not None
        and "测试周期" in clause.text
    )


def _is_pv_candidate(clause: _Clause) -> bool:
    return (
        _PV_TOKEN_PATTERN.search(clause.text) is not None
        and "测试" in clause.text
    )


def _is_bugfix_candidate(clause: _Clause) -> bool:
    return _BUG_TOKEN_PATTERN.search(clause.text) is not None and (
        "加上" in clause.text or "预留" in clause.text
    )


def _is_regression_candidate(clause: _Clause) -> bool:
    return "线上回归" in clause.text
