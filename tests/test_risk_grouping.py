import pytest

from requirement_monitor.models import RiskFinding, RiskLevel
from requirement_monitor.risk_grouping import group_risk_families, group_risk_findings


def finding(
    reason_code,
    reason_text,
    *,
    stage_refs=(),
    domain_refs=(),
    level=RiskLevel.WARNING,
):
    return RiskFinding(
        reason_code=reason_code,
        reason_text=reason_text,
        stage_refs=list(stage_refs),
        domain_refs=list(domain_refs),
        level=level,
        source="test",
    )


def test_groups_same_reason_across_stages_and_domains():
    groups = group_risk_findings(
        [
            finding(
                "test_gate.at_incomplete",
                "AT 测试未通过，不能进入 PV",
                stage_refs=["AT 测试第二轮"],
                domain_refs=["车辆"],
            ),
            finding(
                "test_gate.at_incomplete",
                "AT 测试未通过，不能进入 PV",
                stage_refs=["AT 测试第一轮"],
                domain_refs=["客户端"],
            ),
        ],
        stage_order={"AT 测试第一轮": 1, "AT 测试第二轮": 2},
    )

    assert len(groups) == 1
    assert groups[0].reason_code == "test_gate.at_incomplete"
    assert groups[0].reason_text == "AT 测试未通过，不能进入 PV"
    assert groups[0].stage_refs == ["AT 测试第一轮", "AT 测试第二轮"]
    assert groups[0].domain_refs == ["车辆", "客户端"]


def test_schedule_group_preserves_domain_stage_source_findings():
    platform_finding = finding(
        "schedule.buffer_low",
        "剩余缓冲不足",
        stage_refs=["AT 测试第一轮"],
        domain_refs=["平台"],
    )
    client_finding = finding(
        "schedule.buffer_low",
        "剩余缓冲不足",
        stage_refs=["PV 测试第一轮"],
        domain_refs=["客户端"],
    )

    groups = group_risk_findings(
        [platform_finding, client_finding],
        stage_order={"AT 测试第一轮": 1, "PV 测试第一轮": 3},
    )

    assert len(groups) == 1
    assert groups[0].source_findings == [platform_finding, client_finding]


def test_different_reason_codes_remain_separate():
    groups = group_risk_findings(
        [
            finding("test_gate.at_incomplete", "AT 未通过"),
            finding("schedule.overdue", "排期已逾期"),
        ],
        stage_order={},
    )

    assert [group.reason_code for group in groups] == [
        "test_gate.at_incomplete",
        "schedule.overdue",
    ]


def test_empty_or_unknown_code_uses_exact_legacy_text_key():
    empty_code = RiskFinding.model_construct(
        reason_code="",
        reason_text="历史风险：请人工确认",
        stage_refs=[],
        domain_refs=[],
        level=RiskLevel.WARNING,
        source="legacy",
    )
    missing_code = RiskFinding.model_construct(
        reason_code=None,
        reason_text="另一个历史风险",
        stage_refs=[],
        domain_refs=[],
        level=RiskLevel.NORMAL,
        source="legacy",
    )

    groups = group_risk_findings([empty_code, missing_code], stage_order={})

    assert [(group.reason_code, group.reason_text) for group in groups] == [
        ("legacy:历史风险：请人工确认", "历史风险：请人工确认"),
        ("legacy:另一个历史风险", "另一个历史风险"),
    ]


def test_legacy_fallback_does_not_collide_with_public_reason_code():
    legacy_finding = RiskFinding.model_construct(
        reason_code="",
        reason_text="collision",
        stage_refs=[],
        domain_refs=[],
        level=RiskLevel.WARNING,
        source="legacy",
    )
    structured_finding = finding("legacy:collision", "结构化风险")

    groups = group_risk_findings(
        [legacy_finding, structured_finding],
        stage_order={},
    )

    assert len(groups) == 2
    assert [(group.reason_code, group.reason_text) for group in groups] == [
        ("legacy:collision", "collision"),
        ("legacy:collision", "结构化风险"),
    ]


def test_same_reason_promotes_to_highest_level():
    groups = group_risk_findings(
        [
            finding("blocker.open", "存在未解决阻塞项", level=RiskLevel.NORMAL),
            finding("blocker.open", "存在未解决阻塞项", level=RiskLevel.SEVERE),
            finding("blocker.open", "存在未解决阻塞项", level=RiskLevel.WARNING),
        ],
        stage_order={},
    )

    assert groups[0].level == RiskLevel.SEVERE


