from pathlib import Path

from requirement_monitor.database import initialize_database
from datetime import datetime, timedelta, timezone

from requirement_monitor.service import create_domain, create_person, create_definition, create_template, add_template_node, create_requirement, get_requirement, list_jobs, list_notifications, update_requirement_node
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
    assert "- 当前环节：开发 · 域：服务端 · 负责人：<at id=ou_backend>沈言</at>" in content
    assert "- 当前环节：测试 · 域：质量 · 负责人：@陈默" in content
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


def test_worker_creates_default_automation_jobs(tmp_path: Path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'worker-jobs.db'}"
    initialize_database(database_url)

    worker_loop(database_url, tmp_path / "missing-config.json", once=True)

    job_types = {job["job_type"] for job in list_jobs(database_url)}
    assert {"risk_scan", "outbox_delivery", "ai_analysis"} <= job_types


def test_notification_scope_controls_generated_cards(tmp_path: Path):
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
    assert _enqueue_notifications(database_url, "all", force=True) == 1
    assert len(list_notifications(database_url)) == 1
