from dataclasses import dataclass
from datetime import datetime, time
from math import ceil
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from requirement_monitor.calendar import DayMode, add_days, days_available, subtract_days
from requirement_monitor.models import (
    Blocker,
    DeliveryNode,
    FixedRules,
    NodeRisk,
    NodeStatus,
    ProjectConfig,
    Requirement,
    RequirementRisk,
    RiskLevel,
)
from requirement_monitor.schema import DEFAULT_PROCESS_NODES


_COMPLETED_NODE_STATUSES = {
    NodeStatus.COMPLETED,
    NodeStatus.SKIPPED,
    NodeStatus.CANCELLED,
}
_COMPLETED_BLOCKER_STATUSES = {"已解决", "已完成", "关闭", "已关闭", "已取消"}
_PROCESS_ORDER = {name: index for index, name in enumerate(DEFAULT_PROCESS_NODES)}
_AT1_ORDER = _PROCESS_ORDER["AT 测试第一轮"]
_AT2_ORDER = _PROCESS_ORDER["AT 测试第二轮"]
_PV1_ORDER = _PROCESS_ORDER["PV 测试第一轮"]
_PV2_ORDER = _PROCESS_ORDER["PV 测试第二轮"]
_REGRESSION_ORDER = _PROCESS_ORDER["线上回归"]
_BUGFIX_ORDER = _PV2_ORDER + 0.25
_SPECIAL_ORDER = _PV2_ORDER + 0.5


@dataclass(frozen=True)
class EffectiveRules:
    duration_mode: DayMode
    at_days: int
    pv_days: int
    bugfix_days: int
    regression_days: int
    special_days: Dict[str, int]
    launch_weekdays: Set[int]
    launch_cutoff: str
    checklist_days_before: int


@dataclass(frozen=True)
class _Event:
    order: float
    duration: int
    label: str
    node: Optional[DeliveryNode] = None


@dataclass(frozen=True)
class _DomainEvaluation:
    domain: str
    predicted_completion: Optional[datetime]
    buffer_days: Optional[int]
    level: RiskLevel
    reasons: List[str]
    node_risks: List[NodeRisk]


def resolve_effective_rules(
    fixed: FixedRules, project_config: Optional[ProjectConfig]
) -> EffectiveRules:
    duration_mode: DayMode = (
        project_config.duration_mode if project_config is not None else "workday"
    )
    fixed_at_days = (
        fixed.at_workdays if duration_mode == "workday" else fixed.at_natural_days
    )

    return EffectiveRules(
        duration_mode=duration_mode,
        at_days=_override(project_config, "at_days", fixed_at_days),
        pv_days=_override(project_config, "pv_days", fixed.pv_days),
        bugfix_days=_override(project_config, "bugfix_days", fixed.bugfix_days),
        regression_days=_override(
            project_config, "regression_days", fixed.regression_days
        ),
        special_days={
            "服务端": _override(project_config, "server_special_days", 0),
            "客户端": _override(project_config, "client_special_days", 0),
            "车辆": _override(project_config, "vehicle_special_days", 0),
        },
        launch_weekdays=set(
            project_config.launch_weekdays
            if project_config is not None
            and project_config.launch_weekdays is not None
            else fixed.server_launch_weekdays
        ),
        launch_cutoff=(
            project_config.launch_cutoff
            if project_config is not None and project_config.launch_cutoff is not None
            else fixed.server_launch_cutoff
        ),
        checklist_days_before=fixed.checklist_days_before,
    )