def test_groups_sort_by_level_then_earliest_configured_stage():
    groups = group_risk_findings(
        [
            finding(
                "normal.late",
                "普通风险",
                stage_refs=["后置阶段"],
                level=RiskLevel.NORMAL,
            ),
            finding(
                "warning.early",
                "预警风险",
                stage_refs=["早期阶段"],
                level=RiskLevel.WARNING,
            ),
            finding(
                "severe.late",
                "严重风险二",
                stage_refs=["后置阶段"],
                level=RiskLevel.SEVERE,
            ),
            finding(
                "severe.early",
                "严重风险一",
                stage_refs=["早期阶段"],
                level=RiskLevel.SEVERE,
            ),
        ],
        stage_order={"早期阶段": 1, "后置阶段": 2},
    )

    assert [group.reason_code for group in groups] == [
        "severe.early",
        "severe.late",
        "warning.early",
        "normal.late",
    ]


def test_stage_refs_follow_configured_order_and_unseen_stages_follow():
    groups = group_risk_findings(
        [
            finding(
                "schedule.risk",
                "排期风险",
                stage_refs=["未配置阶段", "阶段二", "阶段一", "阶段二"],
            )
        ],
        stage_order={"阶段一": 1, "阶段二": 2},
    )

    assert groups[0].stage_refs == ["阶段一", "阶段二", "未配置阶段"]


def test_references_are_stably_deduplicated_across_findings():
    groups = group_risk_findings(
        [
            finding(
                "schedule.risk",
                "排期风险",
                stage_refs=["阶段一", "阶段二"],
                domain_refs=["客户端", "车辆"],
            ),
            finding(
                "schedule.risk",
                "排期风险",
                stage_refs=["阶段二", "阶段三", "阶段一"],
                domain_refs=["车辆", "平台", "客户端"],
            ),
        ],
        stage_order={"阶段一": 1, "阶段二": 2, "阶段三": 3},
    )

    assert groups[0].stage_refs == ["阶段一", "阶段二", "阶段三"]
    assert groups[0].domain_refs == ["客户端", "车辆", "平台"]


def test_empty_findings_return_no_groups():
    assert group_risk_findings([], stage_order={}) == []


def test_grouping_does_not_mutate_findings_or_reference_lists():
    findings = [
        finding(
            "schedule.risk",
            "排期风险",
            stage_refs=["阶段二", "阶段一"],
            domain_refs=["车辆", "客户端"],
        ),
        finding(
            "other.risk",
            "其他风险",
            stage_refs=["未配置阶段"],
            domain_refs=["平台"],
        ),
    ]
    findings_before = [item.model_dump() for item in findings]
    stage_refs_before = [list(item.stage_refs) for item in findings]
    domain_refs_before = [list(item.domain_refs) for item in findings]

    group_risk_findings(
        findings,
        stage_order={"阶段一": 1, "阶段二": 2},
    )

    assert [item.model_dump() for item in findings] == findings_before
    assert [item.stage_refs for item in findings] == stage_refs_before
    assert [item.domain_refs for item in findings] == domain_refs_before


def test_output_is_deterministic_with_original_order_ties():
    findings = [
        finding(
            "reason.b",
            "B 风险",
            stage_refs=["同一阶段"],
            level=RiskLevel.WARNING,
        ),
        finding(
            "reason.a",
            "A 风险",
            stage_refs=["同一阶段"],
            level=RiskLevel.WARNING,
        ),
    ]

    first = group_risk_findings(findings, stage_order={"同一阶段": 1})
    second = group_risk_findings(findings, stage_order={"同一阶段": 1})

    assert [group.model_dump() for group in first] == [group.model_dump() for group in second]
    assert [group.reason_code for group in first] == ["reason.b", "reason.a"]


@pytest.mark.parametrize(
    ("reason_code", "expected_title"),
    [
        ("schedule.buffer_negative", "合板窗口不足"),
        ("node.delay_consumes_buffer", "节点延期与缓冲耗尽"),
        ("test.duration_below_minimum", "测试周期不足"),
        ("schedule.minimum_window_insufficient", "合板窗口不足"),
        ("domain.completion_after_merge", "合板窗口不足"),
        ("server_launch.weekday_invalid", "合板窗口不足"),
        ("server_launch.cutoff_exceeded", "合板窗口不足"),
        ("stage.current_missing", "排期缺失"),
        ("test.schedule_missing", "排期缺失"),
        ("test_gate.regression_missing", "排期缺失"),
        ("test_gate.at_incomplete", "门禁未通过"),
        ("test_gate.pv_incomplete", "门禁未通过"),
        ("test_gate.regression_incomplete", "门禁未通过"),
        ("server_launch.checklist_incomplete", "门禁未通过"),
        ("node.stale_update", "进展停滞"),
        ("blocker.overdue", "阻塞风险"),
        ("blocker.due_soon", "阻塞风险"),
        ("schedule.buffer_low", "剩余缓冲不足"),
        ("node.delay_with_buffer", "节点延期但仍有缓冲"),
        ("translation.incomplete", "多语言翻译未完成"),
    ],
)
def test_known_reason_codes_map_to_approved_risk_families(
    reason_code, expected_title
):
    families = group_risk_families(
        [finding(reason_code, "原始风险原因")],
        stage_order={},
    )

    assert [family.title for family in families] == [expected_title]


