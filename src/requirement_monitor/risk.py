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
    Person,
    ProjectConfig,
    Requirement,
    RequirementRisk,
    RiskFinding,
    RiskLevel,
    ScheduleFormula,
    ScheduleFormulaTerm,
)
from requirement_monitor.schema import DEFAULT_PROCESS_NODES


_COMPLETED_NODE_STATUSES = {NodeStatus.COMPLETED, NodeStatus.SKIPPED}
_COMPLETED_BLOCKER_STATUSES = {"已解决", "已完成", "关闭", "已关闭", "已取消"}
_PROCESS_ORDER = {name: index for index, name in enumerate(DEFAULT_PROCESS_NODES)}
_AT1_ORDER = _PROCESS_ORDER["AT 测试第一轮"]
_PV1_ORDER = _PROCESS_ORDER["PV 测试第一轮"]
_REGRESSION_ORDER = _PROCESS_ORDER["线上回归"]
_SHARED_REGRESSION_DOMAINS = {"平台", "公共流程"}
_PhaseRank = Tuple[int, int]
_SPECIAL_ORDER = (_REGRESSION_ORDER, -1)


@dataclass(frozen=True)
class EffectiveRules:
    duration_mode: DayMode
    stage_days: Dict[str, int]
    regression_days: int
    launch_weekdays: Set[int]
    launch_cutoff: str
    checklist_days_before: int
    process_order: Dict[str, int]
    has_stage_configuration: bool
    enabled_stages: Set[str]
    disabled_stages: Set[str]
    test_role_states: Dict[str, bool]


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
    findings: List[RiskFinding]
    node_risks: List[NodeRisk]
    schedule_formula: Optional[ScheduleFormula]


