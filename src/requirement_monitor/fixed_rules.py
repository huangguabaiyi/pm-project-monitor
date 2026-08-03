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
_AT1_PATTERN = re.compile(r"AT\s*(?:测试\s*)?(?:第一轮|第\s*一\s*轮|AT?1轮|1轮)", re.I)
_AT2_PATTERN = re.compile(r"AT\s*(?:测试\s*)?(?:第二轮|第\s*二\s*轮|AT?2轮|2轮)", re.I)
_PV1_PATTERN = re.compile(r"PV\s*(?:测试\s*)?(?:第一轮|第\s*一\s*轮|PV?1轮|1轮)", re.I)
_PV2_PATTERN = re.compile(r"PV\s*(?:测试\s*)?(?:第二轮|第\s*二\s*轮|PV?2轮|2轮)", re.I)
_EXPLICIT_DAYS_PATTERN = re.compile(r"(?P<days>\d+)\s*(?:个\s*)?(?:工作日|自然日|天)")
_REVERSE_RULE_PATTERN = re.compile(r"(?:以下|以内|最多|不超过|小于|少于|不需要|无需|并非|不是)")
_PV_DAYS_PATTERN = re.compile(
    r"PV\s*(?:测试\s*一般\s*在|测试周期\s*在)\s*"
    r"(?P<days>\d+)\s*天\s*左右",
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

    stage_days = _parse_explicit_stage_days(clauses)
    legacy_at = _has_single_valid_clause(
        clauses, _is_at_candidate, _AT_DURATION_PATTERN
    )
    if legacy_at:
        if any(stage_days.values()):
            stage_days = {}
        else:
            stage_days = {"at1_days": 4, "at2_days": 4}
    for field_name in ("at1_days", "at2_days"):
        if field_name not in stage_days:
            missing_rules.append(field_name)

    pv_days = _single_matched_days(clauses, _is_pv_candidate, _PV_DAYS_PATTERN)
    if pv_days is not None:
        stage_days.setdefault("pv1_days", pv_days)
    for field_name in ("pv1_days", "pv2_days"):
        if field_name not in stage_days:
            missing_rules.append(field_name)

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
        at1_days=stage_days["at1_days"],
        at2_days=stage_days["at2_days"],
        pv1_days=stage_days["pv1_days"],
        pv2_days=stage_days["pv2_days"],
        at_workdays=8,
        at_natural_days=11,
        pv_days=pv_days or stage_days["pv1_days"],
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


def _parse_explicit_stage_days(clauses: List[_Clause]):
    patterns = (
        ("at1_days", _AT1_PATTERN),
        ("at2_days", _AT2_PATTERN),
        ("pv1_days", _PV1_PATTERN),
        ("pv2_days", _PV2_PATTERN),
    )
    result = {}
    for field_name, pattern in patterns:
        candidates = [clause.text for clause in clauses if pattern.search(clause.text)]
        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        if _REVERSE_RULE_PATTERN.search(candidate):
            continue
        matches = list(_EXPLICIT_DAYS_PATTERN.finditer(candidate))
        if len(matches) == 1:
            result[field_name] = int(matches[0].group("days"))
    return result


def _is_pv_candidate(clause: _Clause) -> bool:
    return (
        _PV_TOKEN_PATTERN.search(clause.text) is not None
        and "测试" in clause.text
    )


def _is_regression_candidate(clause: _Clause) -> bool:
    return "线上回归" in clause.text
