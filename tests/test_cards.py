import json
import re
from datetime import datetime
from types import SimpleNamespace
from typing import get_type_hints
from zoneinfo import ZoneInfo

import pytest

import requirement_monitor.cards as cards
import requirement_monitor.risk_grouping as risk_grouping
from requirement_monitor.cards import (
    build_daily_card,
    build_daily_cards,
    build_data_error_card,
    build_plain_text_fallback,
    build_severe_card,
    build_severe_cards,
    escape_value,
    interactive_card,
    mention,
)
from requirement_monitor.models import (
    Blocker,
    LLMEnrichment,
    NodeRisk,
    NodeStatus,
    Person,
    RequirementRisk,
    RiskFinding,
    RiskGroup,
    RiskLevel,
    RunReport,
    ScheduleFormula,
    ScheduleFormulaTerm,
    ValidationIssue,
)


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 24, 20, 0, tzinfo=TZ)


def at(day, hour=18):
    return datetime(2026, 7, day, hour, 0, tzinfo=TZ)


def in_aug(day, hour=18):
    return datetime(2026, 8, day, hour, 0, tzinfo=TZ)


def make_node_risk(
    record_id,
    name,
    planned_end,
    *,
    requirement_id="REQ-1",
    owner_id="ou-zhang",
    owner_name="张三",
    domain="服务端",
    status=NodeStatus.IN_PROGRESS,
    level=RiskLevel.NORMAL,
    safe_deadline=None,
):
    return NodeRisk(
        node_record_id=record_id,
        requirement_id=requirement_id,
        node_name=name,
        domain=domain,
        owner_id=owner_id,
        owner_name=owner_name,
        planned_end=planned_end,
        status=status,
        level=level,
        predicted_completion=planned_end,
        safe_deadline=safe_deadline or planned_end,
        buffer_days=1,
    )


def make_blocker(
    *,
    requirement_id="REQ-1",
    title="等待 API & 权限",
    owner_id="ou-blocker",
    owner_name="王五",
    planned_resolution_at=None,
):
    return Blocker(
        record_id="rec-blocker-{}".format(requirement_id),
        requirement_id=requirement_id,
        title=title,
        owner_id=owner_id,
        owner_name=owner_name,
        found_at=at(23, 9),
        planned_resolution_at=planned_resolution_at or at(26),
        status="处理中",
        affects_merge=True,
    )


def make_risk(
    requirement_id="REQ-1",
    name="账号<迁移>",
    *,
    project="米家",
    version="8.0",
    level=RiskLevel.WARNING,
    owner_id="ou-project",
    owner_name="项目负责人",
    nodes=None,
    blockers=None,
    buffer_days=3,
    predicted_completion=None,
    affected_domains=None,
    reasons=None,
    actions=None,
    llm_enrichment=None,
    current_stage="未提供",
    findings=None,
    schedule_formula=None,
    process_reminders=None,
    stage_order=None,
):
    return RequirementRisk(
        requirement_record_id="rec-{}".format(requirement_id),
        requirement_id=requirement_id,
        requirement_name=name,
        project=project,
        target_version=version,
        current_stage=current_stage,
        merge_at=in_aug(3),
        launch_at=in_aug(2),
        project_owner_id=owner_id,
        project_owner_name=owner_name,
        level=level,
        predicted_completion=predicted_completion or in_aug(2),
        buffer_days=buffer_days,
        affected_domains=(
            affected_domains if affected_domains is not None else ["服务端"]
        ),
        reasons=reasons if reasons is not None else ["合板缓冲不足"],
        actions=actions if actions is not None else ["今天完成联调"],
        node_risks=nodes or [],
        blockers=blockers or [],
        llm_enrichment=llm_enrichment,
        findings=findings or [],
        schedule_formula=schedule_formula,
        process_reminders=process_reminders or [],
        stage_order=stage_order or {},
    )


def make_report(risks, *, llm_attempted=False, llm_degraded=False):
    return RunReport(
        trigger="manual",
        started_at=NOW,
        total_requirements=len(risks),
        eligible_requirement_count=len(risks),
        processed_requirements=len(risks),
        normal_requirements=sum(item.level == RiskLevel.NORMAL for item in risks),
        warning_requirements=sum(item.level == RiskLevel.WARNING for item in risks),
        severe_requirements=sum(item.level == RiskLevel.SEVERE for item in risks),
        llm_attempted=llm_attempted,
        llm_degraded=llm_degraded,
        requirement_risks=risks,
    )


def make_risk_family(
    code,
    title,
    *,
    level=RiskLevel.SEVERE,
    stage_refs=None,
    domain_refs=None,
    source_findings=None,
):
    return SimpleNamespace(
        code=code,
        title=title,
        level=level,
        stage_refs=stage_refs or [],
        domain_refs=domain_refs or [],
        source_findings=source_findings or [],
    )


@pytest.mark.parametrize(
    "reason_code",
    [
        "schedule.buffer_low",
        "schedule.buffer_negative",
        "schedule.minimum_window_insufficient",
        "domain.completion_after_merge",
    ],
)
def test_risk_group_element_labels_schedule_stage_refs_as_unconfirmed_schedule(
    reason_code,
):
    finding = RiskFinding(
        reason_code=reason_code,
        reason_text="排期风险",
        stage_refs=["PV 测试第二轮", "线上回归"],
        domain_refs=["客户端"],
        level=RiskLevel.SEVERE,
        source="domain_schedule",
    )
    group = RiskGroup(
        reason_code=reason_code,
        reason_text="排期风险",
        stage_refs=["PV 测试第二轮", "线上回归"],
        domain_refs=["客户端"],
        level=RiskLevel.SEVERE,
        source_findings=[finding],
    )

    content = cards._risk_group_element(1, group)["text"]["content"]

    assert "未确定排期" in content
    assert "客户端：PV 测试第二轮、线上回归" in content
    assert "交付域：客户端" not in content
    assert "环节：PV 测试第二轮、线上回归" not in content


