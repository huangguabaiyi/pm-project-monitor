import json
import re
from datetime import datetime
from typing import get_type_hints
from zoneinfo import ZoneInfo

import pytest

from requirement_monitor.cards import (
    build_daily_card,
    build_data_error_card,
    build_plain_text_fallback,
    build_severe_card,
    escape_value,
    interactive_card,
    mention,
)
from requirement_monitor.models import (
    Blocker,
    LLMEnrichment,
    NodeRisk,
    NodeStatus,
    RequirementRisk,
    RiskLevel,
    RunReport,
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
):
    return RequirementRisk(
        requirement_record_id="rec-{}".format(requirement_id),
        requirement_id=requirement_id,
        requirement_name=name,
        project=project,
        target_version=version,
        merge_at=in_aug(3),
        launch_at=in_aug(5),
        project_owner_id=owner_id,
        project_owner_name=owner_name,
        level=level,
        predicted_completion=predicted_completion or in_aug(2),
        buffer_days=buffer_days,
        affected_domains=affected_domains or ["服务端"],
        reasons=reasons or ["合板缓冲不足"],
        actions=actions or ["今天完成联调"],
        node_risks=nodes or [],
        blockers=blockers or [],
        llm_enrichment=llm_enrichment,
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
    return "\n".join(
        element["text"]["content"] for element in payload["card"]["elements"]
    )


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
    contents = [element["text"]["content"] for element in payload["card"]["elements"]]
    bold_lines = [content for content in contents if content.startswith("**需求摘要｜")]

    assert bold_lines
    assert all("**｜项目：" in content for content in bold_lines)
    assert all(content.count("**") % 2 == 0 for content in bold_lines)
    assert payload_bytes(payload) <= 18 * 1024


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


def test_daily_card_groups_projects_and_owners_with_required_ordering():
    first_nodes = [
        make_node_risk("late", "逾期节点", at(23), safe_deadline=at(22)),
        make_node_risk("today", "今日节点", at(24), domain="客户端"),
        make_node_risk("future", "普通未来节点", at(25), domain="车辆"),
        make_node_risk(
            "warning",
            "预警节点",
            at(28),
            level=RiskLevel.WARNING,
            safe_deadline=at(26),
        ),
        make_node_risk("parallel", "并行节点", at(28)),
        make_node_risk("later", "八天后节点", in_aug(5)),
        make_node_risk(
            "done",
            "已完成节点",
            at(24),
            status=NodeStatus.COMPLETED,
        ),
    ]
    first = make_risk(
        nodes=first_nodes,
        blockers=[make_blocker()],
        llm_enrichment=LLMEnrichment(
            available=False,
            rule_level=RiskLevel.WARNING,
            effective_level=RiskLevel.WARNING,
            failure_reason="timeout",
        ),
    )
    second = make_risk(
        requirement_id="REQ-2",
        name="固件升级",
        project="车载",
        level=RiskLevel.SEVERE,
        owner_id="ou-car-owner",
        owner_name="车载负责人",
        nodes=[
            make_node_risk(
                "li",
                "嵌入式开发",
                at(27),
                requirement_id="REQ-2",
                owner_id="ou-li",
                owner_name="李四",
                domain="嵌入式",
                level=RiskLevel.SEVERE,
            )
        ],
        buffer_days=-1,
    )

    payload = build_daily_card(
        make_report([first, second], llm_attempted=True, llm_degraded=True)
    )
    text = card_text(payload)
    first_element = payload["card"]["elements"][0]["text"]["content"]

    assert payload["card"]["header"]["template"] == "red"
    assert "需求 2｜普通 0｜预警 1｜严重 1" in payload["card"]["header"]["title"]["content"]
    assert text.index("## 米家") < text.index("### 张三") < text.index("## 车载")
    assert text.index("逾期节点") < text.index("今日节点")
    assert text.index("今日节点") < text.index("预警节点")
    assert text.index("预警节点") < text.index("普通未来节点")
    assert "并行节点" in text
    assert "八天后节点" not in text
    assert "已完成节点" not in text
    assert "7 天后待办：1 项" in text
    assert '<at id="ou-zhang">张三</at>' in text
    assert "账号&lt;迁移&gt;" in text
    assert "需求｜交付域｜节点｜计划 DDL｜最晚安全 DDL｜状态" in text
    assert "版本：8.0" in text
    assert "合板：2026-08-03 18:00" in text
    assert "上线：2026-08-05 18:00" in text
    assert "缓冲：3 天" in text
    assert "阻塞：等待 API &amp; 权限" in text
    assert "AI 补充分析不可用，基础规则正常运行" in text
    assert "### 张三" in first_element
    assert '<at id="ou-zhang">张三</at>' in first_element
    assert "逾期节点" in first_element
    assert text.index("普通未来节点") < text.index("**需求摘要｜")
    assert_no_none(payload)


def test_daily_card_keeps_every_owner_and_top_node_before_30_summaries():
    risks = make_owner_risks(30)

    payload = build_daily_card(make_report(risks))
    text = card_text(payload)

    assert payload_bytes(payload) <= 18 * 1024
    for index in range(30):
        owner_id = "ou-owner-{:02d}".format(index)
        owner_name = "负责人{:02d}".format(index)
        assert owner_name in text
        assert '<at id="{}">{}</at>'.format(owner_id, owner_name) in text
        assert "节点{:02d}".format(index) in text


@pytest.mark.parametrize("owner_count", [40, 50])
def test_daily_card_packs_all_short_owner_groups_within_limits(owner_count):
    payload = build_daily_card(make_report(make_owner_risks(owner_count)))
    text = card_text(payload)
    owner_elements = [
        element
        for element in payload["card"]["elements"]
        if '<at id="ou-owner-' in element["text"]["content"]
    ]

    assert payload_bytes(payload) <= 18 * 1024
    assert len(payload["card"]["elements"]) <= 40
    assert len(owner_elements) < owner_count
    assert any(
        element["text"]["content"].count('<at id="ou-owner-') > 1
        for element in owner_elements
    )
    assert all(
        len(element["text"]["content"].encode("utf-8")) <= 1800
        for element in owner_elements
    )
    assert "负责人信息已展示" not in text
    for index in range(owner_count):
        owner_id = "ou-owner-{:02d}".format(index)
        owner_name = "负责人{:02d}".format(index)
        assert '<at id="{}">{}</at>'.format(owner_id, owner_name) in text
        assert "节点{:02d}".format(index) in text


def test_daily_card_reports_owner_count_when_owner_groups_exceed_budget():
    owner_count = 150
    payload = build_daily_card(make_report(make_owner_risks(owner_count)))
    text = card_text(payload)
    match = re.search(
        r"负责人信息已展示 (\d+)/150，剩余 (\d+) 位请查看多维表格",
        text,
    )

    assert payload_bytes(payload) <= 18 * 1024
    assert len(payload["card"]["elements"]) <= 40
    assert match is not None
    shown = int(match.group(1))
    remaining = int(match.group(2))
    assert shown + remaining == owner_count
    assert text.count('<at id="ou-owner-') == shown


def test_daily_card_reserves_llm_footer_before_owner_truncation_notice():
    owner_count = 150
    payload = build_daily_card(
        make_report(
            make_owner_risks(owner_count),
            llm_attempted=True,
            llm_degraded=True,
        )
    )
    text = card_text(payload)
    match = re.search(
        r"负责人信息已展示 (\d+)/150，剩余 (\d+) 位请查看多维表格",
        text,
    )
    llm_notice = "AI 补充分析不可用，基础规则正常运行"

    assert payload_bytes(payload) <= 18 * 1024
    assert len(payload["card"]["elements"]) <= 40
    assert match is not None
    shown = int(match.group(1))
    remaining = int(match.group(2))
    assert shown + remaining == owner_count
    assert text.count('<at id="ou-owner-') == shown
    assert llm_notice in text
    assert text.index('<at id="ou-owner-') < text.index(llm_notice)
    assert text.index(llm_notice) < text.index(match.group(0))


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


def test_severe_card_mentions_project_owner_and_has_complete_context():
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
            )
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
    assert '<at id="ou-zhang">张三</at>' in text
    for label in (
        "需求：登陆安全",
        "项目：米家",
        "版本：8.0",
        "统一合板：2026-08-03 18:00",
        "当前预计完成：2026-08-06 18:00",
        "预计延期：3 天",
        "受影响交付域：服务端、客户端",
        "判定原因：联调已晚于安全截止",
        "阻塞：等待鉴权方案",
        "行动：项目负责人协调资源",
        "最晚处理时间：2026-08-01 18:00",
        "节点负责人：",
        "项目负责人：",
    ):
        assert label in text
    assert_no_none(payload)


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