def test_risk_family_merges_scope_and_preserves_source_findings():
    first = finding(
        "node.delay_consumes_buffer",
        "节点延期已经耗尽全部缓冲",
        stage_refs=["AT 测试第二轮"],
        domain_refs=["车辆"],
        level=RiskLevel.SEVERE,
    )
    second = finding(
        "node.delay_consumes_buffer",
        "另一个节点延期已经耗尽全部缓冲",
        stage_refs=["AT 测试第一轮", "AT 测试第二轮"],
        domain_refs=["客户端", "车辆"],
        level=RiskLevel.SEVERE,
    )

    families = group_risk_families(
        [first, second],
        stage_order={"AT 测试第一轮": 1, "AT 测试第二轮": 2},
    )

    assert len(families) == 1
    assert families[0].code == "node_delay_buffer_exhaustion"
    assert families[0].title == "节点延期与缓冲耗尽"
    assert families[0].stage_refs == ["AT 测试第一轮", "AT 测试第二轮"]
    assert families[0].domain_refs == ["车辆", "客户端"]
    assert families[0].source_findings == [first, second]


def test_negative_buffer_merges_with_other_merge_window_findings():
    findings = [
        finding(
            "schedule.minimum_window_insufficient",
            "车辆 PV 最低测试周期无法容纳合板窗口",
            domain_refs=["车辆"],
            level=RiskLevel.SEVERE,
        ),
        finding(
            "schedule.buffer_negative",
            "剩余缓冲为负",
            domain_refs=["客户端", "车辆"],
            level=RiskLevel.SEVERE,
        ),
        finding(
            "domain.completion_after_merge",
            "客户端交付域预计完成时间晚于合板时间",
            domain_refs=["客户端"],
            level=RiskLevel.SEVERE,
        ),
    ]

    families = group_risk_families(findings, stage_order={})

    assert len(families) == 1
    assert families[0].title == "合板窗口不足"
    assert families[0].domain_refs == ["车辆", "客户端"]
    assert families[0].source_findings == findings


def test_legacy_round_duration_text_merges_with_structured_duration_finding():
    findings = [
        finding(
            "test.duration_below_minimum",
            "测试周期低于最低要求",
            stage_refs=["PV 测试第二轮"],
            domain_refs=["车辆"],
            level=RiskLevel.SEVERE,
        ),
        finding(
            "legacy:PV2计划测试周期低于最低要求",
            "PV2计划测试周期低于最低要求",
            stage_refs=["PV 测试第二轮"],
            domain_refs=["车辆"],
            level=RiskLevel.SEVERE,
        ),
    ]

    families = group_risk_families(findings, stage_order={})

    assert len(families) == 1
    assert families[0].title == "测试周期不足"
    assert families[0].source_findings == findings


def test_risk_families_sort_severe_before_warning():
    families = group_risk_families(
        [
            finding(
                "node.stale_update",
                "连续2个工作日没有进展更新",
                stage_refs=["开发"],
                level=RiskLevel.WARNING,
            ),
            finding(
                "test.duration_below_minimum",
                "测试周期低于最低要求",
                stage_refs=["PV 测试第一轮"],
                level=RiskLevel.SEVERE,
            ),
        ],
        stage_order={"开发": 1, "PV 测试第一轮": 2},
    )

    assert [family.title for family in families] == ["测试周期不足", "进展停滞"]


def test_warning_families_remain_distinct_for_compact_warning_section():
    findings = [
        finding(
            "schedule.buffer_low",
            "剩余缓冲不超过2天",
            domain_refs=["客户端"],
        ),
        finding(
            "translation.incomplete",
            "多语言翻译未完成，建议合板前完成",
            stage_refs=["多语言翻译"],
            domain_refs=["平台"],
        ),
    ]

    families = group_risk_families(findings, stage_order={"多语言翻译": 10})

    assert [family.code for family in families] == [
        "translation_incomplete",
        "buffer_low",
    ]
    assert [family.title for family in families] == [
        "多语言翻译未完成",
        "剩余缓冲不足",
    ]
    assert [family.source_findings for family in families] == [
        [findings[1]],
        [findings[0]],
    ]


def test_unknown_reason_codes_remain_separate_with_original_text():
    first = finding("custom.unknown", "未知风险原文 A", stage_refs=["阶段一"])
    second = finding("custom.unknown", "未知风险原文 B", stage_refs=["阶段二"])

    families = group_risk_families(
        [first, second],
        stage_order={"阶段一": 1, "阶段二": 2},
    )

    assert [(family.code, family.title) for family in families] == [
        ("custom.unknown", "未知风险原文 A"),
        ("custom.unknown", "未知风险原文 B"),
    ]
    assert [family.source_findings for family in families] == [[first], [second]]
