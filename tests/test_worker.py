from pathlib import Path

from requirement_monitor.database import initialize_database
from datetime import datetime, timedelta, timezone

from requirement_monitor.service import create_domain, create_person, create_definition, create_template, add_template_node, create_requirement, get_requirement, list_jobs, list_notifications, update_requirement, update_requirement_node
from requirement_monitor.worker import _enqueue_notifications, _risk_card, worker_loop


def test_risk_card_mentions_risky_node_owners_and_adds_link_buttons():
    payload = _risk_card(
        {
            "sequence_id": 7,
            "name": "支付链路改造",
            "risk_level": 2,
            "risk_reasons": ["开发节点逾期", "测试节点前置未完成"],
            "current_nodes": ["开发", "测试"],
            "current_node_ids": ["node-dev", "node-test"],
            "meego_url": "https://project.meego.cn/story/7",
            "figma_url": "https://www.figma.com/design/example",
            "requirement_url": "https://docs.example.com/requirement/7",
            "nodes": [
                {
                    "id": "node-dev",
                    "name": "开发",
                    "domain_name": "服务端",
                    "risk_level": 2,
                    "risk_reasons": ["开发节点已逾期"],
                    "owners": [
                        {"display_name": "沈言", "feishu_open_id": "ou_backend"}
                    ],
                },
                {
                    "id": "node-test",
                    "name": "测试",
                    "domain_name": "质量",
                    "risk_level": 1,
                    "owners": [{"display_name": "陈默", "feishu_open_id": ""}],
                },
                {
                    "id": "node-design",
                    "name": "视觉",
                    "domain_name": "设计",
                    "status": "completed",
                    "risk_level": 0,
                    "owners": [
                        {"display_name": "周屿", "feishu_open_id": "ou_client"}
                    ],
                },
            ],
        }
    )

    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["title"]["content"] == "严重 · 支付链路改造"
    content = payload["card"]["elements"][0]["content"]
    assert "- **开发 · 服务端** · 负责人：<at id=ou_backend>沈言</at>" in content
    assert "风险：已逾期" in content
    assert "- **测试 · 质量** · 负责人：@陈默" in content
    assert "<at id=ou_backend>沈言</at>" in content
    assert "@陈默" in content
    assert "周屿" not in content
    actions = payload["card"]["elements"][1]["actions"]
    assert [item["text"]["content"] for item in actions] == [
        "Meego",
        "Figma",
        "需求文档",
    ]
    assert [item["url"] for item in actions] == [
        "https://project.meego.cn/story/7",
        "https://www.figma.com/design/example",
        "https://docs.example.com/requirement/7",
    ]


def test_risk_card_is_compact_and_lists_missing_schedule_with_multiple_owners():
    payload = _risk_card(
        {
            "sequence_id": 8,
            "name": "会员中心改造",
            "risk_level": 1,
            "risk_reasons": ["缺少计划时间", "其他风险", "不要展示的长风险"],
            "current_node_ids": ["node-a"],
            "edges": [{"source": "node-a", "target": "node-b"}],
            "nodes": [
                {
                    "id": "node-a",
                    "name": "开发",
                    "domain_name": "服务端",
                    "status": "in_progress",
                    "risk_level": 1,
                    "planned_start": "2026-08-19T09:00:00+08:00",
                    "planned_end": "2026-08-20T18:00:00+08:00",
                    "owners": [
                        {"display_name": "张三", "feishu_open_id": "ou_zhang"},
                        {"display_name": "李四", "feishu_open_id": ""},
                    ],
                },
                {
                    "id": "node-b",
                    "name": "测试",
                    "domain_name": "质量",
                    "status": "not_started",
                    "risk_level": 1,
                    "planned_start": None,
                    "planned_end": None,
                    "owners": [{"display_name": "王五", "feishu_open_id": "ou_wang"}],
                },
            ],
        }
    )
    content = payload["card"]["elements"][0]["content"]
    assert "**开发 · 服务端** · 负责人：<at id=ou_zhang>张三</at> @李四" in content
    assert "**待补计划**\n- 测试 · 质量 · <at id=ou_wang>王五</at>" in content
    assert "不要展示的长风险" not in content


