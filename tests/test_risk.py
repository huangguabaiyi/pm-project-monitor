from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from requirement_monitor.models import (
    BaseConfig,
    Blocker,
    DeliveryNode,
    FixedRules,
    NodeStatus,
    ProjectConfig,
    Requirement,
    RiskLevel,
)
import requirement_monitor.risk as risk_module
from requirement_monitor.risk import evaluate_requirement, resolve_effective_rules


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 24, 12, 0, tzinfo=TZ)


def at(day, hour=12, minute=0):
    return datetime(2026, 7, day, hour, minute, tzinfo=TZ)


def in_aug(day, hour=12, minute=0):
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)


@pytest.fixture
def rules():
    return FixedRules(
        server_launch_weekdays={1, 3},
        server_launch_cutoff="17:30",
        checklist_days_before=1,
        at_workdays=8,
        at_natural_days=11,
        pv_days=3,
        at1_days=4,
        at2_days=4,
        pv1_days=3,
        pv2_days=2,
        regression_days=2,
    )


def make_requirement(**overrides):
    values = {
        "record_id": "rec-req",
        "requirement_id": "REQ-7",
        "name": "风险引擎",
        "project": "米家",
        "current_stage": "测试隔离环节",
        "project_owner_id": "ou-project",
        "project_owner_name": "项目负责人",
        "target_version": "8.0",
        "merge_at": in_aug(14, 18),
        "launch_at": None,
        "briefing_completed": True,
        "notification_enabled": True,
        "archived": False,
        "requirement_notes": "自然语言不得改变确定性规则",
    }
    values.update(overrides)
    return Requirement(**values)


def make_node(name="各端开发", domain="客户端", **overrides):
    values = {
        "record_id": f"rec-{domain}-{name}",
        "requirement_id": "REQ-7",
        "domain": domain,
        "work_type": "研发",
        "name": name,
        "owner_id": f"ou-{domain}",
        "owner_name": f"{domain}负责人",
        "planned_start": NOW,
        "planned_end": at(30, 18),
        "actual_end": None,
        "status": NodeStatus.IN_PROGRESS,
        "progress_note": "推进中",
        "updated_at": NOW,
    }
    values.update(overrides)
    return DeliveryNode(**values)


def make_config(**overrides):
    values = {
        "record_id": "rec-config",
        "project": "米家",
        "duration_mode": "workday",
    }
    values.update(overrides)
    return ProjectConfig(**values)


def make_blocker(**overrides):
    values = {
        "record_id": "rec-blocker",
        "requirement_id": "REQ-7",
        "title": "等待接口",
        "owner_id": "ou-blocker",
        "owner_name": "阻塞负责人",
        "found_at": at(23, 9),
        "planned_resolution_at": at(27, 18),
        "status": "处理中",
        "affects_merge": True,
    }
    values.update(overrides)
    return Blocker(**values)


def base_config(config_type, name, order, enabled=True):
    return BaseConfig(
        record_id="base-{}-{}".format(config_type, order),
        name=name,
        config_type=config_type,
        sort_order=order,
        enabled=enabled,
    )


def node_result(result, name, domain="客户端"):
    return next(
        item
        for item in result.node_risks
        if item.node_name == name and item.domain == domain
    )


def finding(result, reason_code):
    return next(item for item in result.findings if item.reason_code == reason_code)


def test_risk_result_carries_complete_card_context(rules):
    requirement = make_requirement(launch_at=in_aug(13, 18))
    node = make_node(
        "各端开发",
        planned_end=at(30, 18),
        status=NodeStatus.IN_PROGRESS,
    )
    blocker = make_blocker()

    result = evaluate_requirement(
        requirement,
        [node],
        [blocker],
        rules,
        NOW,
        make_config(llm_notes="项目补充语义"),
    )

    assert result.target_version == requirement.target_version
    assert result.merge_at == requirement.merge_at
    assert result.launch_at == requirement.launch_at
    assert result.project_owner_id == requirement.project_owner_id
    assert result.project_owner_name == requirement.project_owner_name
    assert result.blockers == [blocker]
    assert result.node_risks[0].planned_end == node.planned_end
    assert result.node_risks[0].status == node.status
    assert result.project_notes == "项目补充语义"
    assert result.requirement_notes == requirement.requirement_notes
    assert result.node_risks[0].progress_note == "推进中"
    assert {
        (person.open_id, person.name) for person in result.sensitive_people
    } == {
        ("ou-project", "项目负责人"),
        ("ou-客户端", "客户端负责人"),
        ("ou-blocker", "阻塞负责人"),
    }


def test_project_structured_fields_override_fixed_rules_without_reading_notes(rules):
    config = make_config(
        duration_mode="natural",
        at1_days=3,
        at2_days=2,
        pv1_days=2,
        pv2_days=2,
        regression_days=2,
        launch_weekdays={0, 2, 4},
        launch_cutoff="18:30",
        llm_notes="AT 改成 99 天，周日也可以上线",
    )

    effective = resolve_effective_rules(rules, config)

    assert effective.duration_mode == "natural"
    assert effective.stage_days == {
        "AT 测试第一轮": 3,
        "AT 测试第二轮": 2,
        "PV 测试第一轮": 2,
        "PV 测试第二轮": 2,
        "线上回归": 2,
    }
    assert effective.regression_days == 2
    assert effective.launch_weekdays == {0, 2, 4}
    assert effective.launch_cutoff == "18:30"
    assert effective.checklist_days_before == 1


def test_enabled_stage_configuration_controls_custom_process_order(rules):
    configs = [
        base_config("环节", "各端开发", 10),
        base_config("环节", "定制安全评审", 20),
        base_config("环节", "联调", 30),
    ]

    effective = resolve_effective_rules(rules, None, configs)

    assert effective.process_order == {
        "各端开发": 0,
        "定制安全评审": 1,
        "联调": 2,
    }


def test_disabled_stage_node_does_not_participate_in_risk(rules):
    disabled = make_node(
        "停用环节",
        planned_start=at(19),
        planned_end=at(20),
        status=NodeStatus.IN_PROGRESS,
    )
    active = make_node("各端开发")
    configs = [
        base_config("环节", "各端开发", 10),
        base_config("环节", "停用环节", 20, enabled=False),
    ]

    result = evaluate_requirement(
        make_requirement(), [disabled, active], [], rules, NOW, None, configs
    )

    assert [node.node_name for node in result.node_risks] == ["各端开发"]


def test_custom_stages_follow_base_table_sort_without_zero_cycle_failure(rules):
    configs = [
        base_config("环节", "定制评审A", 10),
        base_config("环节", "定制评审B", 20),
    ]
    later = make_node(
        "定制评审B",
        work_type="公共环节",
        planned_end=at(29),
    )
    earlier = make_node(
        "定制评审A",
        work_type="公共环节",
        planned_end=at(28),
    )

    result = evaluate_requirement(
        make_requirement(), [later, earlier], [], rules, NOW, None, configs
    )

    assert [node.node_name for node in result.node_risks] == [
        "定制评审A",
        "定制评审B",
    ]


def test_disabled_at2_is_not_synthesized_or_counted(rules):
    configs = [
        base_config("环节", "AT 测试第一轮", 10),
        base_config("环节", "AT 测试第二轮", 20, enabled=False),
    ]
    effective = resolve_effective_rules(rules, None, configs)
    at1 = make_node(
        "AT 测试第一轮",
        work_type="测试",
        planned_start=at(24),
        planned_end=at(31),
    )

    events = risk_module._build_events("客户端", [at1], effective)

    assert [(event.label, event.duration) for event in events] == [("AT1", 4)]


def test_each_test_round_and_custom_stage_use_their_own_configured_order(rules):
    configs = [
        base_config("环节", "AT 测试第一轮", 10),
        base_config("环节", "定制验收", 20),
        base_config("环节", "AT 测试第二轮", 30),
        base_config("环节", "PV 测试第一轮", 40),
        base_config("环节", "PV 测试第二轮", 50),
        base_config("环节", "线上回归", 60),
    ]
    effective = resolve_effective_rules(rules, None, configs)
    nodes = [
        make_node("PV 测试第二轮", work_type="测试"),
        make_node("AT 测试第二轮", work_type="测试"),
        make_node("定制验收", work_type="公共环节"),
        make_node("AT 测试第一轮", work_type="测试"),
        make_node("PV 测试第一轮", work_type="测试"),
    ]

    events = risk_module._build_events("客户端", nodes, effective)

    assert [event.label for event in events if event.node is not None] == [
        "AT1",
        "定制验收",
        "AT2",
        "PV1",
        "PV2",
    ]