def evaluate_requirement(
    requirement: Requirement,
    nodes: Sequence[DeliveryNode],
    blockers: Sequence[Blocker],
    fixed_rules: FixedRules,
    now: datetime,
    project_config: Optional[ProjectConfig] = None,
) -> Optional[RequirementRisk]:
    if not _is_eligible(requirement):
        return None
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    effective = resolve_effective_rules(fixed_rules, project_config)
    relevant_nodes = [
        node for node in nodes if node.requirement_id == requirement.requirement_id
    ]
    relevant_blockers = [
        blocker
        for blocker in blockers
        if blocker.requirement_id == requirement.requirement_id
    ]

    domain_results = [
        _evaluate_domain(requirement, domain, domain_nodes, effective, now)
        for domain, domain_nodes in _group_nodes_by_domain(relevant_nodes)
    ]
    reasons: List[str] = []
    level = RiskLevel.NORMAL
    affected_domains: List[str] = []

    for domain_result in domain_results:
        level = max(level, domain_result.level)
        reasons.extend(domain_result.reasons)
        if domain_result.level > RiskLevel.NORMAL:
            affected_domains.append(domain_result.domain)

    blocker_level, blocker_reasons, blocker_domains = _evaluate_blockers(
        relevant_blockers, relevant_nodes, now
    )
    level = max(level, blocker_level)
    reasons.extend(blocker_reasons)
    affected_domains.extend(blocker_domains)

    launch_level, launch_reasons = _evaluate_server_launch(
        requirement, relevant_nodes, effective, now
    )
    level = max(level, launch_level)
    reasons.extend(launch_reasons)
    if launch_level > RiskLevel.NORMAL:
        affected_domains.append("服务端")

    predictions = [
        result.predicted_completion
        for result in domain_results
        if result.predicted_completion is not None
    ]
    buffers = [
        result.buffer_days
        for result in domain_results
        if result.buffer_days is not None
    ]

    return RequirementRisk(
        requirement_record_id=requirement.record_id,
        requirement_id=requirement.requirement_id,
        requirement_name=requirement.name,
        project=requirement.project,
        level=level,
        predicted_completion=max(predictions) if predictions else None,
        buffer_days=min(buffers) if buffers else None,
        affected_domains=_deduplicate(affected_domains),
        reasons=_deduplicate(reasons),
        actions=[],
        node_risks=[
            node_risk
            for domain_result in domain_results
            for node_risk in domain_result.node_risks
        ],
    )


