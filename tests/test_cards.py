from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from requirement_monitor.cards import (
    build_daily_card,
    build_data_error_card,
    build_plain_text_fallback,
    build_severe_card,
    interactive_card,
    mention,
)
from requirement_monitor.models import RiskLevel, ValidationIssue


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 24, 20, 0, tzinfo=TZ)


def at(day, hour=18):
    return datetime(2026, 7, day, hour, 0, tzinfo=TZ)


def in_aug(day, hour=18):
    return datetime(2026, 8, day, hour, 0, tzinfo=TZ)


def requirement(
    requirement_id,
    name,
    *,
    project="米家",
    version="8.0",
    owner_id="ou-project",
    owner_name="项目负责人",
):
    return {
        "record_id": f"rec-{requirement_id}",
        "requirement_id": requirement_id,
        "name": name,
        "project": project,
        "target_version": version,
        "merge_at": in_aug(3),
        "launch_at": in_aug(5),
        "project_owner_id": owner_id,
        "project_owner_name": owner_name,
    }


def node(
    record_id,
    requirement_id,
    name,
    planned_end,
    *,
    owner_id="ou-zhang",
    owner_name="张三",
    domain="服务端",
    status="进行中",
    level=RiskLevel.NORMAL,
    safe_deadline=None,
):
    return {
        "record_id": record_id,
        "requirement_id": requirement_id,
        "name": name,
        "planned_end": planned_end,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "domain": domain,
        "status": status,
        "risk_level": level,
        "safe_deadline": safe_deadline or planned_end,
    }


def risk(requirement_value, level, nodes, **overrides):
    values = {
        "requirement_record_id": requirement_value["record_id"],
        "requirement_id": requirement_value["requirement_id"],
        "requirement_name": requirement_value["name"],
        "project": requirement_value["project"],
        "level": level,
        "predicted_completion": in_aug(6),
        "buffer_days": -1.5 if level == RiskLevel.SEVERE else 3.0,
        "affected_domains": ["服务端"],
        "reasons": ["合板缓冲不足"],
        "actions": ["今天完成联调"],
        "node_risks": [
            {
                "node_record_id": value["record_id"],
                "requirement_id": value["requirement_id"],
                "node_name": value["name"],
                "domain": value["domain"],
                "owner_id": value["owner_id"],
                "owner_name": value["owner_name"],
                "level": value["risk_level"],
                "predicted_completion": value["planned_end"],
                "safe_deadline": value["safe_deadline"],
                "buffer_days": 1.0,
                "reasons": [],
                "actions": [],
            }
            for value in nodes
        ],
    }
    values.update(overrides)
    return values


def assert_no_none(value):
    if isinstance(value, dict):
        for item in value.values():
            assert item is not None
            assert_no_none(item)
    elif isinstance(value, list):
        for item in value:
            assert item is not None
            assert_no_none(item)


def test_mention_escapes_identifier_and_visible_name():
    rendered = mention('ou-"bad', "张<三>&")

    assert rendered == '<at id="ou-&quot;bad">张&lt;三&gt;&amp;</at>'


def test_interactive_card_uses_wide_screen_and_chunks_long_blocks():
    payload = interactive_card(
        title=None,
        template="yellow",
        markdown_blocks=["x" * 7000, None],
    )

    assert payload["msg_type"] == "interactive"
    assert payload["card"]["config"]["wide_screen_mode"] is True
    assert payload["card"]["header"]["title"]["content"] == ""
    contents = [element["text"]["content"] for element in payload["card"]["elements"]]
    assert "".join(contents) == "x" * 7000
    assert all(len(content) <= 3000 for content in contents)
    assert_no_none(payload)


def test_daily_card_groups_by_project_then_owner_and_orders_visible_nodes():
    first = requirement("REQ-1", "账号<迁移>")
    first_nodes = [
        node("late", "REQ-1", "逾期节点", at(23), safe_deadline=at(22)),
        node("today", "REQ-1", "今日节点", at(24), domain="客户端"),
        node(
            "future",
            "REQ-1",
            "普通未来节点",
            at(25),
            domain="车辆",
        ),
        node(
            "warning",
            "REQ-1",
            "预警节点",
            at(28),
            level=RiskLevel.WARNING,
            safe_deadline=at(26),
        ),
        node("parallel", "REQ-1", "并行节点", at(28)),
        node("later", "REQ-1", "八天后节点", in_aug(5)),
        node("done", "REQ-1", "已完成节点", at(24), status="已完成"),
    ]
    second = requirement(
        "REQ-2",
        "固件升级",
        project="车载",
        owner_id="ou-car-owner",
        owner_name="车载负责人",
    )
    second_nodes = [
        node(
            "li",
            "REQ-2",
            "嵌入式开发",
            at(27),
            owner_id="ou-li",
            owner_name="李四",
            domain="嵌入式",
        )
    ]
    report = {
        "started_at": NOW,
        "requirements": [
            {
                "requirement": first,
                "risk": risk(first, RiskLevel.WARNING, first_nodes),
                "nodes": first_nodes,
                "blockers": [
                    {
                        "title": "等待 API & 权限",
                        "owner_id": "ou-blocker",
                        "owner_name": "王五",
                        "status": "处理中",
                    }
                ],
                "enrichment": {
                    "available": False,
                    "failure_reason": "timeout",
                },
            },
            {
                "requirement": second,
                "risk": risk(second, RiskLevel.SEVERE, second_nodes),
                "nodes": second_nodes,
                "blockers": [],
            },
        ],
    }

    payload = build_daily_card(report)
    text = "\n".join(
        element["text"]["content"] for element in payload["card"]["elements"]
    )

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
    assert "目标版本：8.0" in text
    assert "合板：2026-08-03 18:00" in text
    assert "上线：2026-08-05 18:00" in text
    assert "缓冲：3 天" in text
    assert "阻塞：等待 API &amp; 权限" in text
    assert "AI 补充分析不可用，基础规则正常运行" in text
    assert_no_none(payload)