def test_risk_card_compacts_node_reasons_and_separates_ai_section():
    payload = _risk_card(
        {
            "sequence_id": 9,
            "name": "快捷跳转",
            "risk_level": 1,
            "risk_reasons": [],
            "current_node_ids": [],
            "nodes": [
                {
                    "id": "pv",
                    "name": "PV测试",
                    "domain_name": "客户端",
                    "status": "not_started",
                    "risk_level": 1,
                    "risk_reasons": [
                        "“PV测试”尚未设置计划开始和结束时间",
                        "后续节点“合版”已有开始时间，但前置节点“PV测试”尚未设置计划结束时间",
                        "重复且不应展示",
                    ],
                    "owners": [],
                }
            ],
            "ai_analysis": {
                "summary": "当前主要风险是前置计划缺失。",
                "actions": [{"action": "补齐 PV 测试排期"}],
            },
        }
    )

    elements = payload["card"]["elements"]
    assert "**PV测试 · 客户端**：未设置计划时间；影响「合版」启动" in elements[0]["content"]
    assert "尚未设置计划开始和结束时间" not in elements[0]["content"]
    assert elements[1] == {"tag": "hr"}
    assert "**AI 风险总结**" in elements[2]["content"]
    assert "###" not in elements[2]["content"]
    assert "**结论**" in elements[2]["content"]
    assert "**建议动作**" in elements[2]["content"]


def test_worker_creates_default_automation_jobs(tmp_path: Path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'worker-jobs.db'}"
    initialize_database(database_url)

    worker_loop(database_url, tmp_path / "missing-config.json", once=True)

    job_types = {job["job_type"] for job in list_jobs(database_url)}
    assert {"risk_scan", "outbox_delivery", "ai_analysis"} <= job_types


def test_notification_scope_controls_generated_cards_without_fingerprint_dedup(tmp_path: Path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'notification-scope.db'}"
    initialize_database(database_url)
    domain = create_domain(database_url, {"name": "产品"})
    owner = create_person(database_url, {"display_name": "林夏"})
    definition = create_definition(database_url, {"name": "评审", "domain_id": domain["id"]})
    template = create_template(database_url, {"name": "通知范围模板"})
    add_template_node(database_url, template["id"], {"definition_id": definition["id"]})
    requirement = create_requirement(database_url, {"name": "普通需求", "owner_id": owner["id"], "template_id": template["id"]})
    node_id = requirement["nodes"][0]["id"]
    now = datetime.now(timezone.utc)
    update_requirement_node(database_url, node_id, {"planned_start": now + timedelta(days=1), "planned_end": now + timedelta(days=2)})

    assert _enqueue_notifications(database_url, "risk_only") == 0
    assert _enqueue_notifications(database_url, "all") == 1
    assert _enqueue_notifications(database_url, "all") == 1
    assert len(list_notifications(database_url)) == 2


def test_planned_and_archived_requirements_are_excluded_from_notifications(tmp_path: Path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'notification-lifecycle.db'}"
    initialize_database(database_url)
    domain = create_domain(database_url, {"name": "产品"})
    owner = create_person(database_url, {"display_name": "林夏"})
    definition = create_definition(database_url, {"name": "评审", "domain_id": domain["id"]})
    template = create_template(database_url, {"name": "状态通知模板"})
    add_template_node(database_url, template["id"], {"definition_id": definition["id"]})
    active = create_requirement(database_url, {"name": "进行中需求", "owner_id": owner["id"], "template_id": template["id"]})
    create_requirement(database_url, {"name": "计划需求", "owner_id": owner["id"], "template_id": template["id"], "lifecycle_status": "planned"})
    create_requirement(database_url, {"name": "归档需求", "owner_id": owner["id"], "template_id": template["id"], "lifecycle_status": "archived"})

    assert _enqueue_notifications(database_url, "all") == 1
    pending = list_notifications(database_url)
    assert len(pending) == 1
    update_requirement(database_url, active["id"], {"lifecycle_status": "planned"})
    assert list_notifications(database_url)[0]["status"] == "canceled"
    assert _enqueue_notifications(database_url, "all") == 0