@dataclass(frozen=True)
class _NodeEvaluation:
    level: RiskLevel
    reasons: List[str]
    buffer_days: int
    findings: List[RiskFinding]


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
    at1_default = fixed.at1_days or ceil(fixed_at_days / 2)
    at2_default = fixed.at2_days or fixed_at_days - ceil(fixed_at_days / 2)
    pv1_default = fixed.pv1_days or fixed.pv_days
    pv2_default = fixed.pv2_days or 2
    stage_days = {
        "AT 测试第一轮": _override(project_config, "at1_days", at1_default),
        "AT 测试第二轮": _override(project_config, "at2_days", at2_default),
        "PV 测试第一轮": _override(project_config, "pv1_days", pv1_default),
        "PV 测试第二轮": _override(project_config, "pv2_days", pv2_default),
        "线上回归": _override(
            project_config, "regression_days", fixed.regression_days
        ),
    }

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
        stage_days=stage_days,
        regression_days=_override(
            project_config, "regression_days", fixed.regression_days
        ),
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
        has_stage_configuration=bool(stage_configs),
        enabled_stages=set(enabled_stages),
        disabled_stages={
            config.name for config in stage_configs if not config.enabled
        },
        test_role_states={
            config.name: config.enabled
            for config in (base_configs or [])
            if config.config_type == "测试角色"
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
    configured_stage_names = _configured_stage_names(effective)
    relevant_nodes = [
        node
        for node in nodes
        if node.requirement_id == requirement.requirement_id
        and node.name not in effective.disabled_stages
        and _configured_stage_name(node, configured_stage_names)
        not in effective.disabled_stages
        and _test_role_enabled(node, effective)
    ]
    relevant_blockers = [
        blocker
        for blocker in blockers
        if blocker.requirement_id == requirement.requirement_id
    ]
    effective_current_stage = _derive_current_stage(
        requirement.current_stage,
        relevant_nodes,
        effective,
    )
    if effective_current_stage != requirement.current_stage:
        requirement = requirement.model_copy(
            update={"current_stage": effective_current_stage}
        )
    effective_launch_at = _effective_server_launch_at(requirement, relevant_nodes)

    domain_groups = list(_group_nodes_by_domain(relevant_nodes))
    if not domain_groups:
        domain_groups = [("项目排期", [])]
    domain_results = [
        _evaluate_domain(requirement, domain, domain_nodes, effective, now)
        for domain, domain_nodes in domain_groups
    ]
    reasons: List[str] = []
    findings: List[RiskFinding] = []
    level = RiskLevel.NORMAL
    affected_domains: List[str] = []

    for domain_result in domain_results:
        level = max(level, domain_result.level)
        reasons.extend(domain_result.reasons)
        findings.extend(domain_result.findings)
        if domain_result.level > RiskLevel.NORMAL:
            affected_domains.append(domain_result.domain)

    blocker_level, blocker_reasons, blocker_domains, blocker_findings = _evaluate_blockers(
        relevant_blockers, relevant_nodes, now
    )
    level = max(level, blocker_level)
    reasons.extend(blocker_reasons)
    findings.extend(blocker_findings)
    affected_domains.extend(blocker_domains)

    launch_level, launch_reasons, launch_findings = _evaluate_server_launch(
        effective_launch_at, relevant_nodes, effective, now
    )
    level = max(level, launch_level)
    reasons.extend(launch_reasons)
    findings.extend(launch_findings)
    if launch_level > RiskLevel.NORMAL:
        affected_domains.append("服务端")

    (
        gate_level,
        gate_reasons,
        gate_domains,
        gate_failures,
        gate_findings,
        gate_reminders,
    ) = _evaluate_test_gates(requirement, relevant_nodes, effective)
    level = max(level, gate_level)
    reasons.extend(gate_reasons)
    findings.extend(gate_findings)
    affected_domains.extend(gate_domains)
    node_risks = [
        node_risk
        for domain_result in domain_results
        for node_risk in domain_result.node_risks
    ]
    for node_risk in node_risks:
        failure = gate_failures.get(node_risk.node_record_id)
        if failure:
            node_risk.level = max(node_risk.level, RiskLevel.SEVERE)
            node_reason, gate_finding = failure
            node_risk.reasons = _deduplicate(node_risk.reasons + [node_reason])
            node_risk.findings = _deduplicate_findings(
                node_risk.findings + [gate_finding]
            )

    current_stage_reminder = _current_stage_reminder(
        requirement, relevant_nodes, effective
    )
    process_reminders = _sorted_process_reminders(
        ([current_stage_reminder] if current_stage_reminder else [])
        + gate_reminders,
        effective,
    )

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

    critical_domain_result = max(
        (
            result
            for result in domain_results
            if result.predicted_completion is not None
        ),
        key=lambda result: result.predicted_completion,
        default=None,
    )

    return RequirementRisk(
        requirement_record_id=requirement.record_id,
        requirement_id=requirement.requirement_id,
        requirement_name=requirement.name,
        project=requirement.project,
        current_stage=requirement.current_stage,
        target_version=requirement.target_version,
        requirement_doc_url=requirement.requirement_doc_url,
        meego_url=requirement.meego_url,
        translation_url=requirement.translation_url,
        merge_at=requirement.merge_at,
        launch_at=effective_launch_at,
        project_owner_id=requirement.project_owner_id,
        project_owner_name=requirement.project_owner_name,
        level=level,
        predicted_completion=max(predictions) if predictions else None,
        schedule_formula=(
            critical_domain_result.schedule_formula
            if critical_domain_result is not None
            else None
        ),
        buffer_days=min(buffers) if buffers else None,
        affected_domains=_deduplicate(affected_domains),
        reasons=_deduplicate(reasons),
        findings=_deduplicate_findings(findings),
        stage_order=dict(effective.process_order),
        process_reminders=process_reminders,
        actions=[],
        project_notes=project_config.llm_notes if project_config is not None else "",
        requirement_notes=requirement.requirement_notes,
        sensitive_people=_requirement_people(
            requirement, relevant_nodes, relevant_blockers
        ),
        node_risks=node_risks,
        blockers=list(relevant_blockers),
    )


def _stage_key(name: str) -> str:
    normalized = "".join(name.upper().split())
    round_stage = _round_stage_from_name(name, normalized)
    if round_stage is not None:
        return "{}{}".format(*round_stage)
    if "服务端上线" in name or "CHECKLIST" in normalized:
        return "服务端上线"
    if "线上回归" in name:
        return "线上回归"
    if "多语言翻译" in name:
        return "多语言翻译"
    return name


def _effective_server_launch_at(
    requirement: Requirement,
    nodes: Sequence[DeliveryNode],
) -> Optional[datetime]:
    explicit_launch_times = [
        node.planned_end
        for node in nodes
        if node.domain == "服务端"
        and _stage_key(node.name) == "服务端上线"
        and not _is_checklist(node)
        and node.planned_end is not None
    ]
    if explicit_launch_times:
        return max(explicit_launch_times)
    return requirement.launch_at


def _current_stage_reminder(
    requirement: Requirement,
    nodes: Sequence[DeliveryNode],
    rules: EffectiveRules,
) -> Optional[str]:
    current = requirement.current_stage
    if any(_stage_key(node.name) == current for node in nodes):
        return None
    normalized_current = _stage_display_to_process(current)
    if any(_matching_process_name(node) == normalized_current for node in nodes):
        return None
    return current


def _sorted_process_reminders(
    reminders: Sequence[str], rules: EffectiveRules
) -> List[str]:
    unique = set(reminders)
    return sorted(
        unique,
        key=lambda stage: (
            rules.process_order.get(
                _stage_display_to_process(stage), len(rules.process_order)
            ),
            stage,
        ),
    )


def _derive_current_stage(
    current: str,
    nodes: Sequence[DeliveryNode],
    rules: EffectiveRules,
) -> str:
    current_process = _stage_display_to_process(current)
    current_order = rules.process_order.get(current_process)
    if current_order is None:
        return current

    configured_names = _configured_stage_names(rules)
    current_nodes = [
        node
        for node in nodes
        if _configured_stage_name(node, configured_names) == current_process
    ]
    if not current_nodes or any(not _node_done(node) for node in current_nodes):
        return current

    future_stages = sorted(
        (stage_order, stage_name)
        for stage_name, stage_order in rules.process_order.items()
        if stage_order > current_order
    )
    for _, stage_name in future_stages:
        stage_nodes = [
            node
            for node in nodes
            if _configured_stage_name(node, configured_names) == stage_name
        ]
        if not stage_nodes or any(not _node_done(node) for node in stage_nodes):
            return stage_name
    if future_stages:
        return future_stages[-1][1]
    return current


def _stage_reached(current: str, target: str, process_order: Mapping[str, int]) -> bool:
    current_order = process_order.get(current)
    target_order = process_order.get(target)
    if current_order is None:
        current_order = process_order.get(_stage_display_to_process(current))
        if current_order is None:
            return False
    if target_order is None:
        target_order = len(process_order)
    return current_order >= target_order


def _stage_display_to_process(stage: str) -> str:
    mapping = {
        "开发": "各端开发",
        "AT1": "AT 测试第一轮",
        "AT2": "AT 测试第二轮",
        "PV1": "PV 测试第一轮",
        "PV2": "PV 测试第二轮",
    }
    return mapping.get(stage, stage)


def _evaluate_test_gates(
    requirement: Requirement,
    nodes: Sequence[DeliveryNode],
    rules: EffectiveRules,
) -> Tuple[
    RiskLevel,
    List[str],
    List[str],
    Dict[str, Tuple[str, RiskFinding]],
    List[RiskFinding],
    List[str],
]:
    test_domains = sorted({node.domain for node in nodes if node.work_type == "测试"})
    current = requirement.current_stage
    at_required = _stage_reached(current, "PV 测试第一轮", rules.process_order)
    has_server = any(node.domain == "服务端" for node in nodes)
    pv_gate_target = "服务端上线" if has_server else "线上回归"
    pv_required = _stage_reached(
        current,
        pv_gate_target,
        rules.process_order,
    )
    regression_required = _stage_reached(current, "版本合入", rules.process_order)
    regression_nodes = [
        node for node in nodes if _stage_key(node.name) == "线上回归"
    ]
    level = RiskLevel.NORMAL
    reasons: List[str] = []
    domains: List[str] = []
    failures: Dict[str, Tuple[str, RiskFinding]] = {}
    findings: List[RiskFinding] = []
    reminders: List[str] = []
    incomplete_regression_domains: Set[str] = set()
    shared_regression_nodes = [
        node
        for node in regression_nodes
        if node.domain in _SHARED_REGRESSION_DOMAINS
    ]
    if regression_required:
        for shared_node in shared_regression_nodes:
            if _node_done(shared_node):
                continue
            level = RiskLevel.SEVERE
            domains.append(shared_node.domain)
            incomplete_regression_domains.add(shared_node.domain)
            reason = "线上回归未通过，不能版本合入"
            reasons.append(reason)
            regression_finding = _make_finding(
                "test_gate.regression_incomplete",
                reason,
                level=RiskLevel.SEVERE,
                source="test_gate",
                stage_refs=["线上回归"],
                domain_refs=[shared_node.domain],
            )
            findings.append(regression_finding)
            _record_gate_failures(
                failures,
                [shared_node],
                "线上回归",
                reason,
                regression_finding,
            )
    for domain in test_domains:
        domain_nodes = [node for node in nodes if node.domain == domain]
        if at_required:
            for stage in ("AT1", "AT2"):
                stage_nodes = [
                    node for node in domain_nodes if _stage_key(node.name) == stage
                ]
                if not stage_nodes:
                    continue
                if not _domain_stage_passed(stage_nodes, stage):
                    level = RiskLevel.SEVERE
                    domains.append(domain)
                    reason = f"{domain} {stage} 测试未通过，不能进入 PV"
                    reasons.append(reason)
                    gate_finding = _make_finding(
                        "test_gate.at_incomplete",
                        "AT 测试未通过，不能进入 PV",
                        level=RiskLevel.SEVERE,
                        source="test_gate",
                        stage_refs=[_phase_display_name(stage)],
                        domain_refs=[domain],
                    )
                    findings.append(gate_finding)
                    _record_gate_failures(
                        failures, domain_nodes, stage, reason, gate_finding
                    )
        if pv_required:
            for stage in ("PV1", "PV2"):
                stage_nodes = [
                    node for node in domain_nodes if _stage_key(node.name) == stage
                ]
                if not stage_nodes:
                    continue
                if not _domain_stage_passed(stage_nodes, stage):
                    level = RiskLevel.SEVERE
                    domains.append(domain)
                    reason = f"{domain} {stage} 测试未通过，不能进入{pv_gate_target}"
                    reasons.append(reason)
                    gate_finding = _make_finding(
                        "test_gate.pv_incomplete",
                        f"PV 测试未通过，不能进入{pv_gate_target}",
                        level=RiskLevel.SEVERE,
                        source="test_gate",
                        stage_refs=[_phase_display_name(stage)],
                        domain_refs=[domain],
                    )
                    findings.append(gate_finding)
                    _record_gate_failures(
                        failures, domain_nodes, stage, reason, gate_finding
                    )
        if regression_required:
            domain_regression_nodes = [
                node
                for node in domain_nodes
                if _stage_key(node.name) == "线上回归"
            ]
            if domain_regression_nodes:
                if not any(
                    not _node_done(node) for node in domain_regression_nodes
                ):
                    continue
                level = RiskLevel.SEVERE
                domains.append(domain)
                incomplete_regression_domains.add(domain)
                reason = f"{domain} 线上回归未通过，不能版本合入"
                reasons.append(reason)
                regression_finding = _make_finding(
                    "test_gate.regression_incomplete",
                    "线上回归未通过，不能版本合入",
                    level=RiskLevel.SEVERE,
                    source="test_gate",
                    stage_refs=["线上回归"],
                    domain_refs=[domain],
                )
                findings.append(regression_finding)
                _record_gate_failures(
                    failures,
                    domain_nodes,
                    "线上回归",
                    reason,
                    regression_finding,
                )
    if regression_required and regression_nodes and not incomplete_regression_domains and any(
        not _node_done(node) for node in regression_nodes
    ):
        level = RiskLevel.SEVERE
        reason = "线上回归未通过，不能版本合入"
        reasons.append(reason)
        findings.append(
            _make_finding(
                "test_gate.regression_incomplete",
                reason,
                level=RiskLevel.SEVERE,
                source="test_gate",
                stage_refs=["线上回归"],
                domain_refs=[node.domain for node in regression_nodes],
            )
        )
    if at_required:
        for stage in ("AT1", "AT2"):
            if not any(_stage_key(node.name) == stage for node in nodes):
                reminders.append(_phase_display_name(stage))
    if pv_required:
        for stage in ("PV1", "PV2"):
            if not any(_stage_key(node.name) == stage for node in nodes):
                reminders.append(_phase_display_name(stage))
    if regression_required and not regression_nodes:
        reminders.append("线上回归")

    translation_required = _stage_reached(current, "多语言翻译", rules.process_order)
    translation_nodes = [
        node for node in nodes if _stage_key(node.name) == "多语言翻译"
    ]
    if translation_required and not translation_nodes:
        reminders.append("多语言翻译")
    elif translation_required and not any(_node_done(node) for node in translation_nodes):
        level = max(level, RiskLevel.WARNING)
        reasons.append("多语言翻译未完成，建议合板前完成")
        findings.append(
            _make_finding(
                "translation.incomplete",
                "多语言翻译未完成，建议合板前完成",
                level=RiskLevel.WARNING,
                source="test_gate",
                stage_refs=["多语言翻译"],
                domain_refs=[node.domain for node in translation_nodes],
            )
        )
    return (
        level,
        _deduplicate(reasons),
        _deduplicate(domains),
        failures,
        _deduplicate_findings(findings),
        _deduplicate(reminders),
    )


def _record_gate_failures(
    failures: Dict[str, Tuple[str, RiskFinding]],
    nodes: Sequence[DeliveryNode],
    stage: str,
    reason: str,
    finding: RiskFinding,
) -> None:
    for node in nodes:
        if _stage_key(node.name) == stage and not _node_done(node):
            failures[node.record_id] = (reason, finding)


def _domain_stage_passed(nodes: Sequence[DeliveryNode], stage: str) -> bool:
    matching = [node for node in nodes if _stage_key(node.name) == stage]
    return bool(matching) and any(_node_done(node) for node in matching)


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
        requirement.current_stage,
    )
    phases = _build_phase_events(events, rules, now)
    safe_deadlines = _safe_deadlines(
        phases,
        requirement.merge_at,
        rules.duration_mode,
    )
    node_risks: List[NodeRisk] = []
    reasons: List[str] = []
    findings: List[RiskFinding] = []
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
        safe_deadline = safe_deadlines[phase.order]
        for event in phase.events:
            if event.node is None:
                continue
            node_evaluation = _evaluate_node(
                event,
                safe_deadline,
                first_unfinished,
                rules,
                now,
            )
            level = max(level, node_evaluation.level)
            reasons.extend(node_evaluation.reasons)
            findings.extend(node_evaluation.findings)
            node_risks.append(
                NodeRisk(
                    node_record_id=event.node.record_id,
                    requirement_id=event.node.requirement_id,
                    node_name=event.node.name,
                    domain=event.node.domain,
                    owner_id=event.node.owner_id,
                    owner_name=event.node.owner_name,
                    owners=list(event.node.owners),
                    planned_end=event.node.planned_end,
                    planned_start=event.node.planned_start,
                    status=event.node.status,
                    level=node_evaluation.level,
                    predicted_completion=_predict_node_completion(event, rules, now),
                    safe_deadline=safe_deadline,
                    buffer_days=node_evaluation.buffer_days,
                    reasons=_deduplicate(node_evaluation.reasons),
                    findings=_deduplicate_findings(node_evaluation.findings),
                    actions=[],
                    progress_note=event.node.progress_note,
                )
            )

    for checklist_node in checklist_nodes:
        checklist_risk = _evaluate_checklist_node(
            checklist_node, requirement, rules, now
        )
        level = max(level, checklist_risk.level)
        reasons.extend(checklist_risk.reasons)
        findings.extend(checklist_risk.findings)
        node_risks.append(checklist_risk)

    for phase in phases:
        if (
            any(event.node is not None for event in phase.events)
            or phase.remaining_duration <= 0
            or phase.label not in {"AT1", "AT2", "PV1", "PV2", "线上回归"}
            or not phase.unfinished
        ):
            continue
        safe_deadline = safe_deadlines[phase.order]
        latest_start = subtract_days(
            safe_deadline, phase.remaining_duration, rules.duration_mode
        )
        if days_available(now, latest_start, rules.duration_mode) <= 2:
            level = max(level, RiskLevel.WARNING)
            reason = "测试排期缺少计划开始时间或测试节点：{}｜{}".format(
                domain,
                _phase_display_name(phase.label),
            )
            reasons.append(reason)
            findings.append(
                _make_finding(
                    "test.schedule_missing",
                    reason,
                    level=RiskLevel.WARNING,
                    source="test_schedule",
                    stage_refs=[_phase_display_name(phase.label)],
                    domain_refs=[domain],
                )
            )

    if not phases:
        return _DomainEvaluation(
            domain=domain,
            predicted_completion=None,
            buffer_days=None,
            level=level,
            reasons=_deduplicate(reasons),
            findings=_deduplicate_findings(findings),
            node_risks=node_risks,
            schedule_formula=None,
        )

    predicted_completion = _predict_domain_completion(phases, rules, now)
    has_unfinished = any(phase.unfinished for phase in phases)
    missing_schedule_stages = _missing_schedule_stage_refs(phases)
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

    if has_unfinished and minimum_buffer < 0:
        level = RiskLevel.SEVERE
        reasons.append(_minimum_window_reason(domain, phases))
        reasons.append("剩余缓冲为负")
        findings.append(
            _make_finding(
                "schedule.minimum_window_insufficient",
                _minimum_window_reason(domain, phases),
                level=RiskLevel.SEVERE,
                source="domain_schedule",
                stage_refs=missing_schedule_stages,
                domain_refs=[domain],
            )
        )
        findings.append(
            _make_finding(
                "schedule.buffer_negative",
                "剩余缓冲为负",
                level=RiskLevel.SEVERE,
                source="domain_schedule",
                stage_refs=missing_schedule_stages,
                domain_refs=[domain],
            )
        )
    elif has_unfinished and buffer_days is not None and buffer_days < 0:
        level = RiskLevel.SEVERE
        reasons.append(f"{domain}交付域预计完成时间晚于合板时间")
        reasons.append("剩余缓冲为负")
        findings.append(
            _make_finding(
                "schedule.buffer_negative",
                "剩余缓冲为负",
                level=RiskLevel.SEVERE,
                source="domain_schedule",
                stage_refs=missing_schedule_stages,
                domain_refs=[domain],
            )
        )
    elif has_unfinished and buffer_days is not None and buffer_days <= 2:
        level = max(level, RiskLevel.WARNING)
        reasons.append("剩余缓冲不超过2天")
        findings.append(
            _make_finding(
                "schedule.buffer_low",
                "剩余缓冲不超过2天",
                level=RiskLevel.WARNING,
                source="domain_schedule",
                stage_refs=missing_schedule_stages,
                domain_refs=[domain],
            )
        )

    if (
        predicted_completion is not None
        and predicted_completion > requirement.merge_at
    ):
        level = RiskLevel.SEVERE
        reason = f"{domain}交付域预计完成时间晚于合板时间"
        reasons.append(reason)
        findings.append(
            _make_finding(
                "domain.completion_after_merge",
                reason,
                level=RiskLevel.SEVERE,
                source="domain_schedule",
                stage_refs=missing_schedule_stages,
                domain_refs=[domain],
            )
        )

    schedule_formula = _build_schedule_formula(
        domain, phases, rules, now, predicted_completion
    )

    return _DomainEvaluation(
        domain=domain,
        predicted_completion=predicted_completion,
        buffer_days=buffer_days,
        level=level,
        reasons=_deduplicate(reasons),
        findings=_deduplicate_findings(findings),
        node_risks=node_risks,
        schedule_formula=schedule_formula,
    )


