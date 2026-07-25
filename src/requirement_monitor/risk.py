import re
from dataclasses import dataclass
from datetime import datetime, time
from math import ceil
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from requirement_monitor.calendar import DayMode, add_days, days_available, subtract_days
from requirement_monitor.models import (
    BaseConfig,
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
_PV1_ORDER = _PROCESS_ORDER["PV 测试第一轮"]
_REGRESSION_ORDER = _PROCESS_ORDER["线上回归"]
_PhaseRank = Tuple[int, int]
_BUGFIX_ORDER = (_REGRESSION_ORDER, -2)
_SPECIAL_ORDER = (_REGRESSION_ORDER, -1)


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
    process_order: Dict[str, int]
    disabled_stages: Set[str]


@dataclass(frozen=True)
class _Event:
    order: _PhaseRank
    duration: int
    label: str
    node: Optional[DeliveryNode] = None


@dataclass(frozen=True)
class _PhaseEvent:
    order: _PhaseRank
    remaining_duration: int
    label: str
    events: Tuple[_Event, ...]
    unfinished: bool


@dataclass(frozen=True)
class _DomainEvaluation:
    domain: str
    predicted_completion: Optional[datetime]
    buffer_days: Optional[int]
    level: RiskLevel
    reasons: List[str]
    node_risks: List[NodeRisk]


def resolve_effective_rules(
    fixed: FixedRules,
    project_config: Optional[ProjectConfig],
    base_configs: Optional[Sequence[BaseConfig]] = None,
) -> EffectiveRules:
    duration_mode: DayMode = (
        project_config.duration_mode if project_config is not None else "workday"
    )
    fixed_at_days = (
        fixed.at_workdays if duration_mode == "workday" else fixed.at_natural_days
    )

    stage_configs = sorted(
        (
            config
            for config in (base_configs or [])
            if config.config_type == "环节"
        ),
        key=lambda config: (config.sort_order, config.name),
    )
    enabled_stages = [config.name for config in stage_configs if config.enabled]
    process_names = enabled_stages or list(DEFAULT_PROCESS_NODES)
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
        process_order={name: index for index, name in enumerate(process_names)},
        disabled_stages={
            config.name for config in stage_configs if not config.enabled
        },
    )