def test_extra_at_round_uses_its_real_configured_stage_order(rules):
    configs = [
        base_config("环节", "AT 测试第一轮", 10),
        base_config("环节", "定制验收", 20),
        base_config("环节", "AT 测试第二轮", 30),
        base_config("环节", "定制发布评审", 40),
        base_config("环节", "AT 测试第3轮", 50),
    ]
    effective = resolve_effective_rules(rules, None, configs)
    nodes = [
        make_node("AT 测试第三轮", work_type="测试"),
        make_node("定制发布评审", work_type="公共环节"),
        make_node("AT 测试第二轮", work_type="测试"),
        make_node("定制验收", work_type="公共环节"),
        make_node("AT 测试第一轮", work_type="测试"),
    ]

    events = risk_module._build_events("客户端", nodes, effective)

    assert [event.label for event in events if event.node is not None] == [
        "AT1",
        "定制验收",
        "AT2",
        "定制发布评审",
        "AT3",
    ]


def test_extra_pv_round_uses_its_real_configured_stage_order(rules):
    configs = [
        base_config("环节", "PV 测试第一轮", 10),
        base_config("环节", "定制验收", 20),
        base_config("环节", "PV 测试第二轮", 30),
        base_config("环节", "定制发布评审", 40),
        base_config("环节", "PV 测试第三轮", 50),
    ]
    effective = resolve_effective_rules(rules, None, configs)
    nodes = [
        make_node("PV 测试第3轮", work_type="测试"),
        make_node("定制发布评审", work_type="公共环节"),
        make_node("PV 测试第二轮", work_type="测试"),
        make_node("定制验收", work_type="公共环节"),
        make_node("PV 测试第一轮", work_type="测试"),
    ]

    events = risk_module._build_events("客户端", nodes, effective)

    assert [event.label for event in events if event.node is not None] == [
        "PV1",
        "定制验收",
        "PV2",
        "定制发布评审",
        "PV3",
    ]


def test_disabled_extra_test_round_config_excludes_matching_node(rules):
    configs = [
        base_config("环节", "AT 测试第一轮", 10),
        base_config("环节", "AT 测试第3轮", 20, enabled=False),
        base_config("环节", "PV 测试第一轮", 30),
        base_config("环节", "PV 测试第三轮", 40, enabled=False),
    ]
    effective = resolve_effective_rules(rules, None, configs)
    nodes = [
        make_node("AT 测试第一轮", work_type="测试"),
        make_node("AT 测试第三轮", work_type="测试"),
        make_node("PV 测试第一轮", work_type="测试"),
        make_node("PV 测试第3轮", work_type="测试"),
    ]

    events = risk_module._build_events("客户端", nodes, effective)

    assert [event.label for event in events if event.node is not None] == [
        "AT1",
        "PV1",
    ]


def test_disabled_domain_test_role_excludes_only_test_nodes(rules):
    configs = [
        base_config("测试角色", "客户端测试", 10, enabled=False),
    ]
    development = make_node("各端开发", work_type="研发")
    testing = make_node("AT 测试第一轮", work_type="测试")

    result = evaluate_requirement(
        make_requirement(), [development, testing], [], rules, NOW, None, configs
    )

    assert [node.node_name for node in result.node_risks] == ["各端开发"]


def test_dynamic_enabled_test_role_allows_new_delivery_domain(rules):
    configs = [base_config("测试角色", "插件测试", 10)]
    plugin_test = make_node(
        "插件专项测试",
        domain="插件",
        work_type="测试",
    )

    result = evaluate_requirement(
        make_requirement(), [plugin_test], [], rules, NOW, None, configs
    )

    assert [node.node_name for node in result.node_risks] == ["插件专项测试"]


def test_fixed_stage_durations_are_independent_from_day_mode(rules):
    workday = resolve_effective_rules(rules, make_config(duration_mode="workday"))
    natural = resolve_effective_rules(rules, make_config(duration_mode="natural"))

    assert workday.stage_days == natural.stage_days == {
        "AT 测试第一轮": 4,
        "AT 测试第二轮": 4,
        "PV 测试第一轮": 3,
        "PV 测试第二轮": 2,
        "线上回归": 2,
    }


def test_safe_deadline_uses_the_project_day_mode(rules):
    nodes = [
        make_node(
            "AT 测试第一轮",
            work_type="测试",
            planned_start=at(24),
            planned_end=None,
        ),
        make_node(
            "AT 测试第二轮",
            work_type="测试",
            planned_start=at(29),
            planned_end=None,
        ),
    ]
    requirement = make_requirement(merge_at=in_aug(3, 18))

    workday = evaluate_requirement(
        requirement,
        nodes,
        [],
        rules,
        NOW,
        project_config=make_config(
            duration_mode="workday",
            at1_days=3,
            at2_days=2,
            pv1_days=0,
            pv2_days=0,
            regression_days=0,
        ),
    )
    natural = evaluate_requirement(
        requirement,
        nodes,
        [],
        rules,
        NOW,
        project_config=make_config(
            duration_mode="natural",
            at1_days=3,
            at2_days=2,
            pv1_days=0,
            pv2_days=0,
            regression_days=0,
        ),
    )

    assert node_result(workday, "AT 测试第一轮").safe_deadline == at(30, 18)
    assert node_result(natural, "AT 测试第一轮").safe_deadline == in_aug(1, 18)


def test_at_rounds_use_independent_project_durations(rules):
    config = make_config(
        at1_days=3,
        at2_days=2,
        pv1_days=0,
        pv2_days=0,
        regression_days=0,
    )
    nodes = [
        make_node(
            "AT 测试第一轮",
            work_type="测试",
            planned_start=at(27),
            planned_end=None,
        ),
        make_node(
            "AT 测试第二轮",
            work_type="测试",
            planned_start=at(30),
            planned_end=None,
        ),
    ]

    result = evaluate_requirement(
        make_requirement(current_stage="开发"),
        nodes,
        [],
        rules,
        NOW,
        project_config=config,
    )

    assert node_result(result, "AT 测试第一轮").safe_deadline == in_aug(12, 18)
    assert node_result(result, "AT 测试第二轮").safe_deadline == in_aug(14, 18)


def test_pv_rounds_and_regression_are_independent_downstream_stages(rules):
    config = make_config(
        at1_days=0,
        at2_days=0,
        pv1_days=2,
        pv2_days=1,
        regression_days=3,
    )
    nodes = [
        make_node("PV 测试第一轮", work_type="测试", planned_end=None),
        make_node("PV 测试第二轮", work_type="测试", planned_end=None),
        make_node("线上回归", work_type="测试", planned_end=None),
    ]

    result = evaluate_requirement(
        make_requirement(), nodes, [], rules, NOW, project_config=config
    )

    assert node_result(result, "PV 测试第一轮").safe_deadline == in_aug(10, 18)
    assert node_result(result, "PV 测试第二轮").safe_deadline == in_aug(11, 18)
    assert node_result(result, "线上回归").safe_deadline == in_aug(14, 18)


def test_schedule_formula_exposes_only_remaining_key_stages(rules):
    config = make_config(
        at1_days=0,
        at2_days=0,
        pv1_days=2,
        pv2_days=1,
        regression_days=1,
    )
    nodes = [
        make_node("PV 测试第一轮", work_type="测试", planned_end=None),
        make_node("PV 测试第二轮", work_type="测试", planned_end=None),
        make_node("线上回归", work_type="测试", planned_end=None),
    ]

    result = evaluate_requirement(
        make_requirement(), nodes, [], rules, NOW, project_config=config
    )

    assert result.schedule_formula is not None
    assert result.schedule_formula.domain == "客户端"
    assert [term.label for term in result.schedule_formula.terms] == [
        "PV 测试第一轮",
        "PV 测试第二轮",
        "线上回归",
    ]
    assert [term.days for term in result.schedule_formula.terms] == [2, 1, 1]
    assert (
        result.schedule_formula.predicted_completion
        == result.predicted_completion
    )


def test_requirement_prediction_is_the_latest_domain_prediction(rules):
    nodes = [
        make_node("各端开发", domain="客户端", planned_end=in_aug(5, 18)),
        make_node("各端开发", domain="服务端", planned_end=in_aug(7, 18)),
    ]

    result = evaluate_requirement(make_requirement(), nodes, [], rules, NOW)

    assert result.predicted_completion == in_aug(7, 18)


def test_normal_when_no_deterministic_rule_is_violated(rules):
    result = evaluate_requirement(
        make_requirement(), [make_node(planned_end=at(30, 18))], [], rules, NOW
    )

    assert result.level == RiskLevel.NORMAL
    assert result.reasons == []


