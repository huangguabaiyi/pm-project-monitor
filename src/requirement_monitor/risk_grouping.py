from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from requirement_monitor.models import RiskFamily, RiskFinding, RiskGroup, RiskLevel


@dataclass(frozen=True)
class _GroupingKey:
    namespace: str
    value: str


@dataclass
class _RiskGroupState:
    reason_code: str
    reason_text: str
    first_finding_index: int
    level: RiskLevel
    stage_refs: List[str] = field(default_factory=list)
    domain_refs: List[str] = field(default_factory=list)
    source_findings: List[RiskFinding] = field(default_factory=list)
    stage_seen: Set[str] = field(default_factory=set, repr=False)
    domain_seen: Set[str] = field(default_factory=set, repr=False)


@dataclass(frozen=True)
class _RiskFamilyDefinition:
    code: str
    title: str


@dataclass
class _RiskFamilyState:
    code: str
    title: str
    first_finding_index: int
    level: RiskLevel
    stage_refs: List[str] = field(default_factory=list)
    domain_refs: List[str] = field(default_factory=list)
    source_findings: List[RiskFinding] = field(default_factory=list)
    stage_seen: Set[str] = field(default_factory=set, repr=False)
    domain_seen: Set[str] = field(default_factory=set, repr=False)


_NODE_DELAY_BUFFER_EXHAUSTION = _RiskFamilyDefinition(
    "node_delay_buffer_exhaustion", "节点延期与缓冲耗尽"
)
_TEST_DURATION_INSUFFICIENT = _RiskFamilyDefinition(
    "test_duration_insufficient", "测试周期不足"
)
_MERGE_WINDOW_INSUFFICIENT = _RiskFamilyDefinition(
    "merge_window_insufficient", "合板窗口不足"
)
_SCHEDULE_MISSING = _RiskFamilyDefinition("schedule_missing", "排期缺失")
_GATE_INCOMPLETE = _RiskFamilyDefinition("gate_incomplete", "门禁未通过")
_PROGRESS_STALLED = _RiskFamilyDefinition("progress_stalled", "进展停滞")
_BLOCKER_RISK = _RiskFamilyDefinition("blocker_risk", "阻塞风险")
_BUFFER_LOW = _RiskFamilyDefinition("buffer_low", "剩余缓冲不足")
_NODE_DELAY_WITH_BUFFER = _RiskFamilyDefinition(
    "node_delay_with_buffer", "节点延期但仍有缓冲"
)
_TRANSLATION_INCOMPLETE = _RiskFamilyDefinition(
    "translation_incomplete", "多语言翻译未完成"
)

_FAMILY_BY_EXACT_CODE = {
    "schedule.buffer_negative": _MERGE_WINDOW_INSUFFICIENT,
    "node.delay_consumes_buffer": _NODE_DELAY_BUFFER_EXHAUSTION,
    "test.duration_below_minimum": _TEST_DURATION_INSUFFICIENT,
    "schedule.minimum_window_insufficient": _MERGE_WINDOW_INSUFFICIENT,
    "domain.completion_after_merge": _MERGE_WINDOW_INSUFFICIENT,
    "server_launch.weekday_invalid": _MERGE_WINDOW_INSUFFICIENT,
    "server_launch.cutoff_exceeded": _MERGE_WINDOW_INSUFFICIENT,
    "stage.current_missing": _SCHEDULE_MISSING,
    "test.schedule_missing": _SCHEDULE_MISSING,
    "test_gate.regression_missing": _SCHEDULE_MISSING,
    "server_launch.checklist_incomplete": _GATE_INCOMPLETE,
    "node.stale_update": _PROGRESS_STALLED,
    "schedule.buffer_low": _BUFFER_LOW,
    "node.delay_with_buffer": _NODE_DELAY_WITH_BUFFER,
    "translation.incomplete": _TRANSLATION_INCOMPLETE,
}
_FAMILY_BY_PREFIX = (
    ("test_gate.", _GATE_INCOMPLETE),
    ("blocker.", _BLOCKER_RISK),
)