def _evaluate_node(
    event: _Event,
    safe_deadline: datetime,
    first_unfinished: Optional[DeliveryNode],
    rules: EffectiveRules,
    now: datetime,
) -> _NodeEvaluation:
    node = event.node
    if node is None:
        raise ValueError("node event is required")
    node_done = _node_done(node)
    predicted_completion = _predict_node_completion(event, rules, now)
    comparison_time = (
        node.actual_end or node.planned_end or predicted_completion or now
        if node_done
        else max(now, node.planned_end or predicted_completion or now)
    )
    buffer_days = days_available(comparison_time, safe_deadline, rules.duration_mode)
    level = RiskLevel.NORMAL
    reasons: List[str] = []
    findings: List[RiskFinding] = []
    stage_ref = _phase_display_name(event.label)
    if node_done:
        return _NodeEvaluation(
            level=level,
            reasons=_deduplicate(reasons),
            buffer_days=buffer_days,
            findings=_deduplicate_findings(findings),
        )

    if buffer_days < 0:
        level = RiskLevel.SEVERE
        reasons.append("节点延期已经耗尽全部缓冲")
        findings.append(
            _make_finding(
                "node.delay_consumes_buffer",
                "节点延期已经耗尽全部缓冲",
                level=RiskLevel.SEVERE,
                source="node_schedule",
                stage_refs=[stage_ref],
                domain_refs=[node.domain],
            )
        )
    elif node.planned_end is not None and now > node.planned_end:
        level = RiskLevel.WARNING
        reasons.append("节点延期但仍有缓冲")
        findings.append(
            _make_finding(
                "node.delay_with_buffer",
                "节点延期但仍有缓冲",
                level=RiskLevel.WARNING,
                source="node_schedule",
                stage_refs=[stage_ref],
                domain_refs=[node.domain],
            )
        )

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
            findings.append(
                _make_finding(
                    "node.stale_update",
                    "连续2个工作日没有进展更新",
                    level=RiskLevel.WARNING,
                    source="node_progress",
                    stage_refs=[stage_ref],
                    domain_refs=[node.domain],
                )
            )

    if (
        event.duration > 0
        and node.work_type == "测试"
        and node.planned_start is None
    ):
        latest_start = subtract_days(safe_deadline, event.duration, rules.duration_mode)
        if days_available(now, latest_start, rules.duration_mode) <= 2:
            level = max(level, RiskLevel.WARNING)
            reasons.append(
                "测试排期缺少计划开始时间：{}｜{}".format(
                    node.domain,
                    node.name,
                )
            )
            findings.append(
                _make_finding(
                    "test.schedule_missing",
                    "测试排期缺少计划开始时间：{}｜{}".format(
                        node.domain,
                        node.name,
                    ),
                    level=RiskLevel.WARNING,
                    source="test_schedule",
                    stage_refs=[stage_ref],
                    domain_refs=[node.domain],
                )
            )

    return _NodeEvaluation(
        level=level,
        reasons=_deduplicate(reasons),
        buffer_days=buffer_days,
        findings=_deduplicate_findings(findings),
    )