@pytest.mark.parametrize(
    ("level", "template"),
    [
        (RiskLevel.NORMAL, "blue"),
        (RiskLevel.WARNING, "yellow"),
        (RiskLevel.SEVERE, "red"),
    ],
)
def test_daily_card_header_matches_highest_risk(level, template):
    item_requirement = requirement("REQ-3", "风险颜色")
    item_nodes = [node("node-3", "REQ-3", "开发", at(25), level=level)]

    payload = build_daily_card(
        {
            "started_at": NOW,
            "requirements": [
                {
                    "requirement": item_requirement,
                    "risk": risk(item_requirement, level, item_nodes),
                    "nodes": item_nodes,
                }
            ],
        }
    )

    assert payload["card"]["header"]["template"] == template


def test_daily_card_has_no_llm_footer_when_enrichment_was_not_attempted():
    item_requirement = requirement("REQ-4", "不启用 AI")
    item_nodes = [node("node-4", "REQ-4", "开发", at(25))]

    payload = build_daily_card(
        {
            "started_at": NOW,
            "requirements": [
                {
                    "requirement": item_requirement,
                    "risk": risk(item_requirement, RiskLevel.NORMAL, item_nodes),
                    "nodes": item_nodes,
                }
            ],
        }
    )

    assert "AI 补充分析不可用" not in str(payload)


def test_daily_card_can_render_requirement_risk_node_details_without_source_nodes():
    report = {
        "started_at": NOW,
        "risks": [
            {
                "requirement_record_id": "rec-risk-only",
                "requirement_id": "REQ-5",
                "requirement_name": "仅风险结果",
                "project": "米家",
                "level": RiskLevel.WARNING,
                "node_risks": [
                    {
                        "node_record_id": "node-risk-only",
                        "requirement_id": "REQ-5",
                        "node_name": "联调",
                        "domain": "服务端",
                        "owner_id": "ou-risk-owner",
                        "owner_name": "风险负责人",
                        "level": RiskLevel.WARNING,
                        "predicted_completion": at(26),
                        "safe_deadline": at(25),
                    },
                    {
                        "node_record_id": "node-without-date",
                        "requirement_id": "REQ-5",
                        "node_name": "待补日期",
                        "domain": "客户端",
                        "owner_id": "ou-risk-owner",
                        "owner_name": "风险负责人",
                        "level": RiskLevel.WARNING,
                    },
                ],
            }
        ],
    }

    payload = build_daily_card(report)
    text = str(payload)

    assert '<at id="ou-risk-owner">风险负责人</at>' in text
    assert "仅风险结果" in text
    assert "服务端" in text
    assert "联调" in text
    assert "2026-07-26 18:00" in text
    assert "2026-07-25 18:00" in text
    assert "待补日期" in text


def test_severe_card_mentions_project_owner_and_contains_complete_actions():
    item_requirement = requirement("REQ-9", "登陆安全")
    item_nodes = [
        node(
            "node-9",
            "REQ-9",
            "服务端联调",
            in_aug(6),
            safe_deadline=in_aug(2),
        )
    ]
    item_risk = risk(
        item_requirement,
        RiskLevel.SEVERE,
        item_nodes,
        predicted_completion=in_aug(6),
        affected_domains=["服务端", "客户端"],
        reasons=["联调已晚于安全截止"],
        actions=["项目负责人协调资源", "节点负责人今日反馈"],
    )
    payload = build_severe_card(
        {
            "requirement": item_requirement,
            "risk": item_risk,
            "nodes": item_nodes,
            "blockers": [
                {
                    "title": "等待鉴权方案",
                    "owner_id": "ou-blocker",
                    "owner_name": "王五",
                    "status": "处理中",
                    "planned_resolution_at": in_aug(1),
                    "affects_merge": True,
                }
            ],
        }
    )
    text = str(payload)

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


def test_data_error_card_has_all_fields_and_escapes_values():
    issues = [
        {
            "table_name": "进展<节点>表",
            "requirement_id": "REQ-10",
            "record_id": "rec-10",
            "field_name": "计划 DDL",
            "current_value": "tomorrow & later",
            "expected_format": "RFC3339 <datetime>",
            "suggestion": "改为 2026-07-25T18:00:00+08:00",
            "skip_requirement": True,
            "message": "日期格式错误",
        },
        ValidationIssue(
            table_name="需求主表",
            requirement_id=None,
            record_id=None,
            field_name="项目",
            message="不能为空",
        ),
    ]

    payload = build_data_error_card(issues)
    text = str(payload)

    assert payload["card"]["header"]["template"] == "red"
    for label in (
        "表名：进展&lt;节点&gt;表",
        "需求：REQ-10",
        "记录标识：rec-10",
        "错误字段：计划 DDL",
        "当前错误值：tomorrow &amp; later",
        "预期格式：RFC3339 &lt;datetime&gt;",
        "修复建议：改为 2026-07-25T18:00:00+08:00",
        "是否跳过该需求：是",
        "错误说明：日期格式错误",
    ):
        assert label in text
    assert "当前错误值：未提供" in text
    assert "是否跳过该需求：否" in text
    assert_no_none(payload)


def test_plain_text_fallback_removes_none_values():
    payload = build_plain_text_fallback(None, ["第一行", None, 42])

    assert payload == {
        "msg_type": "text",
        "content": {"text": "\n第一行\n\n42"},
    }
    assert_no_none(payload)