def test_risk_group_element_keeps_stage_label_for_other_risks():
    group = RiskGroup(
        reason_code="test_gate.at_incomplete",
        reason_text="AT 测试未通过",
        stage_refs=["AT 测试第二轮"],
        domain_refs=["车辆"],
        level=RiskLevel.SEVERE,
    )

    content = cards._risk_group_element(1, group)["text"]["content"]

    assert "环节：AT 测试第二轮" in content
    assert "未确定排期" not in content


def test_severe_card_labels_unconfirmed_schedule_stages():
    risk = make_risk(
        level=RiskLevel.SEVERE,
        findings=[
            RiskFinding(
                reason_code="schedule.buffer_negative",
                reason_text="剩余缓冲为负",
                stage_refs=["PV 测试第二轮", "线上回归"],
                domain_refs=["客户端"],
                level=RiskLevel.SEVERE,
                source="domain_schedule",
            )
        ],
    )

    text = card_text(build_severe_card(risk))

    assert "未确定排期" in text
    assert "客户端：PV 测试第二轮、线上回归" in text


def test_severe_card_keeps_stage_and_domain_labels_for_other_risks():
    risk = make_risk(
        level=RiskLevel.SEVERE,
        findings=[
            RiskFinding(
                reason_code="test_gate.at_incomplete",
                reason_text="AT 测试未通过",
                stage_refs=["AT 测试第二轮"],
                domain_refs=["车辆"],
                level=RiskLevel.SEVERE,
                source="test_gate",
            )
        ],
    )

    text = card_text(build_severe_card(risk))

    assert "环节：AT 测试第二轮" in text
    assert "交付域：车辆" in text
    assert "未确定排期" not in text


def test_severe_card_keeps_non_schedule_stages_in_mixed_risk_family(monkeypatch):
    schedule_finding = RiskFinding(
        reason_code="schedule.buffer_negative",
        reason_text="剩余缓冲为负",
        stage_refs=["PV 测试第二轮"],
        domain_refs=["客户端"],
        level=RiskLevel.SEVERE,
        source="domain_schedule",
    )
    launch_finding = RiskFinding(
        reason_code="server_launch.weekday_invalid",
        reason_text="服务端上线日期不符合规则",
        stage_refs=["服务端上线"],
        domain_refs=["服务端"],
        level=RiskLevel.SEVERE,
        source="server_launch",
    )
    monkeypatch.setattr(
        risk_grouping,
        "group_risk_families",
        lambda findings, stage_order: [
            make_risk_family(
                "merge_window_insufficient",
                "合板窗口不足",
                stage_refs=["PV 测试第二轮", "服务端上线"],
                domain_refs=["客户端", "服务端"],
                source_findings=[schedule_finding, launch_finding],
            )
        ],
    )
    risk = make_risk(
        level=RiskLevel.SEVERE,
        findings=[schedule_finding, launch_finding],
    )

    text = card_text(build_severe_card(risk))

    assert "未确定排期" in text
    assert "客户端：PV 测试第二轮" in text
    assert "环节：服务端上线" in text
    assert "交付域：服务端" in text
    assert "交付域：客户端、服务端" not in text


def test_daily_card_orders_missing_schedule_stages_by_requirement_stage_order():
    risk = make_risk(
        stage_order={"PV 测试第一轮": 0, "AT 测试第二轮": 1},
        findings=[
            RiskFinding(
                reason_code="schedule.buffer_low",
                reason_text="剩余缓冲不超过2天",
                stage_refs=["AT 测试第二轮", "PV 测试第一轮"],
                domain_refs=["客户端"],
                level=RiskLevel.WARNING,
                source="domain_schedule",
            )
        ],
    )

    text = card_text(build_daily_card(make_report([risk])))

    assert "客户端：PV 测试第一轮、AT 测试第二轮" in text


def test_daily_card_groups_missing_schedule_stages_by_domain():
    risk = make_risk(
        stage_order={
            "AT 测试第一轮": 0,
            "AT 测试第二轮": 1,
            "PV 测试第一轮": 2,
            "PV 测试第二轮": 3,
            "线上回归": 4,
        },
        findings=[
            RiskFinding(
                reason_code="schedule.buffer_low",
                reason_text="剩余缓冲不超过2天",
                stage_refs=[
                    "AT 测试第一轮",
                    "AT 测试第二轮",
                    "PV 测试第一轮",
                    "PV 测试第二轮",
                    "线上回归",
                ],
                domain_refs=["平台"],
                level=RiskLevel.WARNING,
                source="domain_schedule",
            ),
            RiskFinding(
                reason_code="schedule.buffer_low",
                reason_text="剩余缓冲不超过2天",
                stage_refs=[
                    "PV 测试第一轮",
                    "PV 测试第二轮",
                    "线上回归",
                ],
                domain_refs=["客户端"],
                level=RiskLevel.WARNING,
                source="domain_schedule",
            ),
        ],
    )

    text = card_text(build_daily_card(make_report([risk])))

    assert "平台：AT 测试第一轮、AT 测试第二轮、PV 测试第一轮、PV 测试第二轮、线上回归" in text
    assert "客户端：PV 测试第一轮、PV 测试第二轮、线上回归" in text
    assert "交付域：平台、客户端" not in text