def _evaluate_blockers(
    blockers: Sequence[Blocker],
    nodes: Sequence[DeliveryNode],
    now: datetime,
) -> Tuple[RiskLevel, List[str], List[str], List[RiskFinding]]:
    level = RiskLevel.NORMAL
    reasons: List[str] = []
    affected_domains: List[str] = []
    node_domains = {node.record_id: node.domain for node in nodes}
    node_names = {node.record_id: node.name for node in nodes}
    findings: List[RiskFinding] = []

    for blocker in blockers:
        if _blocker_done(blocker):
            continue
        blocker_is_risky = False
        if blocker.planned_resolution_at < now:
            blocker_is_risky = True
            if blocker.affects_merge:
                level = RiskLevel.SEVERE
                reason = "影响合板的阻塞项已超期"
                reasons.append(reason)
            else:
                level = max(level, RiskLevel.WARNING)
                reason = "阻塞项已超期"
                reasons.append(reason)
            findings.append(
                _make_finding(
                    "blocker.overdue",
                    reason,
                    level=RiskLevel.SEVERE
                    if blocker.affects_merge
                    else RiskLevel.WARNING,
                    source="blocker",
                    stage_refs=[node_names[blocker.node_record_id]]
                    if blocker.node_record_id in node_names
                    else [],
                    domain_refs=[node_domains[blocker.node_record_id]]
                    if blocker.node_record_id in node_domains
                    else [],
                )
            )
        else:
            due_in = days_available(now, blocker.planned_resolution_at, "workday")
            if 0 <= due_in <= 1:
                level = max(level, RiskLevel.WARNING)
                reasons.append("阻塞项将在1个工作日内到期")
                blocker_is_risky = True
                findings.append(
                    _make_finding(
                        "blocker.due_soon",
                        "阻塞项将在1个工作日内到期",
                        level=RiskLevel.WARNING,
                        source="blocker",
                        stage_refs=[node_names[blocker.node_record_id]]
                        if blocker.node_record_id in node_names
                        else [],
                        domain_refs=[node_domains[blocker.node_record_id]]
                        if blocker.node_record_id in node_domains
                        else [],
                    )
                )
        if blocker_is_risky and blocker.node_record_id in node_domains:
            affected_domains.append(node_domains[blocker.node_record_id])

    return (
        level,
        _deduplicate(reasons),
        _deduplicate(affected_domains),
        _deduplicate_findings(findings),
    )