def _evaluate_domain(
    requirement: Requirement,
    domain: str,
    nodes: Sequence[DeliveryNode],
    rules: EffectiveRules,
    now: datetime,
) -> _DomainEvaluation:
    checklist_nodes = [node for node in nodes if _is_checklist(node)]
    events = _build_events(
        domain,
        [node for node in nodes if not _is_checklist(node)],
        rules,
    )
    node_risks: List[NodeRisk] = []
    reasons: List[str] = []
    level = RiskLevel.NORMAL
    first_unfinished = next(
        (event.node for event in events if event.node is not None and not _node_done(event.node)),
        None,
    )

    for event in events:
        if event.node is None:
            continue
        safe_deadline = subtract_days(
            requirement.merge_at,
            _downstream_duration(events, event.order),
            rules.duration_mode,
        )
        node_level, node_reasons, node_buffer = _evaluate_node(
            event,
            safe_deadline,
            first_unfinished,
            events,
            rules,
            now,
        )
        level = max(level, node_level)
        reasons.extend(node_reasons)
        node_risks.append(
            NodeRisk(
                node_record_id=event.node.record_id,
                requirement_id=event.node.requirement_id,
                node_name=event.node.name,
                domain=event.node.domain,
                owner_id=event.node.owner_id,
                owner_name=event.node.owner_name,
                level=node_level,
                predicted_completion=_predict_node_completion(event, rules, now),
                safe_deadline=safe_deadline,
                buffer_days=node_buffer,
                reasons=_deduplicate(node_reasons),
                actions=[],
            )
        )

    for checklist_node in checklist_nodes:
        checklist_risk = _evaluate_checklist_node(
            checklist_node, requirement, rules, now
        )
        level = max(level, checklist_risk.level)
        reasons.extend(checklist_risk.reasons)
        node_risks.append(checklist_risk)

    for event in events:
        if (
            event.node is not None
            or event.duration <= 0
            or event.label not in {"AT1", "AT2", "PV1", "PV2", "线上回归"}
            or not _event_is_unfinished(event, events)
        ):
            continue
        safe_deadline = subtract_days(
            requirement.merge_at,
            _downstream_duration(events, event.order),
            rules.duration_mode,
        )
        latest_start = subtract_days(
            safe_deadline, event.duration, rules.duration_mode
        )
        if days_available(now, latest_start, rules.duration_mode) <= 2:
            level = max(level, RiskLevel.WARNING)
            reasons.append("测试排期缺少计划开始时间")

    if not events:
        return _DomainEvaluation(
            domain=domain,
            predicted_completion=None,
            buffer_days=None,
            level=level,
            reasons=_deduplicate(reasons),
            node_risks=node_risks,
        )

    predicted_completion = _predict_domain_completion(events, rules, now)
    remaining_duration = sum(
        event.duration for event in events if _event_is_unfinished(event, events)
    )
    available = days_available(now, requirement.merge_at, rules.duration_mode)
    minimum_buffer = available - remaining_duration
    minimum_completion = add_days(now, remaining_duration, rules.duration_mode)
    workday_minimum_buffer = days_available(
        minimum_completion, requirement.merge_at, "workday"
    )
    workday_schedule_buffer = (
        days_available(predicted_completion, requirement.merge_at, "workday")
        if predicted_completion is not None
        else workday_minimum_buffer
    )
    buffer_days = min(workday_minimum_buffer, workday_schedule_buffer)

    if minimum_buffer < 0:
        level = RiskLevel.SEVERE
        reasons.append(_minimum_window_reason(domain, events))
        reasons.append("剩余缓冲为负")
    elif buffer_days < 0:
        level = RiskLevel.SEVERE
        reasons.append(f"{domain}交付域预计完成时间晚于合板时间")
        reasons.append("剩余缓冲为负")
    elif buffer_days <= 2:
        level = max(level, RiskLevel.WARNING)
        reasons.append("剩余缓冲不超过2天")

    if (
        predicted_completion is not None
        and predicted_completion > requirement.merge_at
    ):
        level = RiskLevel.SEVERE
        reasons.append(f"{domain}交付域预计完成时间晚于合板时间")

    return _DomainEvaluation(
        domain=domain,
        predicted_completion=predicted_completion,
        buffer_days=buffer_days,
        level=level,
        reasons=_deduplicate(reasons),
        node_risks=node_risks,
    )


def _evaluate_node(
    event: _Event,
    safe_deadline: datetime,
    first_unfinished: Optional[DeliveryNode],
    events: Sequence[_Event],
    rules: EffectiveRules,
    now: datetime,
) -> Tuple[RiskLevel, List[str], int]:
    node = event.node
    if node is None:
        raise ValueError("node event is required")
    comparison_time = max(now, node.planned_end) if not _node_done(node) else node.planned_end
    buffer_days = days_available(comparison_time, safe_deadline, rules.duration_mode)
    if _node_done(node):
        return RiskLevel.NORMAL, [], buffer_days

    level = RiskLevel.NORMAL
    reasons: List[str] = []
    if buffer_days < 0:
        level = RiskLevel.SEVERE
        reasons.append("节点延期已经耗尽全部缓冲")
    elif now > node.planned_end:
        level = RiskLevel.WARNING
        reasons.append("节点延期但仍有缓冲")

    if node.planned_start is not None and event.duration > 0:
        planned_duration = days_available(
            node.planned_start, node.planned_end, rules.duration_mode
        )
        if planned_duration < event.duration:
            level = RiskLevel.SEVERE
            reasons.append(f"{event.label}计划测试周期低于最低要求")

    if (
        node is first_unfinished
        and node.planned_start is not None
        and node.planned_start <= now
    ):
        update_reference = max(
            node.updated_at or node.planned_start,
            node.planned_start,
        )
        if days_available(update_reference, now, "workday") >= 2:
            level = max(level, RiskLevel.WARNING)
            reasons.append("连续2个工作日没有进展更新")

    if (
        event.duration > 0
        and node.work_type == "测试"
        and node.planned_start is None
    ):
        latest_start = subtract_days(safe_deadline, event.duration, rules.duration_mode)
        if days_available(now, latest_start, rules.duration_mode) <= 2:
            level = max(level, RiskLevel.WARNING)
            reasons.append("测试排期缺少计划开始时间")

    return level, _deduplicate(reasons), buffer_days