def test_schedule_risk_without_missing_stages_does_not_claim_unconfirmed_schedule():
    risk = make_risk(
        level=RiskLevel.SEVERE,
        findings=[
            RiskFinding(
                reason_code="schedule.buffer_negative",
                reason_text="剩余缓冲为负",
                stage_refs=[],
                domain_refs=["客户端"],
                level=RiskLevel.SEVERE,
                source="domain_schedule",
            )
        ],
    )

    daily_text = card_text(build_daily_card(make_report([risk])))
    severe_text = card_text(build_severe_card(risk))

    assert "未确定排期" not in daily_text
    assert "未确定排期" not in severe_text
    assert "交付域：客户端" in daily_text
    assert "交付域：客户端" in severe_text


def test_severe_card_falls_back_to_unconfirmed_label_without_source_findings(
    monkeypatch,
):
    monkeypatch.setattr(
        risk_grouping,
        "group_risk_families",
        lambda findings, stage_order: [
            make_risk_family(
                "schedule.buffer_negative",
                "剩余缓冲为负",
                stage_refs=["PV 测试第二轮"],
                domain_refs=["客户端"],
                source_findings=[],
            )
        ],
    )
    risk = make_risk(level=RiskLevel.SEVERE)

    text = card_text(build_severe_card(risk))

    assert "未确定排期：PV 测试第二轮" in text
    assert "交付域：客户端" in text
    assert "环节：PV 测试第二轮" not in text


def test_split_grouped_units_bounds_non_atomic_markdown_elements():
    oversized = cards._markdown_element("超长内容" * 2000)

    payloads = cards._split_grouped_units(
        "非原子 Markdown",
        "yellow",
        [
            (cards._markdown_element("正常原子内容"), True),
            (oversized, False),
        ],
    )

    assert payloads
    for payload in payloads:
        for element in payload["card"]["elements"]:
            assert len(cards._element_text(element).encode("utf-8")) <= 1800


def make_owner_risks(count):
    risks = []
    for index in range(count):
        requirement_id = "REQ-{:02d}".format(index)
        owner_id = "ou-owner-{:02d}".format(index)
        owner_name = "负责人{:02d}".format(index)
        risks.append(
            make_risk(
                requirement_id=requirement_id,
                name="需求{:02d}".format(index),
                project="项目{}".format(index // 10),
                nodes=[
                    make_node_risk(
                        "node-{:02d}".format(index),
                        "节点{:02d}".format(index),
                        at(25 + index % 5),
                        requirement_id=requirement_id,
                        owner_id=owner_id,
                        owner_name=owner_name,
                    )
                ],
            )
        )
    return risks


def card_text(payload):
    values = []

    def collect(value):
        if isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, dict) and isinstance(text.get("content"), str):
                values.append(text["content"])
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)

    collect(payload["card"]["elements"])
    return "\n".join(values)


def payload_bytes(payload):
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def assert_no_none(value):
    if isinstance(value, dict):
        for item in value.values():
            assert item is not None
            assert_no_none(item)
    elif isinstance(value, list):
        for item in value:
            assert item is not None
            assert_no_none(item)


def test_card_builders_use_strict_domain_model_signatures():
    assert get_type_hints(build_daily_card)["report"] is RunReport
    assert get_type_hints(build_severe_card)["risk"] is RequirementRisk
    assert get_type_hints(build_data_error_card)["issues"] == list[ValidationIssue]


def test_mention_escapes_identifier_and_visible_name():
    rendered = mention('ou-"bad', "张<三>&")

    assert rendered == '<at id="ou-&quot;bad">张&lt;三&gt;&amp;</at>'


def test_interactive_card_enforces_utf8_payload_and_element_budgets():
    business_lines = [
        "业务行 {}｜{}".format(index, "中文内容" * 80) for index in range(100)
    ]

    payload = interactive_card("中文预算卡片", "yellow", business_lines)
    contents = [element["text"]["content"] for element in payload["card"]["elements"]]

    assert payload["card"]["config"]["wide_screen_mode"] is True
    assert payload_bytes(payload) <= 18 * 1024
    assert len(contents) <= 40
    assert all(len(content.encode("utf-8")) <= 1800 for content in contents)
    assert contents[-1] == "内容过长，请查看多维表格"
    assert all(content in business_lines for content in contents[:-1])
    assert_no_none(payload)


def test_long_bold_value_is_truncated_before_trusted_markdown_wrapper():
    risk = make_risk(
        name="超长需求" * 3000,
        nodes=[make_node_risk("node", "开发", at(25))],
    )

    payload = build_daily_card(make_report([risk]))
    text = card_text(payload)

    assert text.count("**") % 2 == 0
    assert payload_bytes(payload) <= 18 * 1024


def test_daily_card_labels_requirement_launch_as_server_launch():
    payload = build_daily_card(make_report([make_risk()]))
    text = card_text(payload)

    assert "服务端上线：2026-08-02" in text
    assert text.index("服务端上线：") < text.index("合板：")


def test_interactive_card_does_not_split_multiline_markdown_markers():
    payload = interactive_card("模板完整性", "blue", ["**粗体\n续行**"])
    contents = [element["text"]["content"] for element in payload["card"]["elements"]]

    assert contents == ["**粗体\n续行**"]
    assert contents[0].count("**") == 2


def test_interactive_card_replaces_empty_content_with_valid_placeholder():
    payload = interactive_card("", "blue", [None])
    contents = [element["text"]["content"] for element in payload["card"]["elements"]]

    assert contents == ["暂无内容"]


def test_long_mention_name_is_truncated_without_breaking_at_tag():
    long_name = "节点负责人" * 2000
    risk = make_risk(
        nodes=[
            make_node_risk(
                "node",
                "开发",
                at(25),
                owner_id="ou-long-owner",
                owner_name=long_name,
            )
        ]
    )

    payload = build_daily_card(make_report([risk]))
    text = card_text(payload)
    matches = re.findall(r'<at id="ou-long-owner">.*?</at>', text)

    assert matches
    assert all(match.endswith("</at>") for match in matches)
    assert all(len(match.encode("utf-8")) <= 256 for match in matches)
    assert text.count('<at id="ou-long-owner">') == text.count("</at>")
    assert payload_bytes(payload) <= 18 * 1024


