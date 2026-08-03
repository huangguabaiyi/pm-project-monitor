from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from requirement_monitor.models import (
    Blocker,
    DataSnapshot,
    DeliveryNode,
    FixedRules,
    NodeRisk,
    NodeStatus,
    Person,
    ProjectConfig,
    Requirement,
    RequirementRisk,
    RiskFamily,
    RiskFinding,
    RiskGroup,
    RiskLevel,
    RunReport,
    ValidationIssue,
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
    if "okr_target" in overrides and "project" not in overrides:
        values.pop("project")
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


def test_requirement_uses_okr_target_and_accepts_optional_links():
    requirement = make_requirement(
        okr_target="2026年Q2 KR3",
        requirement_doc_url="https://docs.example/req",
        meego_url="https://meego.example/123",
        translation_url="https://translate.example/123",
    )

    assert requirement.okr_target == "2026年Q2 KR3"
    assert requirement.project == "2026年Q2 KR3"
    assert requirement.requirement_doc_url == "https://docs.example/req"
    assert requirement.meego_url == "https://meego.example/123"
    assert requirement.translation_url == "https://translate.example/123"


def test_requirement_model_dump_preserves_legacy_project_field():
    requirement = make_requirement(okr_target="2026年Q2 KR3")

    dumped = requirement.model_dump()

    assert dumped["okr_target"] == "2026年Q2 KR3"
    assert dumped["project"] == "2026年Q2 KR3"


def test_requirement_accepts_legacy_chinese_project_field():
    values = make_requirement().model_dump()
    values.pop("okr_target")
    values.pop("project")
    values["项目名称"] = "旧项目名称"

    requirement = Requirement(**values)

    assert requirement.okr_target == "旧项目名称"


def test_requirement_rejects_conflicting_legacy_project_fields_without_okr_target():
    values = make_requirement().model_dump()
    values.pop("okr_target")
    values["项目名称"] = "另一个项目"

    with pytest.raises(
        ValidationError,
        match="project and 项目名称 contain conflicting values",
    ):
        Requirement(**values)


@pytest.mark.parametrize("legacy_field", ("project", "项目名称"))
def test_requirement_rejects_conflicting_project_alias(legacy_field):
    with pytest.raises(ValidationError, match="okr_target conflicts with legacy"):
        make_requirement(okr_target="新目标", **{legacy_field: "旧目标"})


@pytest.mark.parametrize("legacy_value", ("新目标", "   "))
def test_requirement_allows_matching_or_blank_legacy_project(legacy_value):
    requirement = make_requirement(
        okr_target="新目标",
        project=legacy_value,
    )

    assert requirement.okr_target == "新目标"


@pytest.mark.parametrize(
    "status",
    (
        NodeStatus.NOT_STARTED,
        NodeStatus.IN_PROGRESS,
        NodeStatus.COMPLETED,
        NodeStatus.SKIPPED,
        NodeStatus.CANCELLED,
    ),
)
def test_delivery_node_may_omit_planned_end_for_any_status(status):
    node = DeliveryNode(
        record_id="rec-node",
        requirement_id="REQ-1",
        domain="服务端",
        work_type="发布",
        name="服务端上线",
        owner_id="ou-owner",
        owner_name="张三",
        planned_end=None,
        status=status,
    )

    assert node.planned_end is None


def test_delivery_node_preserves_all_people_and_legacy_owner_properties():
    node = DeliveryNode(
        record_id="rec-node",
        requirement_id="REQ-1",
        domain="服务端",
        work_type="研发",
        name="服务端开发",
        owners=[
            Person(open_id="ou-a", name="甲"),
            Person(open_id="ou-b", name="乙"),
        ],
        planned_end=aware_datetime(),
        status=NodeStatus.IN_PROGRESS,
    )

    assert [person.open_id for person in node.owners] == ["ou-a", "ou-b"]
    assert node.owner_id == "ou-a"
    assert node.owner_name == "甲"


def test_delivery_node_rejects_empty_owner_assignment_and_handles_runtime_clear():
    node = DeliveryNode(
        record_id="rec-node",
        requirement_id="REQ-1",
        domain="服务端",
        work_type="研发",
        name="服务端开发",
        owners=[Person(open_id="ou-a", name="甲")],
        planned_end=aware_datetime(),
        status=NodeStatus.IN_PROGRESS,
    )

    with pytest.raises(ValidationError, match="at least 1"):
        node.owners = []

    node.owners.clear()

    with pytest.raises(ValueError, match="owners must contain at least one owner"):
        _ = node.owner_id
    with pytest.raises(ValueError, match="owners must contain at least one owner"):
        _ = node.owner_name


def test_project_config_exposes_five_key_stage_duration_overrides():
    config = ProjectConfig(
        record_id="rec-config",
        project="工作小工具",
        duration_mode="workday",
        at1_days=1,
        at2_days=2,
        pv1_days=3,
        pv2_days=4,
        regression_days=5,
    )

    assert (config.at1_days, config.at2_days, config.pv1_days) == (1, 2, 3)
    assert (config.pv2_days, config.regression_days) == (4, 5)


def test_project_config_does_not_expose_bugfix_reserve_days():
    assert "bugfix_days" not in ProjectConfig.model_fields


def test_project_config_only_exposes_five_schedule_duration_fields():
    assert {
        "at_days",
        "pv_days",
        "server_special_days",
        "client_special_days",
        "vehicle_special_days",
    }.isdisjoint(ProjectConfig.model_fields)


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


def test_validation_issue_normalizes_blank_requirement_id_to_none():
    issue = ValidationIssue(
        table_name="需求主表",
        requirement_id="   ",
        field_name="需求编号",
        message="must not be empty",
    )

    assert issue.requirement_id is None


def test_validation_issue_preserves_repair_and_isolation_contract():
    issue = ValidationIssue(
        table_name="进展节点表",
        record_id="rec-node",
        requirement_id="REQ-1",
        field_name="计划完成时间",
        current_value="tomorrow",
        expected_format="RFC3339 datetime",
        fix_suggestion="填写带时区的时间",
        skip_scope="record",
        message="invalid datetime",
    )

    assert issue.current_value == "tomorrow"
    assert issue.expected_format == "RFC3339 datetime"
    assert issue.fix_suggestion == "填写带时区的时间"
    assert issue.skip_scope == "record"


def test_run_report_carries_card_inputs_and_llm_state():
    risk = RequirementRisk(
        requirement_record_id="rec-req",
        requirement_id="REQ-1",
        requirement_name="需求进展提醒",
        project="工作小工具",
        target_version="1.0.0",
        merge_at=aware_datetime(3),
        launch_at=aware_datetime(2),
        project_owner_id="ou-project",
        project_owner_name="项目负责人",
    )
    issue = ValidationIssue(
        table_name="需求主表",
        field_name="合板时间",
        message="invalid datetime",
        skip_scope="requirement",
    )

    report = RunReport(
        trigger="manual",
        started_at=aware_datetime(),
        requirement_risks=[risk],
        validation_issues=[issue],
        llm_attempted=True,
        llm_degraded=True,
        llm_failure_reasons=["timeout"],
    )

    assert report.requirement_risks == [risk]
    assert report.validation_issues == [issue]
    assert report.llm_attempted is True
    assert report.llm_failure_reasons == ["timeout"]


def test_risk_finding_deduplicates_stage_and_domain_references():
    finding = RiskFinding(
        reason_code="test_gate.at_incomplete",
        reason_text="AT 测试未通过，不能进入 PV",
        stage_refs=["AT 测试第一轮", "AT 测试第一轮"],
        domain_refs=["客户端", "客户端", "车辆"],
        level=RiskLevel.SEVERE,
        source="test_gate",
    )

    assert finding.stage_refs == ["AT 测试第一轮"]
    assert finding.domain_refs == ["客户端", "车辆"]


def test_risk_group_defaults_source_findings_to_empty():
    group = RiskGroup(
        reason_code="schedule.buffer_low",
        reason_text="剩余缓冲不足",
        level=RiskLevel.WARNING,
    )

    assert group.source_findings == []


def test_risk_family_model_carries_merged_scope_and_source_findings():
    source = RiskFinding(
        reason_code="schedule.buffer_negative",
        reason_text="剩余缓冲为负",
        stage_refs=["PV 测试第二轮"],
        domain_refs=["客户端"],
        level=RiskLevel.SEVERE,
        source="domain_schedule",
    )

    family = RiskFamily(
        code="node_delay_buffer_exhaustion",
        title="节点延期与缓冲耗尽",
        level=RiskLevel.SEVERE,
        stage_refs=["PV 测试第二轮"],
        domain_refs=["客户端"],
        source_findings=[source],
    )

    assert family.code == "node_delay_buffer_exhaustion"
    assert family.title == "节点延期与缓冲耗尽"
    assert family.level == RiskLevel.SEVERE
    assert family.stage_refs == ["PV 测试第二轮"]
    assert family.domain_refs == ["客户端"]
    assert family.source_findings == [source]


def test_node_and_requirement_risks_default_to_empty_findings():
    node_risk = NodeRisk(
        node_record_id="rec-node-risk",
        requirement_id="REQ-1",
        node_name="客户端开发",
        domain="客户端",
        owner_id="ou-owner",
        owner_name="张三",
        status=NodeStatus.IN_PROGRESS,
    )
    requirement_risk = RequirementRisk(
        requirement_record_id="rec-req-risk",
        requirement_id="REQ-1",
        requirement_name="需求进展提醒",
        project="工作小工具",
        target_version="1.0.0",
        merge_at=aware_datetime(3),
        project_owner_id="ou-project-owner",
        project_owner_name="李四",
    )

    assert node_risk.findings == []
    assert requirement_risk.findings == []


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


def test_requirement_launch_cannot_follow_merge():
    with pytest.raises(ValidationError, match="launch_at"):
        make_requirement(merge_at=aware_datetime(14), launch_at=aware_datetime(15))


def test_requirement_launch_can_precede_or_equal_merge():
    before = make_requirement(
        merge_at=aware_datetime(14),
        launch_at=aware_datetime(13),
    )
    same_time = make_requirement(
        merge_at=aware_datetime(14),
        launch_at=aware_datetime(14),
    )

    assert before.launch_at == aware_datetime(13)
    assert same_time.launch_at == aware_datetime(14)


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
            regression_days=3,
        )


def test_fixed_rules_supports_legacy_constructor_without_new_stage_days():
    rules = FixedRules(
        server_launch_weekdays={1, 3},
        server_launch_cutoff="17:30",
        checklist_days_before=1,
        at_workdays=8,
        at_natural_days=11,
        pv_days=3,
        regression_days=3,
    )

    assert (rules.at1_days, rules.at2_days, rules.pv1_days, rules.pv2_days) == (
        None,
        None,
        None,
        None,
    )


def test_fixed_rules_accepts_nonnegative_new_stage_days():
    rules = FixedRules(
        server_launch_weekdays={1, 3},
        server_launch_cutoff="17:30",
        checklist_days_before=1,
        at1_days=0,
        at2_days=1,
        pv1_days=2,
        pv2_days=3,
        regression_days=3,
    )

    assert (rules.at1_days, rules.at2_days, rules.pv1_days, rules.pv2_days) == (
        0,
        1,
        2,
        3,
    )


def test_run_report_finish_cannot_precede_start():
    with pytest.raises(ValidationError, match="finished_at"):
        RunReport(
            trigger="manual",
            started_at=aware_datetime(4),
            finished_at=aware_datetime(3),
        )