def _evaluate_blockers(
    blockers: Sequence[Blocker],
    nodes: Sequence[DeliveryNode],
    now: datetime,
) -> Tuple[RiskLevel, List[str], List[str]]:
    level = RiskLevel.NORMAL
    reasons: List[str] = []
    affected_domains: List[str] = []
    node_domains = {node.record_id: node.domain for node in nodes}

    for blocker in blockers:
        if _blocker_done(blocker):
            continue
        blocker_is_risky = False
        if blocker.planned_resolution_at < now:
            blocker_is_risky = True
            if blocker.affects_merge:
                level = RiskLevel.SEVERE
                reasons.append("影响合板的阻塞项已超期")
            else:
                level = max(level, RiskLevel.WARNING)
                reasons.append("阻塞项已超期")
        else:
            due_in = days_available(now, blocker.planned_resolution_at, "workday")
            if 0 <= due_in <= 1:
                level = max(level, RiskLevel.WARNING)
                reasons.append("阻塞项将在1个工作日内到期")
                blocker_is_risky = True
        if blocker_is_risky and blocker.node_record_id in node_domains:
            affected_domains.append(node_domains[blocker.node_record_id])

    return level, _deduplicate(reasons), _deduplicate(affected_domains)


def _evaluate_server_launch(
    requirement: Requirement,
    nodes: Sequence[DeliveryNode],
    rules: EffectiveRules,
    now: datetime,
) -> Tuple[RiskLevel, List[str]]:
    if requirement.launch_at is None or not any(
        node.domain == "服务端" for node in nodes
    ):
        return RiskLevel.NORMAL, []

    level = RiskLevel.NORMAL
    reasons: List[str] = []
    if requirement.launch_at.weekday() not in rules.launch_weekdays:
        level = RiskLevel.SEVERE
        reasons.append("服务端上线日期不符合允许星期")

    cutoff = _parse_cutoff(rules.launch_cutoff)
    if requirement.launch_at.timetz().replace(tzinfo=None) > cutoff:
        level = RiskLevel.SEVERE
        reasons.append(f"服务端上线时间晚于{rules.launch_cutoff}")

    checklist_deadline = subtract_days(
        requirement.launch_at, rules.checklist_days_before, "natural"
    )
    if now.date() >= checklist_deadline.date():
        checklist_nodes = [node for node in nodes if _is_checklist(node)]
        if not checklist_nodes or any(not _node_done(node) for node in checklist_nodes):
            level = RiskLevel.SEVERE
            reasons.append("服务端上线 Checklist 未完成")

    return level, _deduplicate(reasons)