def test_daily_card_mentions_severe_node_owner():
    risk = make_risk(
        level=RiskLevel.SEVERE,
        nodes=[
            make_node_risk(
                "node-li",
                "PV 测试第二轮",
                at(1),
                owner_id="ou-li-minghua",
                owner_name="李名华",
                level=RiskLevel.SEVERE,
            )
        ],
    )

    payload = build_daily_card(make_report([risk]))

    assert '<at id="ou-li-minghua">李名华</at>' in card_text(payload)


def test_escape_value_neutralizes_newlines_and_markdown_block_injection():
    raw = (
        "第一行\n# 标题\r> 引用\t- 列表\n--- 分隔 "
        "*粗体* [链接](url) | 表格"
    )

    escaped = escape_value(raw)

    assert "\n" not in escaped
    assert "\r" not in escaped
    assert "\t" not in escaped
    assert ">" not in escaped
    assert "&gt;" in escaped
    for marker in (
        r"\#",
        r"\-",
        r"\---",
        r"\*",
        r"\[",
        r"\]",
        r"\(",
        r"\)",
        r"\|",
    ):
        assert marker in escaped


def test_daily_card_uses_structured_stage_matrix_and_real_mentions():
    risks = make_owner_risks(2)
    payload = build_daily_card(make_report(risks, llm_attempted=True, llm_degraded=True))
    text = card_text(payload)
    column_sets = [element for element in payload["card"]["elements"] if element["tag"] == "column_set"]

    assert payload["card"]["header"]["template"] == "yellow"
    assert column_sets
    for label in ("环节", "交付域", "负责人", "计划开始", "计划完成", "最晚安全DDL", "状态"):
        assert label in text
    assert '<at id="ou-owner-00">负责人00</at>' in text
    assert '<at id="ou-owner-01">负责人01</at>' in text
    assert "AI 补充分析不可用，基础规则正常运行" in text
    assert payload_bytes(payload) <= 18 * 1024
    assert_no_none(payload)


@pytest.mark.parametrize("owner_count", [30, 50, 150])
def test_daily_card_truncates_structured_rows_within_card_limits(owner_count):
    payload = build_daily_card(make_report(make_owner_risks(owner_count)))
    text = card_text(payload)

    assert payload_bytes(payload) <= 18 * 1024
    assert len(payload["card"]["elements"]) <= 40
    assert "内容过长，请查看多维表格" in text


def test_daily_card_reserves_llm_footer_before_secondary_node_truncation():
    nodes = [
        make_node_risk(
            "node-{:03d}".format(index),
            "节点{:03d}".format(index),
            at(25),
        )
        for index in range(100)
    ]
    payload = build_daily_card(
        make_report(
            [make_risk(nodes=nodes)],
            llm_attempted=True,
            llm_degraded=True,
        )
    )
    text = card_text(payload)
    llm_notice = "AI 补充分析不可用，基础规则正常运行"

    assert payload_bytes(payload) <= 18 * 1024
    assert len(payload["card"]["elements"]) <= 40
    assert '<at id="ou-zhang">张三</at>' in text
    assert "节点000" in text
    assert "节点001" in text
    assert llm_notice in text
    assert text.index(llm_notice) < text.index("节点001")
    assert "内容过长，请查看多维表格" in text


@pytest.mark.parametrize(
    ("level", "template"),
    [
        (RiskLevel.NORMAL, "blue"),
        (RiskLevel.WARNING, "yellow"),
        (RiskLevel.SEVERE, "red"),
    ],
)
def test_daily_card_header_matches_highest_risk(level, template):
    risk = make_risk(
        level=level,
        nodes=[make_node_risk("node", "开发", at(25), level=level)],
    )

    payload = build_daily_card(make_report([risk]))

    assert payload["card"]["header"]["template"] == template


def test_daily_card_footer_requires_attempted_failed_llm():
    risk = make_risk(nodes=[make_node_risk("node", "开发", at(25))])

    not_attempted = build_daily_card(make_report([risk], llm_degraded=False))
    successful = build_daily_card(
        make_report([risk], llm_attempted=True, llm_degraded=False)
    )

    assert "AI 补充分析不可用" not in str(not_attempted)
    assert "AI 补充分析不可用" not in str(successful)


