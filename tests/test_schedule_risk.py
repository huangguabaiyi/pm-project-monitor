from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from requirement_monitor.schedule_risk import RiskLevel, evaluate_schedule


NOW = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)


@dataclass
class Node:
    id: str
    name: str
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    actual_end: datetime | None = None
    status: str = "not_started"
    blocked_reason: str = ""


@dataclass
class Edge:
    source_node_id: str
    target_node_id: str


def evaluate(nodes, edges=()):
    return evaluate_schedule(nodes, edges, now=NOW)


@pytest.mark.parametrize(
    ("node", "level", "reason"),
    [
        (Node("a", "未排期"), RiskLevel.WARNING, "尚未设置计划开始和结束时间"),
        (Node("a", "缺开始", planned_end=NOW + timedelta(days=1)), RiskLevel.WARNING, "缺少计划开始时间"),
        (Node("a", "缺结束", planned_start=NOW + timedelta(days=1)), RiskLevel.WARNING, "缺少计划结束时间"),
        (Node("a", "倒置", planned_start=NOW + timedelta(days=2), planned_end=NOW + timedelta(days=1)), RiskLevel.SEVERE, "结束时间早于"),
        (Node("a", "过期", planned_start=NOW - timedelta(days=2), planned_end=NOW - timedelta(days=1)), RiskLevel.SEVERE, "超过计划结束时间"),
        (Node("a", "应开始", planned_start=NOW, planned_end=NOW + timedelta(days=1)), RiskLevel.WARNING, "仍未开始"),
    ],
)
def test_single_node_schedule_rules(node, level, reason):
    result = evaluate([node])
    assert result.level == level
    assert any(reason in item for item in result.reasons)


def test_downstream_schedule_detects_any_ancestor_missing_end():
    start = Node("start", "需求评审", planned_start=NOW - timedelta(days=2), status="in_progress")
    middle = Node("middle", "开发")
    finish = Node("finish", "测试", planned_start=NOW + timedelta(days=2), planned_end=NOW + timedelta(days=3))
    result = evaluate([start, middle, finish], [Edge("start", "middle"), Edge("middle", "finish")])
    assert any("测试" in reason and "需求评审" in reason for reason in result.node_results["start"].reasons)
    assert any("测试" in reason and "开发" in reason for reason in result.node_results["middle"].reasons)


def test_dependency_overlap_and_early_start_are_severe():
    source = Node("a", "开发", planned_start=NOW, planned_end=NOW + timedelta(days=3), status="in_progress")
    target = Node("b", "测试", planned_start=NOW + timedelta(days=2), planned_end=NOW + timedelta(days=4), status="in_progress")
    result = evaluate([source, target], [Edge("a", "b")])
    assert result.node_results["b"].level == RiskLevel.SEVERE
    assert any("早于前置节点" in reason for reason in result.node_results["b"].reasons)
    assert any("前置节点" in reason and "尚未完成" in reason for reason in result.node_results["b"].reasons)


def test_parallel_predecessors_must_both_complete_before_join():
    left = Node("left", "客户端", NOW - timedelta(days=1), NOW + timedelta(days=1), status="completed", actual_end=NOW)
    right = Node("right", "服务端", NOW - timedelta(days=1), NOW + timedelta(days=1), status="in_progress")
    join = Node("join", "联调", NOW + timedelta(days=1), NOW + timedelta(days=2), status="in_progress")
    result = evaluate([left, right, join], [Edge("left", "join"), Edge("right", "join")])
    assert any("服务端" in reason for reason in result.node_results["join"].reasons)
    assert not any("客户端" in reason and "尚未完成" in reason for reason in result.node_results["join"].reasons)


def test_completed_or_skipped_nodes_count_as_progress_and_frontier_is_computed():
    done = Node("done", "完成", status="completed", actual_end=NOW)
    skipped = Node("skip", "跳过", status="skipped")
    next_node = Node("next", "下一步", NOW + timedelta(days=1), NOW + timedelta(days=2))
    result = evaluate([done, skipped, next_node], [Edge("done", "next"), Edge("skip", "next")])
    assert result.completed_nodes == 2
    assert result.current_node_ids == ["next"]
    assert result.planned_completion == NOW + timedelta(days=2)