def _build_events(
    domain: str, nodes: Sequence[DeliveryNode], rules: EffectiveRules
) -> List[_Event]:
    at_first = ceil(rules.at_days / 2)
    at_second = rules.at_days - at_first
    pv_first = ceil(rules.pv_days / 2)
    pv_second = rules.pv_days - pv_first
    events: List[_Event] = []

    for node in nodes:
        order, label, duration = _node_stage(
            node, at_first, at_second, pv_first, pv_second, rules.regression_days
        )
        events.append(_Event(order=order, duration=duration, label=label, node=node))

    stages = {event.label for event in events}
    has_at = "AT1" in stages or "AT2" in stages
    has_pv = "PV1" in stages or "PV2" in stages
    has_test_flow = has_at or has_pv or "线上回归" in stages

    if has_at and "AT1" not in stages and at_first:
        events.append(_Event(_AT1_ORDER, at_first, "AT1"))
    if has_at and "AT2" not in stages and at_second:
        events.append(_Event(_AT2_ORDER, at_second, "AT2"))
    if has_pv and "PV1" not in stages and pv_first:
        events.append(_Event(_PV1_ORDER, pv_first, "PV1"))
    if has_pv and "PV2" not in stages and pv_second:
        events.append(_Event(_PV2_ORDER, pv_second, "PV2"))
    if has_pv and rules.bugfix_days:
        events.append(_Event(_BUGFIX_ORDER, rules.bugfix_days, "Bug修复预留"))
    special_days = rules.special_days.get(domain, 0)
    if special_days:
        events.append(_Event(_SPECIAL_ORDER, special_days, f"{domain}专项测试"))
    if has_test_flow and "线上回归" not in stages and rules.regression_days:
        events.append(_Event(_REGRESSION_ORDER, rules.regression_days, "线上回归"))

    return sorted(events, key=_event_sort_key)


def _node_stage(
    node: DeliveryNode,
    at_first: int,
    at_second: int,
    pv_first: int,
    pv_second: int,
    regression_days: int,
) -> Tuple[float, str, int]:
    normalized = "".join(node.name.upper().split())
    if "AT1" in normalized or ("AT" in normalized and "第一轮" in node.name):
        return _AT1_ORDER, "AT1", at_first
    if "AT2" in normalized or ("AT" in normalized and "第二轮" in node.name):
        return _AT2_ORDER, "AT2", at_second
    if "PV1" in normalized or ("PV" in normalized and "第一轮" in node.name):
        return _PV1_ORDER, "PV1", pv_first
    if "PV2" in normalized or ("PV" in normalized and "第二轮" in node.name):
        return _PV2_ORDER, "PV2", pv_second
    if "线上回归" in node.name:
        return _REGRESSION_ORDER, "线上回归", regression_days

    process_name = _matching_process_name(node)
    return float(_PROCESS_ORDER.get(process_name, len(DEFAULT_PROCESS_NODES))), process_name, 0


def _matching_process_name(node: DeliveryNode) -> str:
    if node.name in _PROCESS_ORDER:
        return node.name
    if node.work_type == "研发" or "开发" in node.name:
        return "各端开发"
    if node.work_type == "联调" or "联调" in node.name:
        return "联调"
    if "提测" in node.name:
        return "提测"
    return node.name


def _predict_domain_completion(
    events: Sequence[_Event], rules: EffectiveRules, now: datetime
) -> Optional[datetime]:
    if not events:
        return None
    cursor = now
    has_unfinished = False
    for event in events:
        if not _event_is_unfinished(event, events):
            continue
        has_unfinished = True
        if event.node is None:
            cursor = add_days(cursor, event.duration, rules.duration_mode)
            continue
        planned_start = event.node.planned_start or cursor
        start = max(cursor, planned_start)
        minimum_end = add_days(start, event.duration, rules.duration_mode)
        cursor = max(minimum_end, event.node.planned_end)
    if has_unfinished:
        return cursor
    completed = [
        event.node.actual_end or event.node.planned_end
        for event in events
        if event.node is not None
    ]
    return max(completed) if completed else None


def _predict_node_completion(
    event: _Event, rules: EffectiveRules, now: datetime
) -> datetime:
    node = event.node
    if node is None:
        raise ValueError("node event is required")
    if _node_done(node):
        return node.actual_end or node.planned_end
    start = max(now, node.planned_start or now)
    return max(add_days(start, event.duration, rules.duration_mode), node.planned_end)


def _downstream_duration(events: Sequence[_Event], order: float) -> int:
    return sum(
        event.duration
        for event in events
        if event.order > order and _event_is_unfinished(event, events)
    )