def test_severe_card_replaces_node_matrix_with_family_context(monkeypatch):
    monkeypatch.setattr(
        risk_grouping,
        "group_risk_families",
        lambda findings, stage_order: [
            make_risk_family(
                "schedule.window",
                "合板窗口不足",
                stage_refs=["服务端联调", "客户端联调"],
                domain_refs=["服务端", "客户端"],
            )
        ],
        raising=False,
    )
    risk = make_risk(
        requirement_id="REQ-9",
        name="登陆安全",
        level=RiskLevel.SEVERE,
        nodes=[
            make_node_risk(
                "node-9",
                "服务端联调",
                in_aug(6),
                requirement_id="REQ-9",
                safe_deadline=in_aug(2),
                level=RiskLevel.SEVERE,
            ).model_copy(
                update={
                    "owners": [
                        Person(open_id="ou-zhang", name="张三"),
                        Person(open_id="ou-li", name="李四"),
                    ]
                }
            ),
            make_node_risk(
                "node-10",
                "客户端联调",
                in_aug(5),
                requirement_id="REQ-9",
                owner_id="ou-zhang",
                owner_name="张三",
                domain="客户端",
                safe_deadline=in_aug(1),
                level=RiskLevel.SEVERE,
            ),
            make_node_risk(
                "node-unrelated",
                "发布准备",
                in_aug(7),
                requirement_id="REQ-9",
                owner_id="ou-unrelated",
                owner_name="无关负责人",
                domain="市场",
                safe_deadline=at(31),
                level=RiskLevel.SEVERE,
            ),
        ],
        blockers=[
            make_blocker(
                requirement_id="REQ-9",
                title="等待鉴权方案",
                planned_resolution_at=in_aug(1),
            )
        ],
        predicted_completion=in_aug(6),
        buffer_days=-3,
        affected_domains=["服务端", "客户端"],
        reasons=["联调已晚于安全截止"],
        actions=["项目负责人协调资源", "节点负责人今日反馈"],
    )

    payload = build_severe_card(risk)
    text = card_text(payload)

    assert payload["card"]["header"]["template"] == "red"
    assert '<at id="ou-project">项目负责人</at>' in text
    assert text.count('<at id="ou-zhang">张三</at>') == 1
    assert text.count('<at id="ou-li">李四</at>') == 1
    assert "无关负责人" not in text
    assert '<at id="ou-blocker">王五</at>' in text
    assert set(re.findall(r'<at id="([^"]+)">', text)) == {
        "ou-project",
        "ou-zhang",
        "ou-li",
        "ou-blocker",
    }
    assert len(payload["card"]["elements"]) <= 8
    assert "##" not in text
    assert "###" not in text
    assert "---" not in text
    for label in (
        "登陆安全",
        "OKR：米家",
        "版本：8.0",
        "合板 08-03",
        "延期 3 自然日",
        "合板窗口不足｜严重",
        "环节：服务端联调、客户端联调",
        "交付域：服务端、客户端",
        "最早安全 DDL：08-01",
        "阻塞项",
        "等待鉴权方案",
        "项目负责人协调资源",
    ):
        assert label in text
    for removed in (
        "计划开始",
        "计划完成",
        "最晚安全DDL",
        "相关负责人",
        "联调已晚于安全截止",
    ):
        assert removed not in text
    assert_no_none(payload)


def test_severe_card_shows_schedule_formula_and_duration_source():
    risk = make_risk(
        level=RiskLevel.SEVERE,
        predicted_completion=in_aug(6),
        schedule_formula=ScheduleFormula(
            domain="车辆",
            started_at=at(28, 20),
            duration_mode="workday",
            terms=[
                ScheduleFormulaTerm(
                    label="PV 测试第二轮", days=2, source="固定规则默认值"
                ),
                ScheduleFormulaTerm(label="线上回归", days=2),
            ],
            predicted_completion=in_aug(6, 20),
        ),
    )

    payload = build_severe_card(risk)
    summary = payload["card"]["elements"][1]["text"]["content"]

    assert len(summary.splitlines()) <= 3
    assert "合板 08-03｜预计 08-06｜延期 3 自然日｜关键路径 车辆" in summary
    assert summary.count("计算：") == 1
    assert "PV 测试第二轮 2 工作日（固定规则默认值）" in summary
    assert "线上回归 2 工作日" in summary
    assert "→ 08-06 20:00" in summary
    assert "上线" not in summary
    assert "延期计算" not in summary
    assert "计算公式" not in summary


def test_severe_card_uses_project_schedule_fallback_and_merges_warnings(monkeypatch):
    monkeypatch.setattr(
        risk_grouping,
        "group_risk_families",
        lambda findings, stage_order: [
            make_risk_family(
                "schedule.missing",
                "排期缺失",
                stage_refs=[],
                domain_refs=[],
            ),
            make_risk_family(
                "buffer.low",
                "低缓冲",
                level=RiskLevel.WARNING,
                stage_refs=["各端开发"],
                domain_refs=["客户端"],
            ),
            make_risk_family(
                "translation.missing",
                "多语言翻译未完成",
                level=RiskLevel.WARNING,
                stage_refs=["线上回归"],
                domain_refs=["车辆"],
            ),
        ],
        raising=False,
    )
    risk = make_risk(
        level=RiskLevel.SEVERE,
        current_stage="需求评审",
        nodes=[],
        findings=[
            RiskFinding(
                reason_code="schedule.missing",
                reason_text="缺少项目排期",
                stage_refs=[],
                domain_refs=[],
                level=RiskLevel.SEVERE,
                source="test",
            )
        ],
    )

    text = card_text(build_severe_card(risk))

    assert "排期缺失｜严重" in text
    assert "环节：项目排期" in text
    assert "未标注" not in text
    assert text.count('<at id="ou-project">项目负责人</at>') == 4
    assert text.count("其他预警") == 1
    assert "低缓冲" in text
    assert "环节：各端开发" in text
    assert "交付域：客户端" in text
    assert "多语言翻译未完成" in text
    assert "环节：线上回归" in text
    assert "交付域：车辆" in text
    assert "低缓冲｜预警" not in text
    assert "多语言翻译未完成｜预警" not in text


def test_severe_card_keeps_each_real_warning_family_inside_one_warning_section():
    risk = make_risk(
        level=RiskLevel.SEVERE,
        findings=[
            RiskFinding(
                reason_code="schedule.buffer_negative",
                reason_text="剩余缓冲为负",
                domain_refs=["客户端"],
                level=RiskLevel.SEVERE,
                source="test",
            ),
            RiskFinding(
                reason_code="node.delay_with_buffer",
                reason_text="节点延期但仍有缓冲",
                stage_refs=["服务端上线"],
                domain_refs=["服务端"],
                level=RiskLevel.WARNING,
                source="test",
            ),
            RiskFinding(
                reason_code="schedule.buffer_low",
                reason_text="剩余缓冲不超过2天",
                domain_refs=["服务端"],
                level=RiskLevel.WARNING,
                source="test",
            ),
            RiskFinding(
                reason_code="translation.incomplete",
                reason_text="多语言翻译未完成，建议合板前完成",
                stage_refs=["多语言翻译"],
                domain_refs=["平台"],
                level=RiskLevel.WARNING,
                source="test",
            ),
        ],
    )

    text = card_text(build_severe_card(risk))

    assert text.count("其他预警") == 1
    assert "节点延期但仍有缓冲" in text
    assert "剩余缓冲不足" in text
    assert "多语言翻译未完成" in text


