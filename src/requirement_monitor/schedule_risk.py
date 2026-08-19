from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set


class RiskLevel(IntEnum):
    NORMAL = 0
    WARNING = 1
    SEVERE = 2


@dataclass
class NodeRiskResult:
    level: RiskLevel = RiskLevel.NORMAL
    reasons: List[str] = field(default_factory=list)

    def add(self, level: RiskLevel, reason: str) -> None:
        self.level = max(self.level, level)
        if reason not in self.reasons:
            self.reasons.append(reason)


@dataclass
class RequirementRiskResult:
    level: RiskLevel
    reasons: List[str]
    node_results: Dict[str, NodeRiskResult]
    completed_nodes: int
    total_nodes: int
    current_node_ids: List[str]
    planned_completion: Optional[datetime]


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _deduplicate(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(value for value in values if value))


def evaluate_schedule(
    nodes: Sequence[object],
    edges: Sequence[object],
    *,
    now: Optional[datetime] = None,
) -> RequirementRiskResult:
    """Evaluate risk using only node dates, status and DAG dependencies."""
    current_time = _aware(now or datetime.now(timezone.utc))
    assert current_time is not None
    by_id = {node.id: node for node in nodes}
    results = {node.id: NodeRiskResult() for node in nodes}
    predecessors: Dict[str, Set[str]] = {node.id: set() for node in nodes}
    successors: Dict[str, Set[str]] = {node.id: set() for node in nodes}
    for edge in edges:
        if edge.source_node_id in by_id and edge.target_node_id in by_id:
            predecessors[edge.target_node_id].add(edge.source_node_id)
            successors[edge.source_node_id].add(edge.target_node_id)

    for node in nodes:
        result = results[node.id]
        start = _aware(node.planned_start)
        end = _aware(node.planned_end)
        status = node.status
        if status == "skipped":
            continue
        if start is None and end is None:
            result.add(RiskLevel.WARNING, f"“{node.name}”尚未设置计划开始和结束时间")
        elif start is None:
            result.add(RiskLevel.WARNING, f"“{node.name}”已设置结束时间，但缺少计划开始时间")
        elif end is None:
            result.add(RiskLevel.WARNING, f"“{node.name}”已设置开始时间，但缺少计划结束时间")
        elif end < start:
            result.add(RiskLevel.SEVERE, f"“{node.name}”计划结束时间早于计划开始时间")

        if status == "not_started" and start is not None and current_time >= start:
            result.add(RiskLevel.WARNING, f"“{node.name}”已到计划开始时间但仍未开始")
        if status not in {"completed", "skipped"} and end is not None and current_time > end:
            result.add(RiskLevel.SEVERE, f"“{node.name}”已超过计划结束时间但尚未完成")
        if status == "blocked":
            level = RiskLevel.SEVERE if end is not None and current_time > end else RiskLevel.WARNING
            reason = node.blocked_reason.strip() if getattr(node, "blocked_reason", "") else "未填写受阻原因"
            result.add(level, f"“{node.name}”当前受阻：{reason}")
        if status == "completed" and _aware(node.actual_end) is None:
            result.add(RiskLevel.WARNING, f"“{node.name}”已完成但缺少实际完成时间")

    ancestor_cache: Dict[str, Set[str]] = {}

    def ancestors(node_id: str, visiting: Optional[Set[str]] = None) -> Set[str]:
        if node_id in ancestor_cache:
            return ancestor_cache[node_id]
        visiting = set(visiting or ())
        if node_id in visiting:
            return set()
        visiting.add(node_id)
        found: Set[str] = set()
        for predecessor_id in predecessors.get(node_id, ()):
            found.add(predecessor_id)
            found.update(ancestors(predecessor_id, visiting))
        ancestor_cache[node_id] = found
        return found

    for target in nodes:
        target_start = _aware(target.planned_start)
        if target_start is not None:
            for source_id in ancestors(target.id):
                source = by_id[source_id]
                if source.status == "skipped":
                    continue
                if _aware(source.planned_end) is None:
                    results[source_id].add(
                        RiskLevel.WARNING,
                        f"后续节点“{target.name}”已有开始时间，但前置节点“{source.name}”尚未设置计划结束时间",
                    )

    for edge in edges:
        source = by_id.get(edge.source_node_id)
        target = by_id.get(edge.target_node_id)
        if source is None or target is None:
            continue
        source_end = _aware(source.planned_end)
        target_start = _aware(target.planned_start)
        if source_end is not None and target_start is not None and source_end > target_start:
            results[target.id].add(
                RiskLevel.SEVERE,
                f"“{target.name}”计划开始时间早于前置节点“{source.name}”的计划结束时间",
            )
        if target.status in {"in_progress", "blocked", "completed"} and source.status not in {"completed", "skipped"}:
            results[target.id].add(
                RiskLevel.SEVERE,
                f"“{target.name}”已经开始，但前置节点“{source.name}”尚未完成",
            )

    completed = sum(node.status in {"completed", "skipped"} for node in nodes)
    active = [node.id for node in nodes if node.status in {"in_progress", "blocked"}]
    if not active:
        active = [
            node.id
            for node in nodes
            if node.status not in {"completed", "skipped"}
            and all(by_id[item].status in {"completed", "skipped"} for item in predecessors[node.id])
        ]
    all_reasons = _deduplicate(reason for result in results.values() for reason in result.reasons)
    level = max((result.level for result in results.values()), default=RiskLevel.NORMAL)
    planned_ends = [_aware(node.planned_end) for node in nodes if node.status != "skipped" and node.planned_end is not None]
    return RequirementRiskResult(
        level=level,
        reasons=all_reasons,
        node_results=results,
        completed_nodes=completed,
        total_nodes=len(nodes),
        current_node_ids=active,
        planned_completion=max(planned_ends) if planned_ends else None,
    )