def _event_is_unfinished(event: _Event, events: Sequence[_Event]) -> bool:
    if event.node is not None:
        return not _node_done(event.node)
    if any(
        candidate.node is not None
        and candidate.order <= event.order
        and not _node_done(candidate.node)
        for candidate in events
    ):
        return True
    if any(
        candidate.node is not None
        and candidate.order > event.order
        and _node_started_or_done(candidate.node)
        for candidate in events
    ):
        return False
    return any(candidate.node is not None for candidate in events)


def _node_done(node: DeliveryNode) -> bool:
    return node.actual_end is not None or node.status in _COMPLETED_NODE_STATUSES


def _node_started_or_done(node: DeliveryNode) -> bool:
    return node.status != NodeStatus.NOT_STARTED or _node_done(node)


def _blocker_done(blocker: Blocker) -> bool:
    return (
        blocker.actual_resolution_at is not None
        or blocker.status in _COMPLETED_BLOCKER_STATUSES
    )


def _is_checklist(node: DeliveryNode) -> bool:
    return node.domain == "服务端" and "checklist" in node.name.lower()


def _evaluate_checklist_node(
    node: DeliveryNode,
    requirement: Requirement,
    rules: EffectiveRules,
    now: datetime,
) -> NodeRisk:
    safe_deadline = (
        subtract_days(
            requirement.launch_at,
            rules.checklist_days_before,
            "natural",
        )
        if requirement.launch_at is not None
        else None
    )
    reasons = (
        ["服务端上线 Checklist 未完成"]
        if _checklist_is_due(node, requirement, rules, now)
        else []
    )
    return NodeRisk(
        node_record_id=node.record_id,
        requirement_id=node.requirement_id,
        node_name=node.name,
        domain=node.domain,
        owner_id=node.owner_id,
        owner_name=node.owner_name,
        level=RiskLevel.SEVERE if reasons else RiskLevel.NORMAL,
        predicted_completion=None,
        safe_deadline=safe_deadline,
        buffer_days=None,
        reasons=reasons,
        actions=[],
    )


def _checklist_is_due(
    node: DeliveryNode,
    requirement: Requirement,
    rules: EffectiveRules,
    now: datetime,
) -> bool:
    if not _is_checklist(node) or _node_done(node) or requirement.launch_at is None:
        return False
    deadline = subtract_days(
        requirement.launch_at, rules.checklist_days_before, "natural"
    )
    return now.date() >= deadline.date()


def _minimum_window_reason(domain: str, events: Sequence[_Event]) -> str:
    labels = {
        event.label for event in events if _event_is_unfinished(event, events)
    }
    if "AT1" in labels or "AT2" in labels:
        return f"{domain} AT 最低测试周期无法容纳合板窗口"
    if "PV1" in labels or "PV2" in labels:
        return f"{domain} PV 最低测试周期无法容纳合板窗口"
    if "线上回归" in labels:
        return f"{domain}线上回归最低周期无法容纳合板窗口"
    return f"{domain}最低所需周期无法容纳合板窗口"


def _group_nodes_by_domain(
    nodes: Sequence[DeliveryNode],
) -> Iterable[Tuple[str, List[DeliveryNode]]]:
    grouped: Dict[str, List[DeliveryNode]] = {}
    for node in nodes:
        grouped.setdefault(node.domain, []).append(node)
    return grouped.items()


def _event_sort_key(event: _Event) -> Tuple[float, datetime, str]:
    planned_end = event.node.planned_end if event.node is not None else datetime.max
    if planned_end.tzinfo is not None:
        planned_end = planned_end.replace(tzinfo=None)
    name = event.node.name if event.node is not None else event.label
    return event.order, planned_end, name


def _parse_cutoff(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _override(project_config: Optional[ProjectConfig], field: str, fallback: int) -> int:
    if project_config is None:
        return fallback
    value = getattr(project_config, field)
    return fallback if value is None else value


def _is_eligible(requirement: Requirement) -> bool:
    return (
        requirement.briefing_completed
        and requirement.notification_enabled
        and not requirement.archived
    )


def _deduplicate(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(value for value in values if value))