def test_severe_card_renders_process_reminders_as_unmentioned_footer():
    risk = make_risk(
        level=RiskLevel.SEVERE,
        process_reminders=["线上回归", "PV 测试第二轮", "线上回归"],
        findings=[
            RiskFinding(
                reason_code="schedule.buffer_negative",
                reason_text="剩余缓冲为负",
                level=RiskLevel.SEVERE,
                source="test",
            )
        ],
    )

    text = card_text(build_severe_card(risk))
    reminder = (
        "流程补充提醒：尚未维护 PV 测试第二轮、线上回归；"
        "如项目涉及，请后续补充。"
    )

    assert reminder in text
    assert "<at" not in text[text.index(reminder) : text.index(reminder) + len(reminder)]
    assert text.index(reminder) > text.index("处理动作")


def test_daily_card_renders_process_reminders_in_requirement_summary():
    risk = make_risk(
        level=RiskLevel.NORMAL,
        reasons=[],
        actions=[],
        affected_domains=[],
        process_reminders=["多语言翻译", "线上回归"],
    )

    text = card_text(build_daily_card(make_report([risk])))

    assert (
        "流程补充提醒：尚未维护 线上回归、多语言翻译；"
        "如项目涉及，请后续补充。"
    ) in text


def test_risk_family_owner_matching_uses_stage_and_domain_intersection(monkeypatch):
    monkeypatch.setattr(
        risk_grouping,
        "group_risk_families",
        lambda findings, stage_order: [
            make_risk_family(
                "test.duration",
                "测试周期不足",
                stage_refs=["PV 测试第二轮"],
                domain_refs=["车辆"],
            )
        ],
    )
    risk = make_risk(
        level=RiskLevel.SEVERE,
        findings=[
            RiskFinding(
                reason_code="test.duration_below_minimum",
                reason_text="测试周期低于最低要求",
                stage_refs=["PV 测试第二轮"],
                domain_refs=["车辆"],
                level=RiskLevel.SEVERE,
                source="test",
            )
        ],
        nodes=[
            make_node_risk(
                "client-pv2",
                "PV 测试第二轮",
                at(25),
                domain="客户端",
                owner_id="ou-client",
                owner_name="客户端负责人",
            ),
            make_node_risk(
                "vehicle-pv1",
                "PV 测试第一轮",
                at(24),
                domain="车辆",
                owner_id="ou-vehicle-pv1",
                owner_name="车辆一轮负责人",
            ),
            make_node_risk(
                "vehicle-pv2",
                "PV 测试第二轮",
                at(23),
                domain="车辆",
                owner_id="ou-vehicle-pv2",
                owner_name="车辆二轮负责人",
            ),
        ],
    )

    text = card_text(build_severe_card(risk))

    assert '<at id="ou-vehicle-pv2">车辆二轮负责人</at>' in text
    assert 'id="ou-client"' not in text
    assert 'id="ou-vehicle-pv1"' not in text
    assert "最早安全 DDL：07-23" in text


def test_grouped_risk_blocks_merge_same_reason_and_keep_context_links_and_mentions():
    findings = [
        RiskFinding(
            reason_code="test_gate.at_incomplete",
            reason_text="AT 测试未通过，不能进入 PV",
            stage_refs=["AT 测试第二轮"],
            domain_refs=["车辆"],
            level=RiskLevel.SEVERE,
            source="test",
        ),
        RiskFinding(
            reason_code="test_gate.at_incomplete",
            reason_text="AT 测试未通过，不能进入 PV",
            stage_refs=["AT 测试第一轮"],
            domain_refs=["客户端"],
            level=RiskLevel.SEVERE,
            source="test",
        ),
    ]
    risk = make_risk(
        level=RiskLevel.SEVERE,
        findings=findings,
        reasons=["AT 测试未通过，不能进入 PV"],
        nodes=[
                make_node_risk(
                    "at-1",
                    "AT 测试第一轮",
                    at(25),
                    domain="客户端",
                    level=RiskLevel.SEVERE,
                )
        ],
    ).model_copy(
        update={
            "requirement_doc_url": "https://example.test/doc",
            "meego_url": "https://example.test/meego",
        }
    )

    payloads = build_severe_cards(risk)
    rendered = json.dumps(payloads, ensure_ascii=False)

    assert len(payloads) == 1
    assert rendered.count("门禁未通过") == 1
    assert "AT 测试未通过，不能进入 PV" not in rendered
    assert "AT 测试第一轮、AT 测试第二轮" in rendered
    assert "车辆、客户端" in rendered
    assert "**风险原因**：AT 测试未通过，不能进入 PV" not in rendered
    assert '"url": "https://example.test/doc"' in rendered
    assert '"url": "https://example.test/meego"' in rendered
    text = card_text(payloads[0])
    assert '<at id="ou-project">项目负责人</at>' in text
    assert '<at id="ou-zhang">张三</at>' in text