def test_overdue_node_with_remaining_buffer_is_warning(rules):
    node = make_node(planned_start=at(20, 9), planned_end=at(23, 18))

    result = evaluate_requirement(make_requirement(), [node], [], rules, NOW)

    assert result.level == RiskLevel.WARNING
    assert "节点延期但仍有缓冲" in result.reasons


def test_buffer_of_two_days_or_less_is_warning(rules):
    config = make_config(
        at1_days=4,
        at2_days=4,
        pv1_days=0,
        pv2_days=0,
        regression_days=0,
    )
    nodes = [
        make_node(
            "AT 测试第一轮",
            work_type="测试",
            planned_start=at(24),
            planned_end=at(30),
        ),
        make_node(
            "AT 测试第二轮",
            work_type="测试",
            planned_start=at(30),
            planned_end=in_aug(5),
        ),
    ]
    requirement = make_requirement(merge_at=in_aug(7, 18))

    result = evaluate_requirement(
        requirement, nodes, [], rules, NOW, project_config=config
    )

    assert result.level == RiskLevel.WARNING
    assert "剩余缓冲不超过2天" in result.reasons


def test_buffer_low_exposes_missing_schedule_stages(rules):
    nodes = [
        make_node(
            "PV 测试第一轮",
            work_type="测试",
            planned_start=NOW,
            planned_end=at(27),
            status=NodeStatus.NOT_STARTED,
        ),
        make_node(
            "PV 测试第二轮",
            work_type="测试",
            planned_start=at(27),
            planned_end=None,
            status=NodeStatus.NOT_STARTED,
        ),
    ]
    config = make_config(
        at1_days=0,
        at2_days=0,
        pv1_days=3,
        pv2_days=1,
        regression_days=1,
    )

    result = evaluate_requirement(
        make_requirement(current_stage="PV 测试第一轮", merge_at=at(31)),
        nodes,
        [],
        rules,
        NOW,
        project_config=config,
    )

    buffer_finding = finding(result, "schedule.buffer_low")
    assert buffer_finding.stage_refs == ["PV 测试第二轮", "线上回归"]


def test_buffer_low_keeps_exhausted_unscheduled_stage_visible(rules):
    node = make_node(
        "PV 测试第一轮",
        work_type="测试",
        planned_start=at(31),
        planned_end=None,
        status=NodeStatus.IN_PROGRESS,
    )
    config = make_config(
        at1_days=0,
        at2_days=0,
        pv1_days=1,
        pv2_days=0,
        regression_days=0,
    )

    result = evaluate_requirement(
        make_requirement(current_stage="PV 测试第一轮", merge_at=in_aug(4)),
        [node],
        [],
        rules,
        in_aug(3),
        project_config=config,
        base_configs=[base_config("环节", "PV 测试第一轮", 10)],
    )

    buffer_finding = finding(result, "schedule.buffer_low")
    assert buffer_finding.stage_refs == ["PV 测试第一轮"]


def test_two_day_buffer_warning_always_uses_workdays(rules):
    node = make_node(planned_end=at(31, 18))
    requirement = make_requirement(merge_at=in_aug(3, 18))
    config = make_config(
        duration_mode="natural",
        at1_days=0,
        at2_days=0,
        pv1_days=0,
        pv2_days=0,
        regression_days=0,
    )

    result = evaluate_requirement(
        requirement, [node], [], rules, NOW, project_config=config
    )

    assert result.level == RiskLevel.WARNING
    assert result.buffer_days == 1
    assert "剩余缓冲不超过2天" in result.reasons


def test_minimum_test_duration_that_cannot_fit_is_severe(rules):
    nodes = [
        make_node("AT 测试第一轮", work_type="测试", planned_end=None),
        make_node("AT 测试第二轮", work_type="测试", planned_end=None),
    ]
    requirement = make_requirement(merge_at=at(31, 18))

    result = evaluate_requirement(requirement, nodes, [], rules, NOW)

    assert result.level == RiskLevel.SEVERE
    assert any("AT" in reason and "无法容纳" in reason for reason in result.reasons)


def test_stale_update_for_two_workdays_is_warning(rules):
    node = make_node(planned_start=at(20, 9), updated_at=at(22, 12))

    result = evaluate_requirement(make_requirement(), [node], [], rules, NOW)

    assert result.level == RiskLevel.WARNING
    assert "连续2个工作日没有进展更新" in result.reasons


def test_missing_update_is_stale_two_workdays_after_node_start(rules):
    node = make_node(planned_start=at(22, 9), updated_at=None)

    result = evaluate_requirement(make_requirement(), [node], [], rules, NOW)

    assert result.level == RiskLevel.WARNING
    assert "连续2个工作日没有进展更新" in result.reasons


def test_in_progress_node_without_planned_start_uses_updated_at_for_staleness(rules):
    node = make_node(
        planned_start=None,
        updated_at=at(22, 9),
        status=NodeStatus.IN_PROGRESS,
    )

    result = evaluate_requirement(make_requirement(), [node], [], rules, NOW)

    assert result.level == RiskLevel.WARNING
    assert "连续2个工作日没有进展更新" in result.reasons


def test_future_node_does_not_trigger_stale_update_warning(rules):
    node = make_node(
        planned_start=at(27, 9),
        updated_at=at(20, 9),
        status=NodeStatus.NOT_STARTED,
    )

    result = evaluate_requirement(make_requirement(), [node], [], rules, NOW)

    assert result.level == RiskLevel.NORMAL
    assert "连续2个工作日没有进展更新" not in result.reasons


def test_stale_update_baseline_never_precedes_planned_start(rules):
    node = make_node(planned_start=at(23, 9), updated_at=at(20, 9))

    result = evaluate_requirement(make_requirement(), [node], [], rules, NOW)

    assert result.level == RiskLevel.NORMAL
    assert "连续2个工作日没有进展更新" not in result.reasons


def test_blocker_due_within_one_workday_is_warning(rules):
    blocker = make_blocker(planned_resolution_at=at(27, 18))

    result = evaluate_requirement(
        make_requirement(), [make_node()], [blocker], rules, NOW
    )

    assert result.level == RiskLevel.WARNING
    assert "阻塞项将在1个工作日内到期" in result.reasons


def test_overdue_merge_blocker_is_severe(rules):
    blocker = make_blocker(planned_resolution_at=at(23, 18))

    result = evaluate_requirement(
        make_requirement(), [make_node()], [blocker], rules, NOW
    )

    assert result.level == RiskLevel.SEVERE
    assert "影响合板的阻塞项已超期" in result.reasons


def test_overdue_non_merge_blocker_is_warning(rules):
    blocker = make_blocker(
        planned_resolution_at=at(23, 18),
        affects_merge=False,
    )

    result = evaluate_requirement(
        make_requirement(), [make_node()], [blocker], rules, NOW
    )

    assert result.level == RiskLevel.WARNING
    assert "阻塞项已超期" in result.reasons


@pytest.mark.parametrize(
    "launch_at, expected_reason",
    [
        (in_aug(19, 17), "服务端上线日期不符合允许星期"),
        (in_aug(18, 17, 31), "服务端上线时间晚于17:30"),
    ],
)
def test_server_wednesday_or_after_1730_is_severe(rules, launch_at, expected_reason):
    requirement = make_requirement(merge_at=in_aug(21, 18), launch_at=launch_at)
    server_node = make_node(domain="服务端")

    result = evaluate_requirement(requirement, [server_node], [], rules, NOW)

    assert result.level == RiskLevel.SEVERE
    assert expected_reason in result.reasons


def test_incomplete_checklist_is_severe_on_day_before_launch(rules):
    now = at(27, 9)
    requirement = make_requirement(merge_at=at(30, 18), launch_at=at(28, 17))
    nodes = [
        make_node(domain="服务端", planned_end=at(27, 8), updated_at=now),
        make_node(
            "上线 Checklist",
            domain="服务端",
            work_type="发布",
            planned_start=None,
            planned_end=at(27, 17),
            status=NodeStatus.NOT_STARTED,
            updated_at=now,
        ),
    ]

    result = evaluate_requirement(requirement, nodes, [], rules, now)

    assert result.level == RiskLevel.SEVERE
    assert "服务端上线 Checklist 未完成" in result.reasons
    checklist_risk = node_result(result, "上线 Checklist", "服务端")
    assert checklist_risk.level == RiskLevel.SEVERE
    assert "服务端上线 Checklist 未完成" in checklist_risk.reasons
    assert checklist_risk.safe_deadline == at(27, 17)


