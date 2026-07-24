from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from requirement_monitor.models import (
    DataSnapshot,
    DeliveryNode,
    NodeStatus,
    Requirement,
    RiskLevel,
)


TIMEZONE = ZoneInfo("Asia/Shanghai")


def aware_datetime(day: int = 3) -> datetime:
    return datetime(2026, 8, day, 18, 0, tzinfo=TIMEZONE)


def make_requirement(**overrides) -> Requirement:
    values = {
        "record_id": "rec-requirement",
        "requirement_id": "REQ-1",
        "name": "需求进展提醒",
        "project": "工作小工具",
        "current_stage": "开发",
        "project_owner_id": "ou-project-owner",
        "project_owner_name": "李四",
        "target_version": "1.0.0",
        "merge_at": aware_datetime(14),
        "briefing_completed": True,
        "notification_enabled": True,
        "archived": False,
    }
    values.update(overrides)
    return Requirement(**values)


def test_delivery_node_defaults_to_normal_risk():
    node = DeliveryNode(
        record_id="rec-node",
        requirement_id="REQ-1",
        domain="服务端",
        work_type="研发",
        name="服务端开发",
        owner_id="ou-owner",
        owner_name="张三",
        planned_end=aware_datetime(),
        status=NodeStatus.IN_PROGRESS,
    )

    assert node.risk_level == RiskLevel.NORMAL
    assert node.risk_reasons == []


def test_naive_datetime_is_rejected():
    with pytest.raises(ValidationError, match="timezone-aware"):
        DeliveryNode(
            record_id="rec-node",
            requirement_id="REQ-1",
            domain="服务端",
            work_type="研发",
            name="服务端开发",
            owner_id="ou-owner",
            owner_name="张三",
            planned_end=datetime(2026, 8, 3, 18, 0),
            status=NodeStatus.IN_PROGRESS,
        )


def test_eligible_requirements_filters_notification_criteria():
    eligible = make_requirement(record_id="eligible", requirement_id="REQ-1")
    briefing_incomplete = make_requirement(
        record_id="briefing", requirement_id="REQ-2", briefing_completed=False
    )
    notifications_disabled = make_requirement(
        record_id="disabled", requirement_id="REQ-3", notification_enabled=False
    )
    archived = make_requirement(
        record_id="archived", requirement_id="REQ-4", archived=True
    )
    snapshot = DataSnapshot(
        requirements=[
            eligible,
            briefing_incomplete,
            notifications_disabled,
            archived,
        ],
        nodes=[],
        blockers=[],
        project_configs=[],
    )

    assert snapshot.eligible_requirements() == [eligible]


def test_risk_levels_are_ordered_by_severity():
    assert RiskLevel.NORMAL < RiskLevel.WARNING < RiskLevel.SEVERE
    assert max(RiskLevel.WARNING, RiskLevel.SEVERE) == RiskLevel.SEVERE


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("record_id", ""),
        ("requirement_id", "   "),
        ("name", ""),
        ("project", " "),
    ),
)
def test_empty_ids_and_names_are_rejected(field_name, field_value):
    with pytest.raises(ValidationError, match="must not be empty"):
        make_requirement(**{field_name: field_value})


def test_mutable_list_defaults_are_not_shared():
    first = DataSnapshot()
    second = DataSnapshot()

    first.requirements.append(make_requirement())

    assert second.requirements == []