def test_daily_grouped_cards_split_without_losing_any_risk_group():
    findings = [
        RiskFinding(
            reason_code="reason.{:03d}".format(index),
            reason_text="结构化风险原因 {:03d}".format(index),
            stage_refs=["阶段{:03d}".format(index)],
            domain_refs=["域{:03d}".format(index)],
            level=RiskLevel.WARNING,
            source="test",
        )
        for index in range(60)
    ]
    payloads = build_daily_cards(
        make_report(
            [
                make_risk(
                    findings=findings,
                    reasons=[finding.reason_text for finding in findings],
                )
            ]
        )
    )
    rendered = [json.dumps(payload, ensure_ascii=False) for payload in payloads]

    assert len(payloads) > 1
    assert "第 1/{} 部分".format(len(payloads)) in rendered[0]
    assert "第 {}/{} 部分".format(len(payloads), len(payloads)) in rendered[-1]
    for finding in findings:
        assert sum(finding.reason_text in part for part in rendered) == 1
    assert all("内容过长，请查看多维表格" not in part for part in rendered)


def test_severe_grouped_cards_split_without_losing_any_risk_group():
    findings = [
        RiskFinding(
            reason_code="severe.reason.{:03d}".format(index),
            reason_text="严重结构化风险 {:03d}".format(index),
            stage_refs=["阶段{:03d}".format(index)],
            domain_refs=["域{:03d}".format(index)],
            level=RiskLevel.SEVERE,
            source="test",
        )
        for index in range(60)
    ]
    payloads = build_severe_cards(
        make_risk(
            level=RiskLevel.SEVERE,
            findings=findings,
            reasons=[finding.reason_text for finding in findings],
        )
    )
    rendered = [json.dumps(payload, ensure_ascii=False) for payload in payloads]

    assert len(payloads) > 1
    assert "第 1/{} 部分".format(len(payloads)) in rendered[0]
    assert "第 {}/{} 部分".format(len(payloads), len(payloads)) in rendered[-1]
    for finding in findings:
        assert sum(finding.reason_text in part for part in rendered) == 1
    assert all("内容过长，请查看多维表格" not in part for part in rendered)


def test_oversized_links_and_owner_mentions_are_preserved_across_parts():
    owners = [
        Person(open_id="ou-owner-{:03d}".format(index), name="负责人{:03d}".format(index))
        for index in range(60)
    ]
    node = make_node_risk(
        "oversized-node",
        "开发",
        at(25),
        level=RiskLevel.SEVERE,
    ).model_copy(update={"owners": owners})
    risk = make_risk(
        level=RiskLevel.SEVERE,
        current_stage="开发",
        nodes=[node],
        findings=[
            RiskFinding(
                reason_code="oversized.context",
                reason_text="保留超大上下文",
                stage_refs=["开发"],
                domain_refs=["客户端"],
                level=RiskLevel.SEVERE,
                source="test",
            )
        ],
    ).model_copy(
        update={
            "requirement_doc_url": "https://example.test/doc/" + "d" * 2600,
            "meego_url": "https://example.test/meego/" + "m" * 2600,
            "translation_url": "https://example.test/translation/" + "t" * 2600,
        }
    )

    payloads = build_severe_cards(risk)
    rendered = json.dumps(payloads, ensure_ascii=False)

    for url in (
        risk.requirement_doc_url,
        risk.meego_url,
        risk.translation_url,
    ):
        assert rendered.count(url) == 1
    text = "\n".join(card_text(payload) for payload in payloads)
    for owner in owners:
        assert text.count('<at id="{}">'.format(owner.open_id)) >= 1
    assert all(payload_bytes(payload) <= 18 * 1024 for payload in payloads)
    assert all(len(payload["card"]["elements"]) <= 40 for payload in payloads)


def test_oversized_url_action_becomes_visible_overflow_notice():
    oversized_url = "https://example.test/" + "u" * (36 * 1024)
    risk = make_risk(
        level=RiskLevel.SEVERE,
        findings=[
            RiskFinding(
                reason_code="oversized.url",
                reason_text="链接 action 过长",
                stage_refs=["开发"],
                domain_refs=["客户端"],
                level=RiskLevel.SEVERE,
                source="test",
            )
        ],
    ).model_copy(update={"requirement_doc_url": oversized_url})

    payloads = build_severe_cards(risk)
    rendered = json.dumps(payloads, ensure_ascii=False)
    text = "\n".join(card_text(payload) for payload in payloads)

    assert payloads
    assert oversized_url not in rendered
    assert "链接内容过长，请查看多维表格" in text
    assert all(payload_bytes(payload) <= 18 * 1024 for payload in payloads)
    assert all(len(payload["card"]["elements"]) <= 40 for payload in payloads)


def test_url_action_budget_uses_maximum_title_bytes():
    oversized_for_short_title_url = "https://example.test/" + "u" * 17900
    risk = make_risk(
        level=RiskLevel.SEVERE,
        project="P" * 100,
        name="N" * 100,
        findings=[
            RiskFinding(
                reason_code="title.budget.url",
                reason_text="标题预算下的链接 action",
                stage_refs=["开发"],
                domain_refs=["客户端"],
                level=RiskLevel.SEVERE,
                source="test",
            )
        ],
    ).model_copy(update={"requirement_doc_url": oversized_for_short_title_url})

    payloads = build_severe_cards(risk)
    rendered = json.dumps(payloads, ensure_ascii=False)
    text = "\n".join(card_text(payload) for payload in payloads)

    assert payloads
    assert oversized_for_short_title_url not in rendered
    assert "链接内容过长，请查看多维表格" in text
    assert all(payload_bytes(payload) <= 18 * 1024 for payload in payloads)
    assert all(len(payload["card"]["elements"]) <= 40 for payload in payloads)