def test_explicit_server_launch_node_does_not_require_legacy_checklist(rules):
    now = in_aug(19, 9)
    requirement = make_requirement(merge_at=in_aug(21, 18), launch_at=in_aug(20, 17))
    server_launch = make_node(
        "服务端上线",
        domain="服务端",
        work_type="发布",
        planned_start=in_aug(19, 9),
        planned_end=in_aug(19, 17),
        status=NodeStatus.IN_PROGRESS,
        updated_at=now,
    )

    result = evaluate_requirement(requirement, [server_launch], [], rules, now)

    assert "服务端上线 Checklist 未完成" not in result.reasons
    assert result.launch_at == server_launch.planned_end


def test_explicit_server_launch_node_drives_launch_rule_checks(rules):
    server_launch = make_node(
        "服务端上线",
        domain="服务端",
        work_type="发布",
        planned_start=in_aug(18, 9),
        planned_end=in_aug(19, 17),
        status=NodeStatus.NOT_STARTED,
    )

    result = evaluate_requirement(
        make_requirement(merge_at=in_aug(21, 18), launch_at=None),
        [server_launch],
        [],
        rules,
        NOW,
    )

    assert result.launch_at == server_launch.planned_end
    assert "服务端上线日期不符合允许星期" in result.reasons


def test_checklist_only_participates_in_launch_checks_not_domain_aggregation(rules):
    now = in_aug(17, 9)
    requirement = make_requirement(
        merge_at=in_aug(18, 18),
        launch_at=in_aug(18, 17),
    )
    client_done = make_node(
        domain="客户端",
        planned_end=in_aug(10, 18),
        actual_end=in_aug(10, 18),
        status=NodeStatus.COMPLETED,
    )
    server_done = make_node(
        domain="服务端",
        planned_end=in_aug(8, 18),
        actual_end=in_aug(8, 18),
        status=NodeStatus.COMPLETED,
    )
    checklist = make_node(
        "上线 Checklist",
        domain="服务端",
        work_type="发布",
        planned_start=None,
        planned_end=in_aug(18, 16),
        status=NodeStatus.NOT_STARTED,
        updated_at=now,
    )

    result = evaluate_requirement(
        requirement,
        [client_done, server_done, checklist],
        [],
        rules,
        now,
    )

    assert result.predicted_completion == in_aug(10, 18)
    assert result.buffer_days == 1
    assert not any(
        "交付域预计完成时间晚于合板时间" in item for item in result.reasons
    )
    checklist_risk = node_result(result, "上线 Checklist", "服务端")
    assert checklist_risk.level == RiskLevel.SEVERE
    assert checklist_risk.buffer_days is None
    assert checklist_risk.reasons == ["服务端上线 Checklist 未完成"]


def test_completed_downstream_stages_do_not_consume_remaining_duration(rules):
    config = make_config(
        at1_days=0,
        at2_days=0,
        pv1_days=2,
        pv2_days=1,
        regression_days=3,
    )
    completed_at = at(23, 18)
    nodes = [
        make_node(
            "AT 测试第一轮",
            work_type="测试",
            planned_start=None,
            planned_end=None,
            status=NodeStatus.SKIPPED,
        ),
        make_node(
            "AT 测试第二轮",
            work_type="测试",
            planned_start=None,
            planned_end=None,
            status=NodeStatus.SKIPPED,
        ),
        make_node(
            "各端开发",
            planned_start=at(15),
            planned_end=at(16),
            actual_end=at(16),
            status=NodeStatus.COMPLETED,
        ),
        make_node(
            "PV 测试第一轮",
            work_type="测试",
            planned_start=at(17),
            planned_end=at(21),
            actual_end=at(21),
            status=NodeStatus.COMPLETED,
        ),
        make_node(
            "PV 测试第二轮",
            work_type="测试",
            planned_start=at(21),
            planned_end=at(22),
            actual_end=at(22),
            status=NodeStatus.COMPLETED,
        ),
        make_node(
            "线上回归",
            work_type="测试",
            planned_start=at(20),
            planned_end=completed_at,
            actual_end=completed_at,
            status=NodeStatus.COMPLETED,
        ),
        make_node(
            "多语言翻译",
            work_type="发布",
            planned_start=at(22),
            planned_end=completed_at,
            actual_end=completed_at,
            status=NodeStatus.COMPLETED,
        ),
        make_node(
            "版本合入",
            planned_start=completed_at,
            planned_end=completed_at,
            actual_end=completed_at,
            status=NodeStatus.COMPLETED,
        ),
    ]

    result = evaluate_requirement(
        make_requirement(), nodes, [], rules, NOW, project_config=config
    )

    assert result.level == RiskLevel.NORMAL
    assert result.predicted_completion == completed_at
    assert result.buffer_days > 0


@pytest.mark.parametrize("current_stage", ["多语言翻译", "版本合入"])
def test_incomplete_translation_is_warning_at_or_after_translation(rules, current_stage):
    node = make_node("多语言翻译", work_type="发布", planned_start=at(25), planned_end=at(28), status=NodeStatus.IN_PROGRESS)
    regression = make_node(
        "线上回归",
        work_type="发布",
        planned_start=at(20),
        planned_end=at(24),
        actual_end=at(24),
        status=NodeStatus.COMPLETED,
    )

    result = evaluate_requirement(
        make_requirement(current_stage=current_stage), [node, regression], [], rules, NOW
    )

    assert result.level == RiskLevel.WARNING
    assert "多语言翻译未完成，建议合板前完成" in result.reasons


def test_failed_test_gate_marks_the_failed_test_node(rules):
    nodes = [
        make_node(
            "AT 测试第一轮",
            work_type="测试",
            planned_start=at(25),
            planned_end=at(27),
            status=NodeStatus.IN_PROGRESS,
        ),
        make_node(
            "AT 测试第二轮",
            work_type="测试",
            planned_start=at(27),
            planned_end=at(29),
            status=NodeStatus.NOT_STARTED,
        ),
    ]

    result = evaluate_requirement(
        make_requirement(current_stage="PV 测试第一轮"), nodes, [], rules, NOW
    )

    at1_risk = node_result(result, "AT 测试第一轮")
    at2_risk = node_result(result, "AT 测试第二轮")
    assert at1_risk.level == RiskLevel.SEVERE
    assert at2_risk.level == RiskLevel.SEVERE
    assert any("AT1 测试未通过" in reason for reason in at1_risk.reasons)
    assert any("AT2 测试未通过" in reason for reason in at2_risk.reasons)
    assert any(
        item.reason_code == "test_gate.at_incomplete"
        for item in at1_risk.findings
    )
    assert any(
        item.reason_code == "test_gate.at_incomplete"
        for item in at2_risk.findings
    )


def test_far_future_blocker_does_not_mark_domain_as_affected(rules):
    node = make_node()
    blocker = make_blocker(
        node_record_id=node.record_id,
        planned_resolution_at=in_aug(10, 18),
    )

    result = evaluate_requirement(make_requirement(), [node], [blocker], rules, NOW)

    assert result.level == RiskLevel.NORMAL
    assert result.affected_domains == []


def test_missing_near_term_test_start_schedule_is_warning(rules):
    config = make_config(at1_days=1, at2_days=1, pv1_days=0, pv2_days=0, regression_days=0)
    node = make_node(
        "AT 测试第一轮",
        work_type="测试",
        planned_start=None,
        planned_end=at(27, 18),
    )
    requirement = make_requirement(merge_at=at(28, 18))

    result = evaluate_requirement(
        requirement, [node], [], rules, NOW, project_config=config
    )

    assert result.level == RiskLevel.WARNING
    assert "测试排期缺少计划开始时间：客户端｜AT 测试第一轮" in result.reasons


def test_missing_next_test_round_does_not_extend_budget_or_raise_risk(rules):
    config = make_config(at1_days=1, at2_days=1, pv1_days=0, pv2_days=0, regression_days=0)
    completed_at = at(24, 10)
    node = make_node(
        "AT 测试第一轮",
        work_type="测试",
        planned_start=at(23, 10),
        planned_end=completed_at,
        actual_end=completed_at,
        status=NodeStatus.COMPLETED,
    )
    requirement = make_requirement(merge_at=at(27, 18))

    result = evaluate_requirement(
        requirement, [node], [], rules, NOW, project_config=config
    )

    assert result.level == RiskLevel.NORMAL
    assert result.predicted_completion == completed_at
    assert not any("AT 测试第二轮" in reason for reason in result.reasons)


def test_in_progress_test_budget_deducts_elapsed_project_days(rules):
    config = make_config(at1_days=2, at2_days=2, pv1_days=0, pv2_days=0, regression_days=0)
    completed_upstream = make_node(
        "提测",
        planned_start=at(21),
        planned_end=at(22),
        actual_end=at(22),
        status=NodeStatus.COMPLETED,
    )
    in_progress_test = make_node(
        "AT 测试第一轮",
        work_type="测试",
        planned_start=at(22),
        planned_end=NOW,
        status=NodeStatus.IN_PROGRESS,
    )

    result = evaluate_requirement(
        make_requirement(),
        [completed_upstream, in_progress_test],
        [],
        rules,
        NOW,
        project_config=config,
    )

    assert result.predicted_completion == NOW
    assert node_result(result, "提测").safe_deadline == in_aug(14, 18)


def test_in_progress_test_without_planned_start_keeps_full_budget(rules):
    config = make_config(at1_days=2, at2_days=2, pv1_days=0, pv2_days=0, regression_days=0)
    node = make_node(
        "AT 测试第一轮",
        work_type="测试",
        planned_start=None,
        planned_end=None,
        status=NodeStatus.IN_PROGRESS,
    )

    result = evaluate_requirement(
        make_requirement(), [node], [], rules, NOW, project_config=config
    )

    assert result.predicted_completion == at(28)


def test_parallel_nodes_in_same_phase_use_max_budget_and_completion(rules):
    config = make_config(at1_days=0, at2_days=0, pv1_days=2, pv2_days=2, regression_days=0)
    nodes = [
        make_node(
            "PV1 客户端主链路",
            work_type="测试",
            record_id="rec-pv1-main",
            planned_start=NOW,
            planned_end=None,
        ),
        make_node(
            "PV1 客户端兼容性",
            work_type="测试",
            record_id="rec-pv1-compat",
            planned_start=NOW,
            planned_end=None,
        ),
        make_node(
            "PV2 客户端主链路",
            work_type="测试",
            record_id="rec-pv2-main",
            planned_start=at(29),
            planned_end=None,
            status=NodeStatus.NOT_STARTED,
        ),
    ]

    result = evaluate_requirement(
        make_requirement(), nodes, [], rules, NOW, project_config=config
    )

    assert result.predicted_completion == at(31)
    assert node_result(result, "PV1 客户端主链路").safe_deadline == in_aug(12, 18)
    assert node_result(result, "PV1 客户端兼容性").safe_deadline == in_aug(12, 18)


def test_extra_at_and_pv_rounds_use_explicit_planned_completion(rules):
    config = make_config(at1_days=1, at2_days=1, pv1_days=2, pv2_days=2, regression_days=3)
    nodes = [
        make_node(
            "AT3 补充轮次",
            work_type="测试",
            planned_start=NOW,
            planned_end=at(29),
        ),
        make_node(
            "PV3 补充轮次",
            work_type="测试",
            planned_start=None,
            planned_end=at(31),
            status=NodeStatus.NOT_STARTED,
        ),
    ]

    result = evaluate_requirement(
        make_requirement(), nodes, [], rules, NOW, project_config=config
    )

    assert result.predicted_completion == at(31)
    assert node_result(result, "AT3 补充轮次").safe_deadline == in_aug(14, 18)
    assert node_result(result, "PV3 补充轮次").safe_deadline == in_aug(14, 18)


@pytest.mark.parametrize(
    "actual_end, planned_end",
    [
        (at(21), at(22)),
        (None, at(21)),
    ],
)
def test_completed_test_window_does_not_trigger_minimum_duration_risk(
    rules, actual_end, planned_end
):
    config = make_config(at1_days=2, at2_days=2, pv1_days=0, pv2_days=0, regression_days=0)
    node = make_node(
        "AT 测试第一轮",
        work_type="测试",
        planned_start=at(20),
        planned_end=planned_end,
        actual_end=actual_end,
        status=NodeStatus.COMPLETED,
    )

    result = evaluate_requirement(
        make_requirement(), [node], [], rules, NOW, project_config=config
    )

    assert node_result(result, "AT 测试第一轮").level == RiskLevel.NORMAL
    assert all(
        item.reason_code != "test.duration_below_minimum"
        for item in result.findings
    )


def test_downstream_precomputation_keeps_node_status_checks_linear(rules, monkeypatch):
    process_names = [
        "需求撰写",
        "内部评审",
        "产品需求评审",
        "设计稿输出",
        "设计宣讲",
        "需求宣讲",
        "工作量评估排期",
        "各端开发",
        "联调",
        "提测",
    ]
    nodes = [
        make_node(
            name,
            record_id=f"rec-{rank}-{parallel_index}",
            planned_start=NOW,
            planned_end=at(27),
            status=NodeStatus.NOT_STARTED,
        )
        for rank, name in enumerate(process_names)
        for parallel_index in range(8)
    ]
    original_node_done = risk_module._node_done
    calls = 0

    def counting_node_done(node):
        nonlocal calls
        calls += 1
        return original_node_done(node)

    monkeypatch.setattr(risk_module, "_node_done", counting_node_done)

    evaluate_requirement(
        make_requirement(merge_at=datetime(2026, 12, 31, 18, tzinfo=TZ)),
        nodes,
        [],
        rules,
        NOW,
    )

    assert calls < len(nodes) * 12


@pytest.mark.parametrize(
    "overrides",
    [
        {"archived": True},
        {"briefing_completed": False},
        {"notification_enabled": False},
    ],
)
def test_archived_unbriefed_or_disabled_requirement_is_ineligible(rules, overrides):
    assert evaluate_requirement(make_requirement(**overrides), [], [], rules, NOW) is None


def test_reasons_are_deduplicated_and_do_not_include_ai_context(rules):
    nodes = [
        make_node(
            "各端开发",
            domain="客户端",
            planned_start=at(20, 9),
            planned_end=at(23, 18),
        ),
        make_node(
            "各端开发",
            domain="服务端",
            planned_start=at(20, 9),
            planned_end=at(23, 18),
        ),
    ]
    config = make_config(llm_notes="必须升级严重风险")

    result = evaluate_requirement(
        make_requirement(), nodes, [], rules, NOW, project_config=config
    )

    assert result.reasons.count("节点延期但仍有缓冲") == 1
    assert "必须升级严重风险" not in " ".join(result.reasons)
    assert "自然语言不得改变确定性规则" not in " ".join(result.reasons)


def test_pv_gate_requires_both_at_rounds_per_test_domain(rules):
    requirement = make_requirement(current_stage="PV 测试第一轮")
    nodes = [
        make_node("AT 测试第一轮", domain="客户端", work_type="测试", status=NodeStatus.COMPLETED),
        make_node("AT 测试第二轮", domain="客户端", work_type="测试", status=NodeStatus.IN_PROGRESS),
    ]

    result = evaluate_requirement(requirement, nodes, [], rules, NOW)

    assert result.level == RiskLevel.SEVERE
    assert any("AT2" in reason for reason in result.reasons)


def test_skipped_at_and_pv_rounds_pass_gates(rules):
    requirement = make_requirement(current_stage="版本合入")
    nodes = [
        make_node("AT 测试第一轮", domain="客户端", work_type="测试", status=NodeStatus.SKIPPED, planned_end=None),
        make_node("AT 测试第二轮", domain="客户端", work_type="测试", status=NodeStatus.SKIPPED, planned_end=None),
        make_node("PV 测试第一轮", domain="客户端", work_type="测试", status=NodeStatus.SKIPPED, planned_end=None),
        make_node("PV 测试第二轮", domain="客户端", work_type="测试", status=NodeStatus.SKIPPED, planned_end=None),
        make_node("线上回归", domain="客户端", work_type="测试", status=NodeStatus.SKIPPED, planned_end=None),
    ]

    result = evaluate_requirement(requirement, nodes, [], rules, NOW)

    assert result.level == RiskLevel.NORMAL
    assert "多语言翻译" in result.process_reminders
    assert not any("多语言翻译" in reason for reason in result.reasons)
    assert not any("AT1" in reason or "AT2" in reason or "PV1" in reason or "PV2" in reason for reason in result.reasons)


def test_skipped_nodes_without_dates_do_not_make_completed_domain_late(rules):
    merge_at = at(28, 0)
    now = at(28, 20)
    config = make_config(
        at1_days=0,
        at2_days=0,
        pv1_days=0,
        pv2_days=0,
        regression_days=0,
    )
    nodes = [
        make_node(
            "AT 测试第一轮",
            work_type="测试",
            status=NodeStatus.SKIPPED,
            planned_start=None,
            planned_end=None,
        ),
        make_node(
            "AT 测试第二轮",
            work_type="测试",
            status=NodeStatus.SKIPPED,
            planned_start=None,
            planned_end=None,
        ),
        make_node(
            "PV 测试第一轮",
            work_type="测试",
            status=NodeStatus.COMPLETED,
            planned_start=at(23, 0),
            planned_end=merge_at,
            actual_end=merge_at,
        ),
        make_node(
            "PV 测试第二轮",
            work_type="测试",
            status=NodeStatus.COMPLETED,
            planned_start=at(27, 0),
            planned_end=merge_at,
            actual_end=merge_at,
        ),
        make_node(
            "版本合入",
            status=NodeStatus.COMPLETED,
            planned_start=merge_at,
            planned_end=merge_at,
            actual_end=merge_at,
        ),
        make_node(
            "线上回归",
            work_type="测试",
            status=NodeStatus.SKIPPED,
            planned_start=None,
            planned_end=None,
        ),
        make_node(
            "多语言翻译",
            work_type="发布",
            status=NodeStatus.SKIPPED,
            planned_start=None,
            planned_end=None,
        ),
    ]

    result = evaluate_requirement(
        make_requirement(current_stage="PV 测试第一轮", merge_at=merge_at),
        nodes,
        [],
        rules,
        now,
        project_config=config,
    )

    assert result.level == RiskLevel.NORMAL
    assert result.predicted_completion == merge_at
    assert not any("预计完成时间晚于合板时间" in reason for reason in result.reasons)
    assert "剩余缓冲不超过2天" not in result.reasons


def test_current_stage_advances_after_all_parallel_nodes_complete(rules):
    config = make_config(
        at1_days=0,
        at2_days=0,
        pv1_days=0,
        pv2_days=0,
        regression_days=0,
    )
    nodes = [
        make_node(
            "AT 测试第一轮",
            work_type="测试",
            status=NodeStatus.SKIPPED,
            planned_end=None,
        ),
        make_node(
            "AT 测试第二轮",
            work_type="测试",
            status=NodeStatus.SKIPPED,
            planned_end=None,
        ),
        make_node(
            "PV 测试第一轮",
            domain="客户端",
            work_type="测试",
            status=NodeStatus.COMPLETED,
            actual_end=at(24),
        ),
        make_node(
            "PV 测试第一轮",
            domain="车辆",
            work_type="测试",
            status=NodeStatus.SKIPPED,
            planned_end=None,
        ),
        make_node(
            "PV 测试第二轮",
            work_type="测试",
            status=NodeStatus.NOT_STARTED,
            planned_start=at(25),
            planned_end=at(28),
        ),
    ]

    result = evaluate_requirement(
        make_requirement(current_stage="PV 测试第一轮"),
        nodes,
        [],
        rules,
        NOW,
        project_config=config,
    )

    assert result.current_stage == "PV 测试第二轮"


def test_current_stage_waits_for_every_parallel_node(rules):
    nodes = [
        make_node(
            "PV 测试第一轮",
            domain="客户端",
            work_type="测试",
            status=NodeStatus.COMPLETED,
            actual_end=at(24),
        ),
        make_node(
            "PV 测试第一轮",
            domain="车辆",
            work_type="测试",
            status=NodeStatus.IN_PROGRESS,
        ),
        make_node(
            "PV 测试第二轮",
            work_type="测试",
            status=NodeStatus.NOT_STARTED,
        ),
    ]

    result = evaluate_requirement(
        make_requirement(current_stage="PV 测试第一轮"),
        nodes,
        [],
        rules,
        NOW,
    )

    assert result.current_stage == "PV 测试第一轮"


def test_current_stage_advances_to_merge_when_all_nodes_are_done(rules):
    config = make_config(
        at1_days=0,
        at2_days=0,
        pv1_days=0,
        pv2_days=0,
        regression_days=0,
    )
    nodes = [
        make_node(
            stage,
            work_type="测试",
            status=NodeStatus.SKIPPED,
            planned_end=None,
        )
        for stage in (
            "AT 测试第一轮",
            "AT 测试第二轮",
            "线上回归",
        )
    ]
    nodes.extend(
        [
            make_node(
                "PV 测试第一轮",
                work_type="测试",
                status=NodeStatus.COMPLETED,
                actual_end=at(24),
            ),
            make_node(
                "PV 测试第二轮",
                work_type="测试",
                status=NodeStatus.COMPLETED,
                actual_end=at(25),
            ),
            make_node(
                "版本合入",
                status=NodeStatus.COMPLETED,
                actual_end=at(26),
            ),
        ]
    )

    result = evaluate_requirement(
        make_requirement(current_stage="PV 测试第一轮"),
        nodes,
        [],
        rules,
        NOW,
        project_config=config,
    )

    assert result.current_stage == "服务端上线"


def test_cancelled_test_round_does_not_pass_gate(rules):
    requirement = make_requirement(current_stage="PV 测试第一轮")
    nodes = [
        make_node(
            "AT 测试第一轮",
            domain="服务端",
            work_type="测试",
            status=NodeStatus.CANCELLED,
            actual_end=at(23),
        ),
        make_node("AT 测试第二轮", domain="服务端", work_type="测试", status=NodeStatus.COMPLETED),
    ]

    result = evaluate_requirement(requirement, nodes, [], rules, NOW)

    assert result.level == RiskLevel.SEVERE
    assert any("AT1" in reason for reason in result.reasons)


def test_development_stage_without_nodes_uses_virtual_schedule_with_warning(rules):
    result = evaluate_requirement(
        make_requirement(current_stage="开发"), [], [], rules, NOW
    )

    assert result.level == RiskLevel.WARNING
    assert result.current_stage == "开发"
    assert any("测试排期缺少" in reason for reason in result.reasons)
    assert all(
        not item.reason_code.startswith("test_gate.")
        for item in result.findings
    )
    assert "开发" in result.process_reminders
    assert result.schedule_formula is not None


def test_translation_warning_is_checked_without_test_domains(rules):
    requirement = make_requirement(current_stage="版本合入")
    nodes = [
        make_node(
            "线上回归",
            domain="公共流程",
            work_type="发布",
            status=NodeStatus.COMPLETED,
            actual_end=at(23),
        ),
        make_node(
            "多语言翻译",
            domain="公共流程",
            work_type="产品",
            status=NodeStatus.IN_PROGRESS,
        ),
    ]

    result = evaluate_requirement(requirement, nodes, [], rules, NOW)

    assert "多语言翻译未完成，建议合板前完成" in result.reasons


def test_explicit_test_window_does_not_create_minimum_duration_finding(rules):
    node = make_node(
        "AT 测试第一轮",
        work_type="测试",
        planned_start=at(24),
        planned_end=at(25),
    )

    result = evaluate_requirement(make_requirement(), [node], [], rules, NOW)

    assert all(
        item.reason_code != "test.duration_below_minimum"
        for item in result.findings
    )


def test_explicit_planned_end_is_authoritative_without_minimum_duration_risk(rules):
    node = make_node(
        "AT 测试第二轮",
        work_type="测试",
        planned_start=in_aug(13, 0),
        planned_end=in_aug(18, 0),
        status=NodeStatus.NOT_STARTED,
    )
    config = make_config(at2_days=4)

    result = evaluate_requirement(
        make_requirement(current_stage="版本合入"),
        [node],
        [],
        rules,
        NOW,
        project_config=config,
    )

    assert result.predicted_completion == node.planned_end
    assert not any(
        item.reason_code == "test.duration_below_minimum"
        for item in result.findings
    )


def test_node_without_planned_end_uses_configured_stage_duration(rules):
    node = make_node(
        "AT 测试第一轮",
        work_type="测试",
        planned_start=NOW,
        planned_end=None,
        status=NodeStatus.NOT_STARTED,
    )
    config = make_config(at1_days=4)

    result = evaluate_requirement(
        make_requirement(current_stage="版本合入"),
        [node],
        [],
        rules,
        NOW,
        project_config=config,
    )

    assert result.predicted_completion == at(30)


def test_missing_current_and_future_key_stages_use_virtual_durations(rules):
    development = make_node(
        "各端开发",
        planned_start=NOW,
        planned_end=in_aug(5, 0),
        status=NodeStatus.IN_PROGRESS,
    )
    config = make_config(
        at1_days=1,
        at2_days=1,
        pv1_days=1,
        pv2_days=1,
        regression_days=1,
    )

    result = evaluate_requirement(
        make_requirement(current_stage="各端开发"),
        [development],
        [],
        rules,
        NOW,
        project_config=config,
    )

    assert result.predicted_completion == in_aug(12, 0)
    assert result.schedule_formula is not None
    assert [term.label for term in result.schedule_formula.terms] == [
        "AT 测试第一轮",
        "AT 测试第二轮",
        "PV 测试第一轮",
        "PV 测试第二轮",
        "线上回归",
    ]
    assert not any(
        item.reason_code.startswith("test_gate.") for item in result.findings
    )


