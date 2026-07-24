from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from requirement_monitor.models import (
    Blocker,
    DataSnapshot,
    DeliveryNode,
    FixedRules,
    NodeStatus,
    ProjectConfig,
    Requirement,
    RiskLevel,
    RunReport,
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
    with pytest.raises(ValidationError, match="timezone"):
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
    with pytest.raises(ValidationError):
        make_requirement(**{field_name: field_value})


def test_mutable_list_defaults_are_not_shared():
    first = DataSnapshot()
    second = DataSnapshot()

    first.requirements.append(make_requirement())

    assert second.requirements == []


def test_business_strings_are_stripped():
    requirement = make_requirement(
        name=" 需求进展提醒 ",
        project=" 工作小工具 ",
        current_stage=" 开发 ",
        target_version=" 1.0.0 ",
    )

    assert requirement.name == "需求进展提醒"
    assert requirement.project == "工作小工具"
    assert requirement.current_stage == "开发"
    assert requirement.target_version == "1.0.0"


@pytest.mark.parametrize("field_name", ("domain", "work_type", "owner_name"))
def test_delivery_node_rejects_blank_business_strings(field_name):
    values = {
        "record_id": "rec-node",
        "requirement_id": "REQ-1",
        "domain": "服务端",
        "work_type": "研发",
        "name": "服务端开发",
        "owner_id": "ou-owner",
        "owner_name": "张三",
        "planned_end": aware_datetime(),
        "status": NodeStatus.IN_PROGRESS,
    }
    values[field_name] = "   "

    with pytest.raises(ValidationError):
        DeliveryNode(**values)


def test_risk_fields_remain_mutable():
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

    node.risk_level = RiskLevel.SEVERE
    node.risk_reasons.append("已超过安全截止时间")

    assert node.risk_level == RiskLevel.SEVERE
    assert node.risk_reasons == ["已超过安全截止时间"]


def test_requirement_launch_cannot_precede_merge():
    with pytest.raises(ValidationError, match="launch_at"):
        make_requirement(merge_at=aware_datetime(14), launch_at=aware_datetime(13))


def test_delivery_node_start_cannot_follow_end():
    with pytest.raises(ValidationError, match="planned_start"):
        DeliveryNode(
            record_id="rec-node",
            requirement_id="REQ-1",
            domain="服务端",
            work_type="研发",
            name="服务端开发",
            owner_id="ou-owner",
            owner_name="张三",
            planned_start=aware_datetime(4),
            planned_end=aware_datetime(3),
            status=NodeStatus.IN_PROGRESS,
        )


@pytest.mark.parametrize(
    ("planned_day", "actual_day", "message"),
    ((2, None, "planned_resolution_at"), (4, 2, "actual_resolution_at")),
)
def test_blocker_resolution_times_follow_found_time(planned_day, actual_day, message):
    values = {
        "record_id": "rec-blocker",
        "requirement_id": "REQ-1",
        "title": "接口未就绪",
        "owner_id": "ou-owner",
        "owner_name": "张三",
        "found_at": aware_datetime(3),
        "planned_resolution_at": aware_datetime(planned_day),
        "status": "处理中",
        "affects_merge": True,
    }
    if actual_day is not None:
        values["actual_resolution_at"] = aware_datetime(actual_day)
        values["found_at"] = aware_datetime(3)

    with pytest.raises(ValidationError, match=message):
        Blocker(**values)


@pytest.mark.parametrize("cutoff", ("9:30", "24:00", "17:60", "invalid"))
def test_project_config_rejects_invalid_launch_cutoff(cutoff):
    with pytest.raises(ValidationError, match="launch_cutoff"):
        ProjectConfig(
            record_id="rec-config",
            project="工作小工具",
            duration_mode="workday",
            launch_weekdays={1, 3},
            launch_cutoff=cutoff,
        )


@pytest.mark.parametrize("weekdays", (set(), {-1}, {7}))
def test_project_config_rejects_invalid_launch_weekdays(weekdays):
    with pytest.raises(ValidationError, match="launch_weekdays"):
        ProjectConfig(
            record_id="rec-config",
            project="工作小工具",
            duration_mode="workday",
            launch_weekdays=weekdays,
        )


def test_fixed_rules_validate_cutoff_weekdays_and_nonnegative_days():
    with pytest.raises(ValidationError):
        FixedRules(
            server_launch_weekdays=set(),
            server_launch_cutoff="24:00",
            checklist_days_before=-1,
            at_workdays=8,
            at_natural_days=11,
            pv_days=3,
            bugfix_days=2,
            regression_days=3,
        )


def test_run_report_finish_cannot_precede_start():
    with pytest.raises(ValidationError, match="finished_at"):
        RunReport(
            trigger="manual",
            started_at=aware_datetime(4),
            finished_at=aware_datetime(3),
        )