def test_single_oversized_risk_group_stays_bounded_with_visible_notice():
    huge_reason = "超长风险原因" * 10000
    huge_stages = ["阶段{:04d}".format(index) for index in range(500)]
    huge_domains = ["域{:04d}".format(index) for index in range(500)]
    risk = make_risk(
        level=RiskLevel.SEVERE,
        findings=[
            RiskFinding(
                reason_code="oversized.group",
                reason_text=huge_reason,
                stage_refs=huge_stages,
                domain_refs=huge_domains,
                level=RiskLevel.SEVERE,
                source="test",
            )
        ],
    )

    payloads = build_severe_cards(risk)
    text = "\n".join(card_text(payload) for payload in payloads)

    assert payloads
    assert all(payload_bytes(payload) <= 18 * 1024 for payload in payloads)
    assert all(len(payload["card"]["elements"]) <= 40 for payload in payloads)
    assert "超长风险原因" in text
    assert "风险组内容过长" in text


def test_many_missing_schedule_domains_keep_every_card_element_bounded():
    findings = [
        RiskFinding(
            reason_code="schedule.buffer_negative",
            reason_text="剩余缓冲为负",
            stage_refs=[
                "AT 测试第一轮",
                "AT 测试第二轮",
                "PV 测试第一轮",
                "PV 测试第二轮",
                "线上回归",
            ],
            domain_refs=["超长交付域{:03d}{}".format(index, "域" * 40)],
            level=RiskLevel.SEVERE,
            source="domain_schedule",
        )
        for index in range(40)
    ]
    risk = make_risk(level=RiskLevel.SEVERE, findings=findings)

    payloads = build_severe_cards(risk)

    assert payloads
    for payload in payloads:
        for element in payload["card"]["elements"]:
            assert len(cards._element_text(element).encode("utf-8")) <= 1800


def test_singular_severe_wrapper_keeps_first_card_with_split_notice():
    findings = [
        RiskFinding(
            reason_code="wrapper.reason.{:03d}".format(index),
            reason_text="wrapper 风险原因 {:03d}".format(index),
            stage_refs=["阶段{:03d}".format(index)],
            domain_refs=["域{:03d}".format(index)],
            level=RiskLevel.SEVERE,
            source="test",
        )
        for index in range(60)
    ]

    risk = make_risk(
        level=RiskLevel.SEVERE,
        findings=findings,
        reasons=[finding.reason_text for finding in findings],
    )
    plural_payloads = build_severe_cards(risk)
    payload = build_severe_card(risk)

    singular_rendered = json.dumps(payload, ensure_ascii=False)
    first_part_text = card_text(plural_payloads[0])
    assert "内容已拆分为多张卡片" in singular_rendered
    for finding in findings:
        if finding.reason_text in first_part_text:
            assert finding.reason_text in card_text(payload)
    assert payload_bytes(payload) <= 18 * 1024
    assert len(payload["card"]["elements"]) <= 40


def test_singular_severe_wrapper_preserves_notice_suffix_for_long_title():
    findings = [
        RiskFinding(
            reason_code="long.title.wrapper.{:03d}".format(index),
            reason_text="长标题 wrapper 风险原因 {:03d}".format(index),
            stage_refs=["阶段{:03d}".format(index)],
            domain_refs=["域{:03d}".format(index)],
            level=RiskLevel.SEVERE,
            source="test",
        )
        for index in range(60)
    ]
    risk = make_risk(
        level=RiskLevel.SEVERE,
        project="P" * 200,
        name="N" * 100,
        findings=findings,
        reasons=[finding.reason_text for finding in findings],
    )

    plural_payloads = build_severe_cards(risk)
    payload = build_severe_card(risk)
    title = payload["card"]["header"]["title"]["content"]
    first_part_text = card_text(plural_payloads[0])
    rendered = json.dumps(payload, ensure_ascii=False)
    notice_suffix = "｜内容已拆分为多张卡片，请继续查看后续部分"

    assert title.endswith(notice_suffix)
    assert notice_suffix in rendered
    for finding in findings:
        if finding.reason_text in first_part_text:
            assert finding.reason_text in card_text(payload)
    assert payload_bytes(payload) <= 18 * 1024
    assert len(payload["card"]["elements"]) <= 40


@pytest.mark.parametrize(
    ("scope", "scope_text", "skip_text"),
    [
        ("record", "隔离范围：仅跳过当前记录", "是否跳过该需求：否"),
        ("requirement", "隔离范围：跳过当前需求", "是否跳过该需求：是"),
        ("run", "隔离范围：停止本次运行", "是否跳过该需求：整次运行停止"),
    ],
)
def test_data_error_card_uses_explicit_skip_scope(scope, scope_text, skip_text):
    issue = ValidationIssue(
        table_name="进展<节点>表",
        requirement_id="REQ-10",
        record_id="rec-10",
        field_name="计划 DDL",
        current_value="tomorrow & later",
        expected_format="RFC3339 <datetime>",
        fix_suggestion="改为 2026-07-25T18:00:00+08:00",
        skip_scope=scope,
        message="日期格式错误",
    )

    payload = build_data_error_card([issue])
    text = card_text(payload)

    assert payload["card"]["header"]["template"] == "red"
    for label in (
        "表名：进展&lt;节点&gt;表",
        "需求：REQ-10",
        "记录标识：rec-10",
        "错误字段：计划 DDL",
        "当前错误值：tomorrow &amp; later",
        "预期格式：RFC3339 &lt;datetime&gt;",
        "修复建议：改为 2026-07-25T18:00:00+08:00",
        scope_text,
        skip_text,
        "错误说明：日期格式错误",
    ):
        assert label in text
    assert_no_none(payload)


def test_plain_text_fallback_removes_none_values():
    payload = build_plain_text_fallback(None, ["第一行", None, 42])

    assert payload == {
        "msg_type": "text",
        "content": {"text": "\n第一行\n\n42"},
    }
    assert_no_none(payload)