def _evaluate_server_launch(
    launch_at: Optional[datetime],
    nodes: Sequence[DeliveryNode],
    rules: EffectiveRules,
    now: datetime,
) -> Tuple[RiskLevel, List[str], List[RiskFinding]]:
    if launch_at is None or not any(node.domain == "服务端" for node in nodes):
        return RiskLevel.NORMAL, [], []

    level = RiskLevel.NORMAL
    reasons: List[str] = []
    findings: List[RiskFinding] = []
    if launch_at.weekday() not in rules.launch_weekdays:
        level = RiskLevel.SEVERE
        reason = "服务端上线日期不符合允许星期"
        reasons.append(reason)
        findings.append(
            _make_finding(
                "server_launch.weekday_invalid",
                reason,
                level=RiskLevel.SEVERE,
                source="server_launch",
                stage_refs=["服务端上线"],
                domain_refs=["服务端"],
            )
        )

    cutoff = _parse_cutoff(rules.launch_cutoff)
    if launch_at.timetz().replace(tzinfo=None) > cutoff:
        level = RiskLevel.SEVERE
        reason = f"服务端上线时间晚于{rules.launch_cutoff}"
        reasons.append(reason)
        findings.append(
            _make_finding(
                "server_launch.cutoff_exceeded",
                reason,
                level=RiskLevel.SEVERE,
                source="server_launch",
                stage_refs=["服务端上线"],
                domain_refs=["服务端"],
            )
        )

    checklist_deadline = subtract_days(
        launch_at, rules.checklist_days_before, "natural"
    )
    has_explicit_server_launch = any(
        _stage_key(node.name) == "服务端上线" and not _is_checklist(node)
        for node in nodes
    )
    if not has_explicit_server_launch and now.date() >= checklist_deadline.date():
        checklist_nodes = [node for node in nodes if _is_checklist(node)]
        if not checklist_nodes or any(not _node_done(node) for node in checklist_nodes):
            level = RiskLevel.SEVERE
            reason = "服务端上线 Checklist 未完成"
            reasons.append(reason)
            findings.append(
                _make_finding(
                    "server_launch.checklist_incomplete",
                    reason,
                    level=RiskLevel.SEVERE,
                    source="server_launch",
                    stage_refs=["服务端上线 Checklist"],
                    domain_refs=["服务端"],
                )
            )

    return level, _deduplicate(reasons), _deduplicate_findings(findings)