def group_risk_findings(
    findings: Sequence[RiskFinding], stage_order: Mapping[str, int]
) -> List[RiskGroup]:
    """Group structured findings by namespaced reason identity.

    A non-blank ``reason_code`` is grouped by the private ``code`` namespace.
    A missing or blank code uses the private ``legacy`` namespace keyed by the
    exact ``reason_text``; its public ``RiskGroup.reason_code`` is rendered as
    ``legacy:<reason_text>``. Groups are ordered by severity descending,
    earliest configured stage, original finding order, and reason text. Stage
    references are ordered by configured stage order with unseen stages after
    them in first-seen order; domain references retain first-seen order.
    """
    grouped: Dict[_GroupingKey, _RiskGroupState] = {}

    for finding_index, finding in enumerate(findings):
        reason_text = finding.reason_text
        reason_code = getattr(finding, "reason_code", None)
        if isinstance(reason_code, str) and reason_code.strip():
            group_key = _GroupingKey("code", reason_code)
            public_reason_code = reason_code
        else:
            group_key = _GroupingKey("legacy", reason_text)
            public_reason_code = f"legacy:{reason_text}"
        group = grouped.get(group_key)
        if group is None:
            group = _RiskGroupState(
                reason_code=public_reason_code,
                reason_text=reason_text,
                first_finding_index=finding_index,
                level=finding.level,
            )
            grouped[group_key] = group

        group.level = max(group.level, finding.level)
        group.source_findings.append(finding)
        _extend_unique(group.stage_refs, group.stage_seen, finding.stage_refs)
        _extend_unique(group.domain_refs, group.domain_seen, finding.domain_refs)

    groups = list(grouped.values())
    for group in groups:
        original_refs = tuple(group.stage_refs)
        first_seen = {ref: index for index, ref in enumerate(original_refs)}
        configured_orders = {
            ref: stage_order[ref] for ref in original_refs if ref in stage_order
        }
        group.stage_refs.sort(
            key=lambda ref: _stage_sort_key(ref, configured_orders, first_seen)
        )

    groups.sort(key=lambda group: _group_sort_key(group, stage_order))
    return [
        RiskGroup(
            reason_code=group.reason_code,
            reason_text=group.reason_text,
            stage_refs=group.stage_refs,
            domain_refs=group.domain_refs,
            level=group.level,
            source_findings=group.source_findings,
        )
        for group in groups
    ]


def group_risk_families(
    findings: Sequence[RiskFinding], stage_order: Mapping[str, int]
) -> List[RiskFamily]:
    grouped: Dict[Tuple[str, object], _RiskFamilyState] = {}

    for finding_index, finding in enumerate(findings):
        definition = _risk_family_definition(
            finding.reason_code, finding.reason_text
        )
        if definition is None:
            group_key: Tuple[str, object] = ("unknown", finding_index)
            code = finding.reason_code
            title = finding.reason_text
        else:
            group_key = ("known", definition.code)
            code = definition.code
            title = definition.title

        family = grouped.get(group_key)
        if family is None:
            family = _RiskFamilyState(
                code=code,
                title=title,
                first_finding_index=finding_index,
                level=finding.level,
            )
            grouped[group_key] = family

        family.level = max(family.level, finding.level)
        _extend_unique(family.stage_refs, family.stage_seen, finding.stage_refs)
        _extend_unique(family.domain_refs, family.domain_seen, finding.domain_refs)
        family.source_findings.append(finding)

    families = list(grouped.values())
    for family in families:
        _sort_stage_refs(family.stage_refs, stage_order)
    families.sort(key=lambda family: _family_sort_key(family, stage_order))
    return [
        RiskFamily(
            code=family.code,
            title=family.title,
            level=family.level,
            stage_refs=family.stage_refs,
            domain_refs=family.domain_refs,
            source_findings=family.source_findings,
        )
        for family in families
    ]


def _extend_unique(target: List[str], seen: Set[str], values: Sequence[str]) -> None:
    for value in values:
        if value and value not in seen:
            target.append(value)
            seen.add(value)


def _risk_family_definition(
    reason_code: str, reason_text: str
) -> Optional[_RiskFamilyDefinition]:
    definition = _FAMILY_BY_EXACT_CODE.get(reason_code)
    if definition is not None:
        return definition
    for prefix, prefix_definition in _FAMILY_BY_PREFIX:
        if reason_code.startswith(prefix):
            return prefix_definition
    if "计划测试周期低于最低要求" in reason_text:
        return _TEST_DURATION_INSUFFICIENT
    return None


def _sort_stage_refs(stage_refs: List[str], stage_order: Mapping[str, int]) -> None:
    original_refs = tuple(stage_refs)
    first_seen = {ref: index for index, ref in enumerate(original_refs)}
    configured_orders = {
        ref: stage_order[ref] for ref in original_refs if ref in stage_order
    }
    stage_refs.sort(
        key=lambda ref: _stage_sort_key(ref, configured_orders, first_seen)
    )


def _stage_sort_key(
    ref: str, configured_orders: Mapping[str, int], first_seen: Mapping[str, int]
) -> Tuple[int, int, int]:
    if ref in configured_orders:
        return (0, configured_orders[ref], first_seen[ref])
    return (1, 0, first_seen[ref])


def _group_sort_key(
    group: _RiskGroupState, stage_order: Mapping[str, int]
) -> Tuple[int, int, int, int, str]:
    first_stage = group.stage_refs[0] if group.stage_refs else None
    if first_stage is not None and first_stage in stage_order:
        stage_sort = (0, stage_order[first_stage])
    else:
        stage_sort = (1, 0)
    return (
        -int(group.level),
        stage_sort[0],
        stage_sort[1],
        group.first_finding_index,
        group.reason_text,
    )


def _family_sort_key(
    family: _RiskFamilyState, stage_order: Mapping[str, int]
) -> Tuple[int, int, int, int, str]:
    first_stage = family.stage_refs[0] if family.stage_refs else None
    if first_stage is not None and first_stage in stage_order:
        stage_sort = (0, stage_order[first_stage])
    else:
        stage_sort = (1, 0)
    return (
        -int(family.level),
        stage_sort[0],
        stage_sort[1],
        family.first_finding_index,
        family.title,
    )