def evaluate_requirement(
    requirement: Requirement,
    nodes: Sequence[DeliveryNode],
    blockers: Sequence[Blocker],
    fixed_rules: FixedRules,
    now: datetime,
    project_config: Optional[ProjectConfig] = None,
    base_configs: Optional[Sequence[BaseConfig]] = None,
) -> Optional[RequirementRisk]:
    if not _is_eligible(requirement):
        return None
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    effective = resolve_effective_rules(fixed_rules, project_config, base_configs)
    relevant_nodes = [
        node
        for node in nodes
        if node.requirement_id == requirement.requirement_id
        and node.name not in effective.disabled_stages
        and _configured_stage_name(node) not in effective.disabled_stages
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
        target_version=requirement.target_version,
        merge_at=requirement.merge_at,
        launch_at=requirement.launch_at,
        project_owner_id=requirement.project_owner_id,
        project_owner_name=requirement.project_owner_name,
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
        blockers=list(relevant_blockers),
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
    phases = _build_phase_events(events, rules, now)
    downstream_durations = _downstream_durations(phases)
    node_risks: List[NodeRisk] = []
    reasons: List[str] = []
    level = RiskLevel.NORMAL
    first_unfinished = next(
        (
            event.node
            for phase in phases
            if phase.unfinished
            for event in phase.events
            if event.node is not None and not _node_done(event.node)
        ),
        None,
    )

    for phase in phases:
        safe_deadline = subtract_days(
            requirement.merge_at,
            downstream_durations[phase.order],
            rules.duration_mode,
        )
        for event in phase.events:
            if event.node is None:
                continue
            node_level, node_reasons, node_buffer = _evaluate_node(
                event,
                safe_deadline,
                first_unfinished,
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
                    planned_end=event.node.planned_end,
                    status=event.node.status,
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

    for phase in phases:
        if (
            any(event.node is not None for event in phase.events)
            or phase.remaining_duration <= 0
            or phase.label not in {"AT1", "AT2", "PV1", "PV2", "线上回归"}
            or not phase.unfinished
        ):
            continue
        safe_deadline = subtract_days(
            requirement.merge_at,
            downstream_durations[phase.order],
            rules.duration_mode,
        )
        latest_start = subtract_days(
            safe_deadline, phase.remaining_duration, rules.duration_mode
        )
        if days_available(now, latest_start, rules.duration_mode) <= 2:
            level = max(level, RiskLevel.WARNING)
            reasons.append("测试排期缺少计划开始时间")

    if not phases:
        return _DomainEvaluation(
            domain=domain,
            predicted_completion=None,
            buffer_days=None,
            level=level,
            reasons=_deduplicate(reasons),
            node_risks=node_risks,
        )

    predicted_completion = _predict_domain_completion(phases, rules, now)
    remaining_duration = sum(phase.remaining_duration for phase in phases)
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
        reasons.append(_minimum_window_reason(domain, phases))
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
    rules: EffectiveRules,
    now: datetime,
) -> Tuple[RiskLevel, List[str], int]:
    node = event.node
    if node is None:
        raise ValueError("node event is required")
    node_done = _node_done(node)
    comparison_time = (
        max(now, node.planned_end)
        if not node_done
        else node.actual_end or node.planned_end
    )
    buffer_days = days_available(comparison_time, safe_deadline, rules.duration_mode)
    level = RiskLevel.NORMAL
    reasons: List[str] = []
    if node.planned_start is not None and event.duration > 0:
        window_end = (
            node.actual_end or node.planned_end if node_done else node.planned_end
        )
        planned_duration = days_available(
            node.planned_start, window_end, rules.duration_mode
        )
        if planned_duration < event.duration:
            level = RiskLevel.SEVERE
            reasons.append(f"{event.label}计划测试周期低于最低要求")

    if node_done:
        return level, _deduplicate(reasons), buffer_days

    if buffer_days < 0:
        level = RiskLevel.SEVERE
        reasons.append("节点延期已经耗尽全部缓冲")
    elif now > node.planned_end:
        level = RiskLevel.WARNING
        reasons.append("节点延期但仍有缓冲")

    staleness_enabled = node.status == NodeStatus.IN_PROGRESS or (
        node.planned_start is not None and node.planned_start <= now
    )
    if node is first_unfinished and staleness_enabled:
        update_references = [
            value for value in (node.updated_at, node.planned_start) if value is not None
        ]
        if (
            update_references
            and days_available(max(update_references), now, "workday") >= 2
        ):
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
    at_order = rules.process_order.get("AT 测试第一轮", _AT1_ORDER)
    pv_order = rules.process_order.get("PV 测试第一轮", _PV1_ORDER)
    regression_order = rules.process_order.get("线上回归", _REGRESSION_ORDER)

    for node in nodes:
        order, label, duration = _node_stage(
            node,
            at_first,
            at_second,
            pv_first,
            pv_second,
            rules.regression_days,
            rules.duration_mode,
            rules.process_order,
        )
        events.append(_Event(order=order, duration=duration, label=label, node=node))

    stages = {event.label for event in events}
    has_at = any(_is_round_label(stage, "AT") for stage in stages)
    has_pv = any(_is_round_label(stage, "PV") for stage in stages)
    has_test_flow = has_at or has_pv or "线上回归" in stages

    if has_at and "AT1" not in stages and at_first:
        events.append(_Event((at_order, 1), at_first, "AT1"))
    if has_at and "AT2" not in stages and at_second:
        events.append(_Event((at_order, 2), at_second, "AT2"))
    if has_pv and "PV1" not in stages and pv_first:
        events.append(_Event((pv_order, 1), pv_first, "PV1"))
    if has_pv and "PV2" not in stages and pv_second:
        events.append(_Event((pv_order, 2), pv_second, "PV2"))
    if has_pv and rules.bugfix_days:
        events.append(_Event((regression_order, -2), rules.bugfix_days, "Bug修复预留"))
    special_days = rules.special_days.get(domain, 0)
    if special_days:
        events.append(_Event((regression_order, -1), special_days, f"{domain}专项测试"))
    if has_test_flow and "线上回归" not in stages and rules.regression_days:
        events.append(
            _Event((regression_order, 0), rules.regression_days, "线上回归")
        )

    return sorted(events, key=_event_sort_key)


def _node_stage(
    node: DeliveryNode,
    at_first: int,
    at_second: int,
    pv_first: int,
    pv_second: int,
    regression_days: int,
    duration_mode: DayMode,
    process_order: Mapping[str, int],
) -> Tuple[_PhaseRank, str, int]:
    normalized = "".join(node.name.upper().split())
    round_stage = _test_round_stage(node, normalized)
    if round_stage is not None:
        family, round_number = round_stage
        if family == "AT":
            duration = (
                at_first
                if round_number == 1
                else at_second
                if round_number == 2
                else _extra_round_duration(node, duration_mode)
            )
            return (
                process_order.get("AT 测试第一轮", _AT1_ORDER),
                round_number,
            ), f"AT{round_number}", duration
        duration = (
            pv_first
            if round_number == 1
            else pv_second
            if round_number == 2
            else _extra_round_duration(node, duration_mode)
        )
        return (
            process_order.get("PV 测试第一轮", _PV1_ORDER),
            round_number,
        ), f"PV{round_number}", duration
    if "线上回归" in node.name:
        return (
            process_order.get("线上回归", _REGRESSION_ORDER),
            0,
        ), "线上回归", regression_days

    process_name = _matching_process_name(node)
    return (
        (process_order.get(process_name, len(process_order)), 0),
        process_name,
        0,
    )


def _test_round_stage(
    node: DeliveryNode, normalized_name: str
) -> Optional[Tuple[str, int]]:
    numeric_match = re.search(r"(AT|PV)(\d+)", normalized_name)
    if numeric_match is not None:
        return numeric_match.group(1), int(numeric_match.group(2))

    for family in ("AT", "PV"):
        if family not in normalized_name:
            continue
        chinese_match = re.search(r"第([一二三四五六七八九十]+)轮", node.name)
        if chinese_match is not None:
            round_number = _chinese_round_number(chinese_match.group(1))
            if round_number is not None:
                return family, round_number
    return None


def _chinese_round_number(value: str) -> Optional[int]:
    digits = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value in digits:
        return digits[value]
    if value == "十":
        return 10
    if "十" in value:
        tens, ones = value.split("十", 1)
        tens_value = digits.get(tens, 1) if tens else 1
        ones_value = digits.get(ones, 0) if ones else 0
        return tens_value * 10 + ones_value
    return None


def _extra_round_duration(node: DeliveryNode, duration_mode: DayMode) -> int:
    if node.planned_start is None:
        return 1
    return max(1, days_available(node.planned_start, node.planned_end, duration_mode))


def _is_round_label(label: str, family: str) -> bool:
    return label.startswith(family) and label[len(family) :].isdigit()


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


def _configured_stage_name(node: DeliveryNode) -> str:
    normalized = "".join(node.name.upper().split())
    round_stage = _test_round_stage(node, normalized)
    if round_stage is not None:
        family, round_number = round_stage
        if family == "AT" and round_number in (1, 2):
            return "AT 测试第{}轮".format("一" if round_number == 1 else "二")
        if family == "PV" and round_number in (1, 2):
            return "PV 测试第{}轮".format("一" if round_number == 1 else "二")
    return _matching_process_name(node)


def _build_phase_events(
    events: Sequence[_Event], rules: EffectiveRules, now: datetime
) -> List[_PhaseEvent]:
    grouped: Dict[_PhaseRank, List[_Event]] = {}
    for event in events:
        grouped.setdefault(event.order, []).append(event)

    grouped_events = [
        (order, tuple(phase_events))
        for order, phase_events in sorted(grouped.items())
    ]
    node_done = {
        id(event.node): _node_done(event.node)
        for event in events
        if event.node is not None
    }
    node_started_or_done = {
        id(event.node): event.node.status != NodeStatus.NOT_STARTED
        or node_done[id(event.node)]
        for event in events
        if event.node is not None
    }
    real_unfinished = [
        any(
            event.node is not None and not node_done[id(event.node)]
            for event in phase_events
        )
        for _, phase_events in grouped_events
    ]
    real_started_or_done = [
        any(
            event.node is not None and node_started_or_done[id(event.node)]
            for event in phase_events
        )
        for _, phase_events in grouped_events
    ]
    prefix_unfinished: List[bool] = []
    has_unfinished = False
    for unfinished in real_unfinished:
        has_unfinished = has_unfinished or unfinished
        prefix_unfinished.append(has_unfinished)
    suffix_started = [False] * (len(grouped_events) + 1)
    for index in range(len(grouped_events) - 1, -1, -1):
        suffix_started[index] = suffix_started[index + 1] or real_started_or_done[index]
    has_real_node = bool(node_done)

    phases: List[_PhaseEvent] = []
    for index, (order, phase_events) in enumerate(grouped_events):
        real_events = [event for event in phase_events if event.node is not None]
        if real_events:
            unfinished = real_unfinished[index]
            remaining_duration = max(
                (
                    _remaining_event_duration(event, rules, now)
                    for event in real_events
                    if not node_done[id(event.node)]
                ),
                default=0,
            )
        else:
            unfinished = prefix_unfinished[index] or (
                not suffix_started[index + 1] and has_real_node
            )
            remaining_duration = (
                max(event.duration for event in phase_events) if unfinished else 0
            )
        phases.append(
            _PhaseEvent(
                order=order,
                remaining_duration=remaining_duration,
                label=phase_events[0].label,
                events=phase_events,
                unfinished=unfinished,
            )
        )
    return phases


def _remaining_event_duration(
    event: _Event, rules: EffectiveRules, now: datetime
) -> int:
    node = event.node
    if node is None:
        return event.duration
    if _node_done(node):
        return 0
    if node.status != NodeStatus.IN_PROGRESS or node.planned_start is None:
        return event.duration
    consumed = max(
        0, days_available(node.planned_start, now, rules.duration_mode)
    )
    return max(0, event.duration - consumed)


def _downstream_durations(phases: Sequence[_PhaseEvent]) -> Dict[_PhaseRank, int]:
    downstream: Dict[_PhaseRank, int] = {}
    suffix_duration = 0
    for phase in reversed(phases):
        downstream[phase.order] = suffix_duration
        suffix_duration += phase.remaining_duration
    return downstream


def _predict_domain_completion(
    phases: Sequence[_PhaseEvent], rules: EffectiveRules, now: datetime
) -> Optional[datetime]:
    if not phases:
        return None
    cursor = now
    has_unfinished = any(phase.unfinished for phase in phases)
    for phase in phases:
        if not phase.unfinished:
            continue
        real_events = [event for event in phase.events if event.node is not None]
        if not real_events:
            cursor = add_days(cursor, phase.remaining_duration, rules.duration_mode)
            continue
        phase_completions = []
        for event in real_events:
            node = event.node
            if node is None:
                continue
            if _node_done(node):
                phase_completions.append(node.actual_end or node.planned_end)
                continue
            planned_start = node.planned_start or cursor
            start = max(cursor, planned_start)
            minimum_end = add_days(
                start,
                _remaining_event_duration(event, rules, now),
                rules.duration_mode,
            )
            phase_completions.append(max(minimum_end, node.planned_end))
        if phase_completions:
            cursor = max(cursor, max(phase_completions))
    if has_unfinished:
        return cursor
    completed = [
        event.node.actual_end or event.node.planned_end
        for phase in phases
        for event in phase.events
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
    return max(
        add_days(
            start,
            _remaining_event_duration(event, rules, now),
            rules.duration_mode,
        ),
        node.planned_end,
    )


def _node_done(node: DeliveryNode) -> bool:
    return node.actual_end is not None or node.status in _COMPLETED_NODE_STATUSES


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
        planned_end=node.planned_end,
        status=node.status,
        level=RiskLevel.SEVERE if reasons else RiskLevel.NORMAL,
        predicted_completion=None,
        safe_deadline=safe_deadline,
        buffer_days=None,
        reasons=reasons,
        actions=[],
        planned_end_is_system_managed=True,
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


def _minimum_window_reason(domain: str, phases: Sequence[_PhaseEvent]) -> str:
    labels = {phase.label for phase in phases if phase.unfinished}
    if any(_is_round_label(label, "AT") for label in labels):
        return f"{domain} AT 最低测试周期无法容纳合板窗口"
    if any(_is_round_label(label, "PV") for label in labels):
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


def _event_sort_key(event: _Event) -> Tuple[_PhaseRank, datetime, str]:
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