def _build_events(
    domain: str,
    nodes: Sequence[DeliveryNode],
    rules: EffectiveRules,
    current_stage: str = "",
) -> List[_Event]:
    at_first = rules.stage_days["AT 测试第一轮"]
    at_second = rules.stage_days["AT 测试第二轮"]
    pv_first = rules.stage_days["PV 测试第一轮"]
    pv_second = rules.stage_days["PV 测试第二轮"]
    events: List[_Event] = []
    regression_order = rules.process_order.get("线上回归", _REGRESSION_ORDER)
    configured_stage_names = _configured_stage_names(rules)

    for node in nodes:
        configured_stage = _configured_stage_name(node, configured_stage_names)
        if (
            rules.has_stage_configuration
            and configured_stage in rules.disabled_stages
        ):
            continue
        order, label, duration = _node_stage(
            node,
            at_first,
            at_second,
            pv_first,
            pv_second,
            rules.regression_days,
            rules.duration_mode,
            rules.process_order,
            configured_stage,
        )
        events.append(_Event(order=order, duration=duration, label=label, node=node))

    stages = {event.label for event in events}
    current_process = _stage_display_to_process(current_stage)
    current_order = rules.process_order.get(current_process)
    virtual_stages = (
        ("AT 测试第一轮", "AT1", 1),
        ("AT 测试第二轮", "AT2", 2),
        ("PV 测试第一轮", "PV1", 1),
        ("PV 测试第二轮", "PV2", 2),
        ("线上回归", "线上回归", 0),
    )
    test_schedule_enabled = _domain_test_schedule_enabled(domain, nodes, rules)
    if current_order is not None:
        for stage_name, label, round_number in virtual_stages:
            stage_order = rules.process_order.get(stage_name)
            if (
                stage_order is None
                or stage_order < current_order
                or label in stages
                or not _stage_enabled(rules, stage_name)
                or (
                    stage_name != "线上回归"
                    and not test_schedule_enabled
                )
            ):
                continue
            events.append(
                _Event(
                    (stage_order, round_number),
                    rules.stage_days[stage_name],
                    label,
                )
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
    configured_stage: Optional[str] = None,
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
            stage_name = configured_stage or _round_stage_name(family, round_number)
            fallback_order = (
                _AT1_ORDER
                if round_number == 1
                else process_order.get("AT 测试第二轮", _AT1_ORDER + 1)
            )
            return (
                process_order.get(stage_name, fallback_order),
                round_number,
            ), f"AT{round_number}", duration
        duration = (
            pv_first
            if round_number == 1
            else pv_second
            if round_number == 2
            else _extra_round_duration(node, duration_mode)
        )
        stage_name = configured_stage or _round_stage_name(family, round_number)
        fallback_order = (
            _PV1_ORDER
            if round_number == 1
            else process_order.get("PV 测试第二轮", _PV1_ORDER + 1)
        )
        return (
            process_order.get(stage_name, fallback_order),
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
    return _round_stage_from_name(node.name, normalized_name)


def _round_stage_from_name(
    name: str, normalized_name: Optional[str] = None
) -> Optional[Tuple[str, int]]:
    normalized = normalized_name or "".join(name.upper().split())
    numeric_match = re.search(
        r"(AT|PV)(?:测试)?第(\d+)轮", normalized
    )
    if numeric_match is None:
        numeric_match = re.search(r"(AT|PV)(\d+)(?:轮)?", normalized)
    if numeric_match is not None:
        return numeric_match.group(1), int(numeric_match.group(2))

    for family in ("AT", "PV"):
        if family not in normalized:
            continue
        chinese_match = re.search(
            r"第([一二三四五六七八九十]+)轮", normalized
        )
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


def _phase_display_name(label: str) -> str:
    match = re.fullmatch(r"(AT|PV)(\d+)", label)
    if match is not None:
        return _round_stage_name(match.group(1), int(match.group(2)))
    return label


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


def _round_stage_name(family: str, round_number: int) -> str:
    chinese_rounds = {1: "一", 2: "二"}
    value = chinese_rounds.get(round_number, str(round_number))
    return f"{family} 测试第{value}轮"


def _configured_stage_names(rules: EffectiveRules) -> Tuple[str, ...]:
    return tuple(rules.process_order) + tuple(sorted(rules.disabled_stages))


def _configured_stage_name(
    node: DeliveryNode, configured_names: Iterable[str] = ()
) -> str:
    normalized = "".join(node.name.upper().split())
    round_stage = _test_round_stage(node, normalized)
    if round_stage is not None:
        names = tuple(configured_names)
        for name in names:
            if "".join(name.upper().split()) == normalized:
                return name
        for name in names:
            if _round_stage_from_name(name) == round_stage:
                return name
        return _round_stage_name(*round_stage)
    return _matching_process_name(node)


def _stage_enabled(rules: EffectiveRules, stage_name: str) -> bool:
    if not rules.has_stage_configuration:
        return True
    return stage_name in rules.enabled_stages


def _test_role_enabled(node: DeliveryNode, rules: EffectiveRules) -> bool:
    if node.work_type != "测试":
        return True
    role_name = "{}测试".format(node.domain)
    return rules.test_role_states.get(role_name, True)


def _domain_test_schedule_enabled(
    domain: str,
    nodes: Sequence[DeliveryNode],
    rules: EffectiveRules,
) -> bool:
    if domain == "项目排期":
        return True
    if any(node.work_type in {"研发", "测试"} for node in nodes):
        return True
    return rules.test_role_states.get("{}测试".format(domain), False)


def _requirement_people(
    requirement: Requirement,
    nodes: Sequence[DeliveryNode],
    blockers: Sequence[Blocker],
) -> List[Person]:
    people = [
        Person(
            open_id=requirement.project_owner_id,
            name=requirement.project_owner_name,
        )
    ]
    if requirement.product_owner_id and requirement.product_owner_name:
        people.append(
            Person(
                open_id=requirement.product_owner_id,
                name=requirement.product_owner_name,
            )
        )
    people.extend(person for node in nodes for person in node.owners)
    people.extend(
        Person(open_id=blocker.owner_id, name=blocker.owner_name)
        for blocker in blockers
    )
    unique = {}
    for person in people:
        unique[(person.open_id, person.name)] = person
    return list(unique.values())


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
    real_unfinished = [
        any(
            event.node is not None and not node_done[id(event.node)]
            for event in phase_events
        )
        for _, phase_events in grouped_events
    ]
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
            unfinished = True
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
    if node.planned_end is not None:
        return 0
    if node.status != NodeStatus.IN_PROGRESS or node.planned_start is None:
        return event.duration
    consumed = max(
        0, days_available(node.planned_start, now, rules.duration_mode)
    )
    return max(0, event.duration - consumed)


def _safe_deadlines(
    phases: Sequence[_PhaseEvent],
    merge_at: datetime,
    duration_mode: DayMode,
) -> Dict[_PhaseRank, datetime]:
    deadlines: Dict[_PhaseRank, datetime] = {}
    suffix_duration = 0
    earliest_downstream_start: Optional[datetime] = None
    for phase in reversed(phases):
        deadline = subtract_days(merge_at, suffix_duration, duration_mode)
        if phase.unfinished and earliest_downstream_start is not None:
            deadline = min(deadline, earliest_downstream_start)
        deadlines[phase.order] = deadline

        planned_starts = [
            event.node.planned_start
            for event in phase.events
            if event.node is not None
            and not _node_done(event.node)
            and event.node.planned_start is not None
            and event.node.planned_end is not None
        ]
        if planned_starts:
            phase_start = min(planned_starts)
            earliest_downstream_start = (
                phase_start
                if earliest_downstream_start is None
                else min(earliest_downstream_start, phase_start)
            )
        suffix_duration += phase.remaining_duration
    return deadlines


def _missing_schedule_stage_refs(phases: Sequence[_PhaseEvent]) -> List[str]:
    key_stage_labels = {"AT1", "AT2", "PV1", "PV2", "线上回归"}
    missing_stages: List[str] = []
    for phase in phases:
        if (
            not phase.unfinished
            or phase.label not in key_stage_labels
        ):
            continue
        real_events = [event for event in phase.events if event.node is not None]
        if not real_events or any(
            not _node_done(event.node) and event.node.planned_end is None
            for event in real_events
            if event.node is not None
        ):
            missing_stages.append(_phase_display_name(phase.label))
    return list(dict.fromkeys(missing_stages))


def _build_schedule_formula(
    domain: str,
    phases: Sequence[_PhaseEvent],
    rules: EffectiveRules,
    started_at: datetime,
    predicted_completion: Optional[datetime],
) -> Optional[ScheduleFormula]:
    if predicted_completion is None:
        return None
    terms = []
    for phase in phases:
        if not phase.unfinished or phase.remaining_duration <= 0:
            continue
        terms.append(
            ScheduleFormulaTerm(
                label=_phase_display_name(phase.label),
                days=phase.remaining_duration,
            )
        )
    if not terms:
        return None
    total_duration = sum(term.days for term in terms)
    formula_start = subtract_days(
        predicted_completion, total_duration, rules.duration_mode
    )
    return ScheduleFormula(
        domain=domain,
        started_at=formula_start,
        duration_mode=rules.duration_mode,
        terms=terms,
        predicted_completion=predicted_completion,
    )


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
                completion = node.actual_end or node.planned_end
                if completion is not None:
                    phase_completions.append(completion)
                continue
            start = (
                max(cursor, now)
                if node.status == NodeStatus.IN_PROGRESS
                else node.planned_start or cursor
            )
            minimum_end = add_days(
                start,
                _remaining_event_duration(event, rules, now),
                rules.duration_mode,
            )
            phase_completions.append(max(minimum_end, node.planned_end or minimum_end))
        if phase_completions:
            cursor = max(cursor, max(phase_completions))
    if has_unfinished:
        return cursor
    completed = [
        completion
        for phase in phases
        for event in phase.events
        if event.node is not None
        for completion in [event.node.actual_end or event.node.planned_end]
        if completion is not None
    ]
    return max(completed) if completed else None


def _predict_node_completion(
    event: _Event, rules: EffectiveRules, now: datetime
) -> Optional[datetime]:
    node = event.node
    if node is None:
        raise ValueError("node event is required")
    if _node_done(node):
        return node.actual_end or node.planned_end
    if node.planned_end is not None:
        return max(now, node.planned_end)
    start = (
        now
        if node.status == NodeStatus.IN_PROGRESS
        else node.planned_start or now
    )
    return max(
        add_days(
            start,
            _remaining_event_duration(event, rules, now),
            rules.duration_mode,
        ),
        node.planned_end or start,
    )


def _node_done(node: DeliveryNode) -> bool:
    if node.status == NodeStatus.CANCELLED:
        return False
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
    findings = (
        [
            _make_finding(
                "server_launch.checklist_incomplete",
                "服务端上线 Checklist 未完成",
                level=RiskLevel.SEVERE,
                source="server_launch",
                stage_refs=["服务端上线 Checklist"],
                domain_refs=[node.domain],
            )
        ]
        if reasons
        else []
    )
    return NodeRisk(
        node_record_id=node.record_id,
        requirement_id=node.requirement_id,
        node_name=node.name,
        domain=node.domain,
        owner_id=node.owner_id,
        owner_name=node.owner_name,
        owners=list(node.owners),
        planned_end=node.planned_end,
        planned_start=node.planned_start,
        status=node.status,
        level=RiskLevel.SEVERE if reasons else RiskLevel.NORMAL,
        predicted_completion=None,
        safe_deadline=safe_deadline,
        buffer_days=None,
        reasons=reasons,
        findings=findings,
        actions=[],
        progress_note=node.progress_note,
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
    planned_end = (
        event.node.planned_end
        if event.node is not None and event.node.planned_end is not None
        else datetime.max
    )
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


def _make_finding(
    reason_code: str,
    reason_text: str,
    *,
    level: RiskLevel,
    source: str,
    stage_refs: Iterable[str] = (),
    domain_refs: Iterable[str] = (),
) -> RiskFinding:
    return RiskFinding(
        reason_code=reason_code,
        reason_text=reason_text,
        stage_refs=list(stage_refs),
        domain_refs=list(domain_refs),
        level=level,
        source=source,
    )


def _deduplicate_findings(findings: Iterable[RiskFinding]) -> List[RiskFinding]:
    seen = set()
    unique: List[RiskFinding] = []
    for finding in findings:
        key = (
            finding.reason_code,
            finding.reason_text,
            tuple(finding.stage_refs),
            tuple(finding.domain_refs),
            finding.level,
            finding.source,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique
