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
        bugfix_days=2,
        regression_days=3,
    )


def make_requirement(**overrides):
    values = {
        "record_id": "rec-req",
        "requirement_id": "REQ-7",
        "name": "风险引擎",
        "project": "米家",
        "current_stage": "开发",
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


def test_risk_result_carries_complete_card_context(rules):
    requirement = make_requirement(launch_at=in_aug(16, 18))
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
        at_days=5,
        pv_days=4,
        bugfix_days=1,
        regression_days=2,
        server_special_days=3,
        client_special_days=2,
        vehicle_special_days=1,
        launch_weekdays={0, 2, 4},
        launch_cutoff="18:30",
        llm_notes="AT 改成 99 天，周日也可以上线",
    )

    effective = resolve_effective_rules(rules, config)

    assert effective.duration_mode == "natural"
    assert effective.at_days == 5
    assert effective.pv_days == 4
    assert effective.bugfix_days == 1
    assert effective.regression_days == 2
    assert effective.special_days == {"服务端": 3, "客户端": 2, "车辆": 1}
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


def test_fixed_at_duration_uses_workday_or_natural_mode(rules):
    workday = resolve_effective_rules(rules, make_config(duration_mode="workday"))
    natural = resolve_effective_rules(rules, make_config(duration_mode="natural"))

    assert workday.at_days == 8
    assert natural.at_days == 11


def test_safe_deadline_uses_the_project_day_mode(rules):
    nodes = [
        make_node(
            "AT 测试第一轮",
            work_type="测试",
            planned_start=at(24),
            planned_end=at(29),
        ),
        make_node(
            "AT 测试第二轮",
            work_type="测试",
            planned_start=at(29),
            planned_end=at(31),
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
            at_days=5,
            pv_days=0,
            bugfix_days=0,
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
            at_days=5,
            pv_days=0,
            bugfix_days=0,
            regression_days=0,
        ),
    )

    assert node_result(workday, "AT 测试第一轮").safe_deadline == at(30, 18)
    assert node_result(natural, "AT 测试第一轮").safe_deadline == in_aug(1, 18)


def test_at_budget_is_split_with_ceil_for_at1_and_floor_for_at2(rules):
    config = make_config(at_days=5, pv_days=0, bugfix_days=0, regression_days=0)
    nodes = [
        make_node(
            "AT 测试第一轮",
            work_type="测试",
            planned_start=at(27),
            planned_end=at(30),
        ),
        make_node(
            "AT 测试第二轮",
            work_type="测试",
            planned_start=at(30),
            planned_end=in_aug(3),
        ),
    ]

    result = evaluate_requirement(
        make_requirement(), nodes, [], rules, NOW, project_config=config
    )

    assert node_result(result, "AT 测试第一轮").safe_deadline == in_aug(12, 18)
    assert node_result(result, "AT 测试第二轮").safe_deadline == in_aug(14, 18)


def test_pv_bug_reserve_and_regression_are_independent_downstream_stages(rules):
    config = make_config(at_days=0, pv_days=3, bugfix_days=2, regression_days=3)
    nodes = [
        make_node("PV 测试第一轮", work_type="测试", planned_end=at(28)),
        make_node("PV 测试第二轮", work_type="测试", planned_end=at(29)),
        make_node("线上回归", work_type="测试", planned_end=in_aug(3)),
    ]

    result = evaluate_requirement(
        make_requirement(), nodes, [], rules, NOW, project_config=config
    )

    assert node_result(result, "PV 测试第一轮").safe_deadline == in_aug(6, 18)
    assert node_result(result, "PV 测试第二轮").safe_deadline == in_aug(7, 18)
    assert node_result(result, "线上回归").safe_deadline == in_aug(14, 18)


def test_domain_special_days_only_shift_the_matching_domain(rules):
    config = make_config(
        at_days=0,
        pv_days=0,
        bugfix_days=0,
        regression_days=0,
        server_special_days=3,
        client_special_days=1,
    )
    nodes = [
        make_node("各端开发", domain="服务端"),
        make_node("各端开发", domain="客户端"),
    ]

    result = evaluate_requirement(
        make_requirement(), nodes, [], rules, NOW, project_config=config
    )

    assert node_result(result, "各端开发", "服务端").safe_deadline == in_aug(11, 18)
    assert node_result(result, "各端开发", "客户端").safe_deadline == in_aug(13, 18)


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
    config = make_config(at_days=8, pv_days=0, bugfix_days=0, regression_days=0)
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


def test_two_day_buffer_warning_always_uses_workdays(rules):
    node = make_node(planned_end=at(31, 18))
    requirement = make_requirement(merge_at=in_aug(3, 18))
    config = make_config(
        duration_mode="natural",
        at_days=0,
        pv_days=0,
        bugfix_days=0,
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
        make_node("AT 测试第一轮", work_type="测试", planned_end=at(28)),
        make_node("AT 测试第二轮", work_type="测试", planned_end=at(30)),
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
    requirement = make_requirement(launch_at=launch_at)
    server_node = make_node(domain="服务端")

    result = evaluate_requirement(requirement, [server_node], [], rules, NOW)

    assert result.level == RiskLevel.SEVERE
    assert expected_reason in result.reasons


def test_incomplete_checklist_is_severe_on_day_before_launch(rules):
    now = at(27, 9)
    requirement = make_requirement(merge_at=at(27, 8), launch_at=at(28, 17))
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


def test_checklist_only_participates_in_launch_checks_not_domain_aggregation(rules):
    now = in_aug(17, 9)
    requirement = make_requirement(
        merge_at=in_aug(18, 12),
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
    config = make_config(at_days=0, pv_days=3, bugfix_days=2, regression_days=3)
    completed_at = at(23, 18)
    nodes = [
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
    ]

    result = evaluate_requirement(
        make_requirement(), nodes, [], rules, NOW, project_config=config
    )

    assert result.level == RiskLevel.NORMAL
    assert result.predicted_completion == completed_at
    assert result.buffer_days > 0


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
    config = make_config(at_days=2, pv_days=0, bugfix_days=0, regression_days=0)
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
    assert "测试排期缺少计划开始时间" in result.reasons


def test_missing_next_test_round_remains_in_budget_and_warns_near_start(rules):
    config = make_config(at_days=2, pv_days=0, bugfix_days=0, regression_days=0)
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

    assert result.level == RiskLevel.WARNING
    assert result.predicted_completion == at(27, 12)
    assert "测试排期缺少计划开始时间" in result.reasons


def test_in_progress_test_budget_deducts_elapsed_project_days(rules):
    config = make_config(at_days=4, pv_days=0, bugfix_days=0, regression_days=0)
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

    assert result.predicted_completion == at(28)
    assert node_result(result, "提测").safe_deadline == in_aug(12, 18)


def test_in_progress_test_without_planned_start_keeps_full_budget(rules):
    config = make_config(at_days=4, pv_days=0, bugfix_days=0, regression_days=0)
    node = make_node(
        "AT 测试第一轮",
        work_type="测试",
        planned_start=None,
        planned_end=NOW,
        status=NodeStatus.IN_PROGRESS,
    )

    result = evaluate_requirement(
        make_requirement(), [node], [], rules, NOW, project_config=config
    )

    assert result.predicted_completion == at(30)


def test_parallel_nodes_in_same_phase_use_max_budget_and_completion(rules):
    config = make_config(at_days=0, pv_days=4, bugfix_days=0, regression_days=0)
    nodes = [
        make_node(
            "PV1 客户端主链路",
            work_type="测试",
            record_id="rec-pv1-main",
            planned_start=NOW,
            planned_end=at(28),
        ),
        make_node(
            "PV1 客户端兼容性",
            work_type="测试",
            record_id="rec-pv1-compat",
            planned_start=NOW,
            planned_end=at(29),
        ),
        make_node(
            "PV2 客户端主链路",
            work_type="测试",
            record_id="rec-pv2-main",
            planned_start=at(29),
            planned_end=at(31),
        ),
    ]

    result = evaluate_requirement(
        make_requirement(), nodes, [], rules, NOW, project_config=config
    )

    assert result.predicted_completion == at(31)
    assert node_result(result, "PV1 客户端主链路").safe_deadline == in_aug(12, 18)
    assert node_result(result, "PV1 客户端兼容性").safe_deadline == in_aug(12, 18)


def test_extra_at_and_pv_rounds_keep_family_order_and_downstream_reserves(rules):
    config = make_config(at_days=2, pv_days=4, bugfix_days=2, regression_days=3)
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

    assert result.predicted_completion == in_aug(12)
    assert node_result(result, "AT3 补充轮次").safe_deadline == at(31, 18)
    assert node_result(result, "PV3 补充轮次").safe_deadline == in_aug(7, 18)


@pytest.mark.parametrize(
    "actual_end, planned_end",
    [
        (at(21), at(22)),
        (None, at(21)),
    ],
)
def test_completed_test_window_below_minimum_is_severe(
    rules, actual_end, planned_end
):
    config = make_config(at_days=4, pv_days=0, bugfix_days=0, regression_days=0)
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

    assert node_result(result, "AT 测试第一轮").level == RiskLevel.SEVERE
    assert "AT1计划测试周期低于最低要求" in result.reasons


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