def test_development_domain_receives_full_virtual_test_chain(rules):
    platform_development = make_node(
        "各端开发",
        domain="平台",
        planned_start=NOW,
        planned_end=in_aug(5, 0),
        status=NodeStatus.IN_PROGRESS,
    )
    configs = [
        base_config("测试角色", "客户端测试", 10),
        base_config("测试角色", "服务端测试", 20),
        base_config("测试角色", "车辆测试", 30),
    ]

    result = evaluate_requirement(
        make_requirement(current_stage="各端开发"),
        [platform_development],
        [],
        rules,
        NOW,
        base_configs=configs,
    )

    assert result.schedule_formula is not None
    labels = [term.label for term in result.schedule_formula.terms]
    assert labels == [
        "AT 测试第一轮",
        "AT 测试第二轮",
        "PV 测试第一轮",
        "PV 测试第二轮",
        "线上回归",
    ]


def test_design_only_domain_does_not_receive_at_or_pv_virtual_stages(rules):
    platform_design = make_node(
        "设计稿输出",
        domain="平台",
        work_type="设计",
        planned_start=NOW,
        planned_end=in_aug(5, 0),
        status=NodeStatus.IN_PROGRESS,
    )
    configs = [
        base_config("测试角色", "客户端测试", 10),
        base_config("测试角色", "服务端测试", 20),
        base_config("测试角色", "车辆测试", 30),
    ]

    result = evaluate_requirement(
        make_requirement(current_stage="各端开发"),
        [platform_design],
        [],
        rules,
        NOW,
        base_configs=configs,
    )

    assert result.schedule_formula is not None
    labels = [term.label for term in result.schedule_formula.terms]
    assert "AT 测试第一轮" not in labels
    assert "AT 测试第二轮" not in labels
    assert "PV 测试第一轮" not in labels
    assert "PV 测试第二轮" not in labels
    assert "线上回归" in labels


def test_completed_current_stage_advances_to_immediate_missing_stage(rules):
    nodes = [
        make_node(
            "AT 测试第一轮",
            work_type="测试",
            status=NodeStatus.COMPLETED,
            actual_end=at(23),
        ),
        make_node(
            "PV 测试第一轮",
            work_type="测试",
            status=NodeStatus.NOT_STARTED,
            planned_start=in_aug(6),
            planned_end=in_aug(10),
        ),
    ]

    result = evaluate_requirement(
        make_requirement(current_stage="AT 测试第一轮"),
        nodes,
        [],
        rules,
        NOW,
    )

    assert result.current_stage == "AT 测试第二轮"
    assert result.schedule_formula is not None
    assert result.schedule_formula.terms[0].label == "AT 测试第二轮"


def test_missing_planned_end_uses_original_planned_start_even_if_started_in_past(rules):
    node = make_node(
        "AT 测试第一轮",
        work_type="测试",
        planned_start=at(23),
        planned_end=None,
        status=NodeStatus.NOT_STARTED,
    )
    config = make_config(
        at1_days=4,
        at2_days=0,
        pv1_days=0,
        pv2_days=0,
        regression_days=0,
    )

    result = evaluate_requirement(
        make_requirement(), [node], [], rules, NOW, project_config=config
    )

    assert result.predicted_completion == at(29)


def test_empty_project_uses_virtual_schedule_from_current_stage(rules):
    config = make_config(
        at1_days=1,
        at2_days=1,
        pv1_days=1,
        pv2_days=1,
        regression_days=1,
    )

    result = evaluate_requirement(
        make_requirement(current_stage="AT 测试第二轮"),
        [],
        [],
        rules,
        NOW,
        project_config=config,
    )

    assert result.predicted_completion == at(30)
    assert result.schedule_formula is not None
    assert result.schedule_formula.domain == "项目排期"
    assert [term.label for term in result.schedule_formula.terms] == [
        "AT 测试第二轮",
        "PV 测试第一轮",
        "PV 测试第二轮",
        "线上回归",
    ]


def test_finding_for_node_delay_consuming_buffer(rules):
    node = make_node(planned_start=at(20, 9), planned_end=at(24, 18))
    requirement = make_requirement(merge_at=at(25, 18))

    result = evaluate_requirement(requirement, [node], [], rules, at(27))

    delay_finding = finding(result, "node.delay_consumes_buffer")
    assert "耗尽全部缓冲" in delay_finding.reason_text
    assert delay_finding.stage_refs == ["各端开发"]
    assert delay_finding.domain_refs == ["客户端"]
    assert delay_finding.level == RiskLevel.SEVERE
    assert delay_finding.source == "node_schedule"


def test_finding_for_missing_test_schedule(rules):
    config = make_config(at1_days=1, at2_days=1, pv1_days=0, pv2_days=0, regression_days=0)
    node = make_node(
        "AT 测试第一轮",
        work_type="测试",
        planned_start=None,
        planned_end=at(27, 18),
    )
    requirement = make_requirement(merge_at=at(28, 18))

    result = evaluate_requirement(
        requirement, [node], [], rules, NOW, project_config=config
    )

    schedule_finding = finding(result, "test.schedule_missing")
    assert "缺少计划开始时间" in schedule_finding.reason_text
    assert schedule_finding.stage_refs == ["AT 测试第一轮"]
    assert schedule_finding.domain_refs == ["客户端"]
    assert schedule_finding.level == RiskLevel.WARNING
    assert schedule_finding.source == "test_schedule"


def test_finding_for_incomplete_at_blocking_pv(rules):
    nodes = [
        make_node("AT 测试第一轮", work_type="测试"),
        make_node("AT 测试第二轮", work_type="测试"),
    ]

    result = evaluate_requirement(
        make_requirement(current_stage="PV 测试第一轮"), nodes, [], rules, NOW
    )

    at_finding = finding(result, "test_gate.at_incomplete")
    assert "不能进入 PV" in at_finding.reason_text
    assert at_finding.stage_refs == ["AT 测试第一轮"]
    assert at_finding.domain_refs == ["客户端"]
    assert at_finding.level == RiskLevel.SEVERE
    assert at_finding.source == "test_gate"


def test_finding_for_incomplete_pv_blocking_regression(rules):
    nodes = [
        make_node(
            "AT 测试第一轮",
            work_type="测试",
            status=NodeStatus.COMPLETED,
            actual_end=at(20),
        ),
        make_node(
            "AT 测试第二轮",
            work_type="测试",
            status=NodeStatus.COMPLETED,
            actual_end=at(21),
        ),
        make_node("PV 测试第一轮", work_type="测试"),
        make_node("PV 测试第二轮", work_type="测试"),
    ]

    result = evaluate_requirement(
        make_requirement(current_stage="线上回归"), nodes, [], rules, NOW
    )

    pv_finding = finding(result, "test_gate.pv_incomplete")
    assert "不能进入线上回归" in pv_finding.reason_text
    assert pv_finding.stage_refs == ["PV 测试第一轮"]
    assert pv_finding.domain_refs == ["客户端"]
    assert pv_finding.level == RiskLevel.SEVERE
    assert pv_finding.source == "test_gate"


def test_finding_for_incomplete_pv_blocking_server_launch(rules):
    nodes = [
        make_node("PV 测试第一轮", domain="服务端", work_type="测试"),
        make_node("PV 测试第二轮", domain="服务端", work_type="测试"),
    ]

    result = evaluate_requirement(
        make_requirement(current_stage="服务端上线"), nodes, [], rules, NOW
    )

    pv_finding = finding(result, "test_gate.pv_incomplete")
    assert pv_finding.reason_text == "PV 测试未通过，不能进入服务端上线"
    assert pv_finding.stage_refs == ["PV 测试第一轮"]
    assert pv_finding.domain_refs == ["服务端"]
    assert pv_finding.level == RiskLevel.SEVERE
    assert pv_finding.source == "test_gate"
    assert "服务端 PV1 测试未通过，不能进入服务端上线" in result.reasons
    assert not any("不能进入线上回归" in reason for reason in result.reasons)


def test_missing_online_regression_is_process_reminder_not_failed_gate(rules):
    node = make_node("各端开发", work_type="研发")

    result = evaluate_requirement(
        make_requirement(current_stage="版本合入"), [node], [], rules, NOW
    )

    assert not any(
        item.reason_code.startswith("test_gate.regression_")
        for item in result.findings
    )
    assert not any("线上回归未通过" in reason for reason in result.reasons)
    assert "线上回归" in result.process_reminders


def test_absent_test_rounds_are_reminders_not_failed_gates(rules):
    merge_node = make_node(
        "版本合入",
        domain="客户端",
        work_type="发布",
        status=NodeStatus.NOT_STARTED,
        planned_start=in_aug(10),
        planned_end=in_aug(12),
    )

    result = evaluate_requirement(
        make_requirement(current_stage="版本合入"), [merge_node], [], rules, NOW
    )

    assert not any(
        item.reason_code.startswith("test_gate.at_")
        or item.reason_code.startswith("test_gate.pv_")
        or item.reason_code.startswith("test_gate.regression_")
        for item in result.findings
    )
    assert result.process_reminders == [
        "AT 测试第一轮",
        "AT 测试第二轮",
        "PV 测试第一轮",
        "PV 测试第二轮",
        "线上回归",
        "多语言翻译",
    ]


def test_absent_synthetic_test_phases_do_not_create_schedule_findings(rules):
    node = make_node(
        "AT 测试第一轮",
        work_type="测试",
        status=NodeStatus.COMPLETED,
        actual_end=at(24),
    )

    result = evaluate_requirement(
        make_requirement(current_stage="PV 测试第一轮"), [node], [], rules, NOW
    )

    assert not any(
        item.reason_code == "test.schedule_missing"
        and item.stage_refs != ["AT 测试第一轮"]
        for item in result.findings
    )
    assert "AT 测试第二轮" in result.process_reminders
    assert "PV 测试第一轮" in result.process_reminders


def test_in_progress_regression_is_incomplete_not_missing(rules):
    nodes = [
        make_node(
            "AT 测试第一轮",
            work_type="测试",
            status=NodeStatus.COMPLETED,
            actual_end=at(20),
        ),
        make_node(
            "AT 测试第二轮",
            work_type="测试",
            status=NodeStatus.COMPLETED,
            actual_end=at(21),
        ),
        make_node(
            "PV 测试第一轮",
            work_type="测试",
            status=NodeStatus.COMPLETED,
            actual_end=at(22),
        ),
        make_node(
            "PV 测试第二轮",
            work_type="测试",
            status=NodeStatus.COMPLETED,
            actual_end=at(23),
        ),
        make_node(
            "线上回归",
            work_type="发布",
            status=NodeStatus.IN_PROGRESS,
            planned_start=at(24),
            planned_end=at(27),
        ),
    ]

    result = evaluate_requirement(
        make_requirement(current_stage="版本合入"), nodes, [], rules, NOW
    )

    regression_findings = [
        item
        for item in result.findings
        if item.reason_code.startswith("test_gate.regression_")
    ]
    assert [item.reason_code for item in regression_findings] == [
        "test_gate.regression_incomplete"
    ]
    assert regression_findings[0].stage_refs == ["线上回归"]
    assert regression_findings[0].domain_refs == ["客户端"]
    assert regression_findings[0].level == RiskLevel.SEVERE
    assert regression_findings[0].source == "test_gate"
    assert result.reasons.count("客户端 线上回归未通过，不能版本合入") == 1
    assert "线上回归未通过，不能版本合入" not in result.reasons


def test_shared_in_progress_regression_covers_test_domains_without_missing_findings(
    rules,
):
    nodes = []
    for domain in ("客户端", "车辆"):
        for stage, day in (
            ("AT 测试第一轮", 20),
            ("AT 测试第二轮", 21),
            ("PV 测试第一轮", 22),
            ("PV 测试第二轮", 23),
        ):
            nodes.append(
                make_node(
                    stage,
                    domain=domain,
                    work_type="测试",
                    status=NodeStatus.COMPLETED,
                    actual_end=at(day),
                )
            )
    nodes.append(
        make_node(
            "线上回归",
            domain="平台",
            work_type="发布",
            status=NodeStatus.IN_PROGRESS,
            planned_start=at(24),
            planned_end=in_aug(3),
        )
    )

    result = evaluate_requirement(
        make_requirement(current_stage="版本合入"), nodes, [], rules, NOW
    )

    regression_findings = [
        item
        for item in result.findings
        if item.reason_code.startswith("test_gate.regression_")
    ]
    assert [item.reason_code for item in regression_findings] == [
        "test_gate.regression_incomplete"
    ]
    assert regression_findings[0].stage_refs == ["线上回归"]
    assert regression_findings[0].domain_refs == ["平台"]
    assert [reason for reason in result.reasons if "线上回归" in reason] == [
        "线上回归未通过，不能版本合入"
    ]


def test_shared_in_progress_regression_is_ignored_before_regression_gate(rules):
    shared_regression = make_node(
        "线上回归",
        domain="平台",
        work_type="发布",
        status=NodeStatus.IN_PROGRESS,
        planned_start=at(24),
        planned_end=in_aug(3),
    )

    result = evaluate_requirement(
        make_requirement(current_stage="开发"),
        [shared_regression],
        [],
        rules,
        NOW,
    )

    assert not any(
        item.reason_code.startswith("test_gate.regression_")
        for item in result.findings
    )
    assert not any("线上回归未通过" in reason for reason in result.reasons)


def test_finding_for_domain_completion_later_than_merge(rules):
    node = make_node(planned_end=in_aug(20, 18))

    result = evaluate_requirement(
        make_requirement(merge_at=in_aug(14, 18)), [node], [], rules, NOW
    )

    completion_finding = finding(result, "domain.completion_after_merge")
    assert "晚于合板时间" in completion_finding.reason_text
    assert completion_finding.stage_refs == []
    assert completion_finding.domain_refs == ["客户端"]
    assert completion_finding.level == RiskLevel.SEVERE
    assert completion_finding.source == "domain_schedule"


def test_finding_for_overdue_blocker(rules):
    node = make_node()
    blocker = make_blocker(
        node_record_id=node.record_id,
        planned_resolution_at=at(23, 18),
    )

    result = evaluate_requirement(make_requirement(), [node], [blocker], rules, NOW)

    blocker_finding = finding(result, "blocker.overdue")
    assert "阻塞项已超期" in blocker_finding.reason_text
    assert blocker_finding.stage_refs == ["各端开发"]
    assert blocker_finding.domain_refs == ["客户端"]
    assert blocker_finding.level == RiskLevel.SEVERE
    assert blocker_finding.source == "blocker"


def test_finding_for_server_launch_rule_failure(rules):
    requirement = make_requirement(
        merge_at=in_aug(21, 18),
        launch_at=in_aug(19, 17),
    )
    server_node = make_node(domain="服务端")

    result = evaluate_requirement(requirement, [server_node], [], rules, NOW)

    launch_finding = finding(result, "server_launch.weekday_invalid")
    assert "不符合允许星期" in launch_finding.reason_text
    assert launch_finding.stage_refs == ["服务端上线"]
    assert launch_finding.domain_refs == ["服务端"]
    assert launch_finding.level == RiskLevel.SEVERE
    assert launch_finding.source == "server_launch"


def test_finding_for_incomplete_translation_warning(rules):
    node = make_node(
        "多语言翻译",
        domain="公共流程",
        work_type="发布",
        planned_start=at(25),
        planned_end=at(28),
        status=NodeStatus.IN_PROGRESS,
    )
    regression = make_node(
        "线上回归",
        domain="公共流程",
        work_type="发布",
        planned_start=at(20),
        planned_end=at(24),
        actual_end=at(24),
        status=NodeStatus.COMPLETED,
    )

    result = evaluate_requirement(
        make_requirement(current_stage="版本合入"), [node, regression], [], rules, NOW
    )

    translation_finding = finding(result, "translation.incomplete")
    assert "翻译未完成" in translation_finding.reason_text
    assert translation_finding.stage_refs == ["多语言翻译"]
    assert translation_finding.domain_refs == ["公共流程"]
    assert translation_finding.level == RiskLevel.WARNING
    assert translation_finding.source == "test_gate"


def test_missing_translation_is_process_reminder_not_warning(rules):
    regression = make_node(
        "线上回归",
        domain="公共流程",
        work_type="发布",
        status=NodeStatus.COMPLETED,
        actual_end=at(24),
    )
    merge_node = make_node(
        "版本合入",
        domain="公共流程",
        work_type="发布",
        status=NodeStatus.IN_PROGRESS,
        planned_start=at(24),
        planned_end=in_aug(3),
    )

    result = evaluate_requirement(
        make_requirement(current_stage="版本合入"),
        [regression, merge_node],
        [],
        rules,
        NOW,
    )

    assert not any(
        item.reason_code == "translation.incomplete" for item in result.findings
    )
    assert "多语言翻译未完成，建议合板前完成" not in result.reasons
    assert "多语言翻译" in result.process_reminders
