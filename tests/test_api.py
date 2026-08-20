from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from requirement_monitor.api import create_app
from requirement_monitor.database import NotificationOutboxRow, ScheduledJobRow, session_scope
from requirement_monitor.models import SendResult


def test_full_configuration_and_requirement_flow(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'api.db'}"))
    assert client.get("/api/health").json()["status"] == "ok"

    person = client.post("/api/people", json={"display_name": "林夏", "role_name": "负责人"})
    assert person.status_code == 201
    domain = client.post("/api/domains", json={"name": "研发", "color": "#24704b"})
    assert domain.status_code == 201
    first = client.post("/api/node-definitions", json={"name": "开发", "domain_id": domain.json()["id"]})
    second = client.post("/api/node-definitions", json={"name": "测试", "domain_id": domain.json()["id"]})
    template = client.post("/api/templates", json={"name": "标准流程"})
    template_id = template.json()["id"]
    node_a = client.post(f"/api/templates/{template_id}/nodes", json={"definition_id": first.json()["id"], "position_x": 10, "position_y": 20})
    node_b = client.post(f"/api/templates/{template_id}/nodes", json={"definition_id": second.json()["id"], "position_x": 300, "position_y": 20})
    edge = client.post(f"/api/templates/{template_id}/edges", json={"source": node_a.json()["id"], "target": node_b.json()["id"]})
    assert edge.status_code == 201
    cycle = client.post(f"/api/templates/{template_id}/edges", json={"source": node_b.json()["id"], "target": node_a.json()["id"]})
    assert cycle.status_code == 400
    assert "cycle" in cycle.json()["detail"]

    created = client.post("/api/requirements", json={"name": "体验升级", "owner_id": person.json()["id"], "template_id": template_id, "meego_url": "https://project.meego.cn/story/1", "requirement_url": "https://docs.example.com/requirement/1", "figma_url": "https://www.figma.com/design/example"})
    assert created.status_code == 201
    requirement = created.json()
    assert requirement["sequence_id"] == 1
    assert "requirement_key" not in requirement
    assert len(requirement["nodes"]) == 2
    assert len(requirement["edges"]) == 1
    assert requirement["meego_url"] == "https://project.meego.cn/story/1"
    assert requirement["requirement_url"] == "https://docs.example.com/requirement/1"
    assert requirement["figma_url"] == "https://www.figma.com/design/example"
    target = next(n for n in requirement["nodes"] if n["name"] == "测试")
    updated = client.patch(f"/api/requirement-nodes/{target['id']}", json={"planned_start": "2026-08-22T09:00:00+08:00", "planned_end": "2026-08-22T18:00:00+08:00"})
    assert updated.status_code == 200
    assert updated.json()["planned_start"] == "2026-08-22T01:00:00+00:00"
    assert updated.json()["planned_end"] == "2026-08-22T10:00:00+00:00"
    detail = client.get(f"/api/requirements/{requirement['id']}").json()
    assert detail["risk_level"] == 1
    assert any("前置节点" in reason and "尚未设置计划结束时间" in reason for reason in detail["risk_reasons"])


def test_removed_project_and_blocker_routes_do_not_exist(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'removed.db'}"))
    assert client.get("/api/projects").status_code == 404
    assert client.get("/api/blockers").status_code == 404
    assert client.get("/api/nodes").status_code == 404


def test_requirement_graph_can_add_move_connect_and_delete_nodes(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'requirement-graph.db'}"))
    owner = client.post("/api/people", json={"display_name": "林夏"}).json()
    domain = client.post("/api/domains", json={"name": "研发", "color": "#24704b"}).json()
    first = client.post("/api/node-definitions", json={"name": "开发", "domain_id": domain["id"]}).json()
    second = client.post("/api/node-definitions", json={"name": "测试", "domain_id": domain["id"]}).json()
    template = client.post("/api/templates", json={"name": "可编辑流程"}).json()
    client.post(f"/api/templates/{template['id']}/nodes", json={"definition_id": first["id"]})
    requirement = client.post("/api/requirements", json={"name": "流程调整需求", "owner_id": owner["id"], "template_id": template["id"]}).json()
    first_node = requirement["nodes"][0]

    added = client.post(
        f"/api/requirements/{requirement['id']}/nodes",
        json={"definition_id": second["id"], "domain_id": domain["id"], "position_x": 320, "position_y": 80},
    )
    assert added.status_code == 201
    second_node = added.json()
    assert second_node["name"] == "测试"
    assert second_node["domain_name"] == "研发"

    moved = client.patch(f"/api/requirement-nodes/{second_node['id']}", json={"position_x": 480, "position_y": 160})
    assert moved.status_code == 200
    assert moved.json()["position"] == {"x": 480.0, "y": 160.0}

    edge = client.post(f"/api/requirements/{requirement['id']}/edges", json={"source": first_node["id"], "target": second_node["id"]})
    assert edge.status_code == 201
    cycle = client.post(f"/api/requirements/{requirement['id']}/edges", json={"source": second_node["id"], "target": first_node["id"]})
    assert cycle.status_code == 400
    assert "cycle" in cycle.json()["detail"]

    detail = client.get(f"/api/requirements/{requirement['id']}").json()
    assert len(detail["nodes"]) == 2
    assert len(detail["edges"]) == 1

    assert client.delete(f"/api/requirement-edges/{edge.json()['id']}").status_code == 200
    assert client.delete(f"/api/requirement-nodes/{second_node['id']}").status_code == 200
    detail = client.get(f"/api/requirements/{requirement['id']}").json()
    assert len(detail["nodes"]) == 1
    assert detail["edges"] == []


def test_requirement_graph_supports_batch_move_and_delete(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'requirement-graph-batch.db'}"))
    owner = client.post("/api/people", json={"display_name": "林夏"}).json()
    domain = client.post("/api/domains", json={"name": "研发", "color": "#24704b"}).json()
    definitions = [
        client.post("/api/node-definitions", json={"name": name, "domain_id": domain["id"]}).json()
        for name in ("开发", "联调", "测试")
    ]
    template = client.post("/api/templates", json={"name": "批量编辑流程"}).json()
    template_nodes = [
        client.post(
            f"/api/templates/{template['id']}/nodes",
            json={"definition_id": definition["id"], "position_x": index * 220, "position_y": 40},
        ).json()
        for index, definition in enumerate(definitions)
    ]
    client.post(f"/api/templates/{template['id']}/edges", json={"source": template_nodes[0]["id"], "target": template_nodes[1]["id"]})
    client.post(f"/api/templates/{template['id']}/edges", json={"source": template_nodes[1]["id"], "target": template_nodes[2]["id"]})
    requirement = client.post("/api/requirements", json={"name": "批量画板需求", "owner_id": owner["id"], "template_id": template["id"]}).json()
    node_ids = [node["id"] for node in requirement["nodes"]]
    original_positions = {node["id"]: node["position"] for node in requirement["nodes"]}

    rejected_move = client.post(
        "/api/requirement-nodes/batch-positions",
        json={"nodes": [{"id": node_ids[0], "position_x": 999, "position_y": 999}, {"id": "missing", "position_x": 1, "position_y": 1}]},
    )
    assert rejected_move.status_code == 400
    detail = client.get(f"/api/requirements/{requirement['id']}").json()
    assert {node["id"]: node["position"] for node in detail["nodes"]} == original_positions

    moved = client.post(
        "/api/requirement-nodes/batch-positions",
        json={"nodes": [{"id": node_ids[0], "position_x": 120, "position_y": 160}, {"id": node_ids[1], "position_x": 360, "position_y": 160}]},
    )
    assert moved.status_code == 200
    assert moved.json()["count"] == 2
    detail = client.get(f"/api/requirements/{requirement['id']}").json()
    positions = {node["id"]: node["position"] for node in detail["nodes"]}
    assert positions[node_ids[0]] == {"x": 120.0, "y": 160.0}
    assert positions[node_ids[1]] == {"x": 360.0, "y": 160.0}

    rejected_delete = client.post("/api/requirement-nodes/batch-delete", json={"node_ids": [node_ids[0], "missing"]})
    assert rejected_delete.status_code == 400
    assert len(client.get(f"/api/requirements/{requirement['id']}").json()["nodes"]) == 3

    deleted = client.post("/api/requirement-nodes/batch-delete", json={"node_ids": node_ids[:2]})
    assert deleted.status_code == 200
    assert deleted.json()["count"] == 2
    detail = client.get(f"/api/requirements/{requirement['id']}").json()
    assert [node["id"] for node in detail["nodes"]] == [node_ids[2]]
    assert detail["edges"] == []
    assert detail["total_nodes"] == 1


def test_requirement_ai_summary_can_be_cleared_or_disabled(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'requirement-ai-toggle.db'}"))
    owner = client.post("/api/people", json={"display_name": "林夏"}).json()
    domain = client.post("/api/domains", json={"name": "研发"}).json()
    definition = client.post("/api/node-definitions", json={"name": "开发", "domain_id": domain["id"]}).json()
    template = client.post("/api/templates", json={"name": "AI 开关模板"}).json()
    client.post(f"/api/templates/{template['id']}/nodes", json={"definition_id": definition["id"]})
    requirement = client.post("/api/requirements", json={"name": "AI 开关需求", "owner_id": owner["id"], "template_id": template["id"]}).json()

    from requirement_monitor.database import RequirementRow, session_scope

    with session_scope(f"sqlite+pysqlite:///{tmp_path / 'requirement-ai-toggle.db'}") as session:
        row = session.get(RequirementRow, requirement["id"])
        row.ai_analysis = {"risk_level": "warning", "summary": "待处理", "confidence": 0.8}
        row.ai_analyzed_at = datetime.now(timezone.utc)
        row.ai_input_hash = "fingerprint"
        row.ai_error = "old error"

    cleared = client.delete(f"/api/requirements/{requirement['id']}/ai-analysis")
    assert cleared.status_code == 200
    assert cleared.json()["ai_analysis"] is None
    assert cleared.json()["ai_enabled"] is True

    with session_scope(f"sqlite+pysqlite:///{tmp_path / 'requirement-ai-toggle.db'}") as session:
        row = session.get(RequirementRow, requirement["id"])
        row.ai_analysis = {"risk_level": "warning", "summary": "待关闭", "confidence": 0.8}
        row.ai_input_hash = "fingerprint"
        row.ai_error = "old error"

    disabled = client.patch(f"/api/requirements/{requirement['id']}", json={"ai_enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["ai_enabled"] is False
    assert disabled.json()["ai_analysis"] is None
    assert disabled.json()["ai_error"] is None
    assert client.post(f"/api/requirements/{requirement['id']}/ai-analysis").status_code == 400

    enabled = client.patch(f"/api/requirements/{requirement['id']}", json={"ai_enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["ai_enabled"] is True


def test_requirement_nodes_support_batch_status_updates(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'batch-status.db'}"))
    owner = client.post("/api/people", json={"display_name": "林夏"}).json()
    domain = client.post("/api/domains", json={"name": "研发"}).json()
    definition_a = client.post("/api/node-definitions", json={"name": "开发", "domain_id": domain["id"]}).json()
    definition_b = client.post("/api/node-definitions", json={"name": "测试", "domain_id": domain["id"]}).json()
    template = client.post("/api/templates", json={"name": "批量状态模板"}).json()
    node_a = client.post(f"/api/templates/{template['id']}/nodes", json={"definition_id": definition_a["id"]}).json()
    node_b = client.post(f"/api/templates/{template['id']}/nodes", json={"definition_id": definition_b["id"]}).json()
    requirement = client.post("/api/requirements", json={"name": "批量状态需求", "owner_id": owner["id"], "template_id": template["id"]}).json()
    node_ids = [node["id"] for node in requirement["nodes"]]
    response = client.post("/api/requirement-nodes/batch-status", json={"node_ids": node_ids, "status": "skipped"})
    assert response.status_code == 200
    assert response.json()["count"] == 2
    detail = client.get(f"/api/requirements/{requirement['id']}").json()
    assert {node["status"] for node in detail["nodes"]} == {"skipped"}


def test_requirement_nodes_support_batch_owner_updates(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'batch-owners.db'}"))
    owner = client.post("/api/people", json={"display_name": "林夏"}).json()
    second_owner = client.post("/api/people", json={"display_name": "沈言"}).json()
    domain = client.post("/api/domains", json={"name": "研发"}).json()
    definition_a = client.post("/api/node-definitions", json={"name": "开发", "domain_id": domain["id"]}).json()
    definition_b = client.post("/api/node-definitions", json={"name": "测试", "domain_id": domain["id"]}).json()
    template = client.post("/api/templates", json={"name": "批量负责人模板"}).json()
    client.post(f"/api/templates/{template['id']}/nodes", json={"definition_id": definition_a["id"]})
    client.post(f"/api/templates/{template['id']}/nodes", json={"definition_id": definition_b["id"]})
    requirement = client.post("/api/requirements", json={"name": "批量负责人需求", "owner_id": owner["id"], "template_id": template["id"]}).json()
    node_ids = [node["id"] for node in requirement["nodes"]]
    response = client.post("/api/requirement-nodes/batch-owners", json={"node_ids": node_ids, "owner_ids": [second_owner["id"]], "mode": "replace"})
    assert response.status_code == 200
    detail = client.get(f"/api/requirements/{requirement['id']}").json()
    assert all([person["id"] for person in node["owners"]] == [second_owner["id"]] for node in detail["nodes"])


def test_node_definition_supports_multiple_domains(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'multi-domain.db'}"))
    product = client.post("/api/domains", json={"name": "产品", "color": "#7c5ce5"}).json()
    client_domain = client.post("/api/domains", json={"name": "客户端", "color": "#3478c7"}).json()
    definition = client.post(
        "/api/node-definitions",
        json={"name": "体验验收", "domain_ids": [product["id"], client_domain["id"]]},
    )
    assert definition.status_code == 201
    payload = definition.json()
    assert payload["domain_id"] == product["id"]
    assert payload["domain_ids"] == [product["id"], client_domain["id"]]
    assert [domain["name"] for domain in payload["domains"]] == ["产品", "客户端"]


def test_multi_domain_definition_creates_one_template_and_requirement_node_per_domain(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'multi-domain-template.db'}"))
    product = client.post("/api/domains", json={"name": "产品"}).json()
    client_domain = client.post("/api/domains", json={"name": "客户端"}).json()
    owner = client.post("/api/people", json={"display_name": "林夏", "role_name": "负责人"}).json()
    definition = client.post(
        "/api/node-definitions",
        json={"name": "体验验收", "domain_ids": [product["id"], client_domain["id"]]},
    ).json()
    template = client.post("/api/templates", json={"name": "多领域流程"}).json()

    product_node = client.post(
        f"/api/templates/{template['id']}/nodes",
        json={"definition_id": definition["id"], "domain_id": product["id"]},
    )
    client_node = client.post(
        f"/api/templates/{template['id']}/nodes",
        json={"definition_id": definition["id"], "domain_id": client_domain["id"]},
    )

    assert product_node.status_code == 201
    assert client_node.status_code == 201
    assert product_node.json()["domain_id"] == product["id"]
    assert client_node.json()["domain_id"] == client_domain["id"]

    template_detail = client.get(f"/api/templates/{template['id']}").json()
    assert len(template_detail["nodes"]) == 2
    assert {node["domain"]["name"] for node in template_detail["nodes"]} == {"产品", "客户端"}

    requirement = client.post(
        "/api/requirements",
        json={"name": "多领域需求", "owner_id": owner["id"], "template_id": template["id"]},
    )
    assert requirement.status_code == 201
    assert len(requirement.json()["nodes"]) == 2
    assert {node["domain_name"] for node in requirement.json()["nodes"]} == {"产品", "客户端"}


def test_requirement_can_be_edited_and_archived(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'requirement-edit.db'}"))
    domain = client.post("/api/domains", json={"name": "产品"}).json()
    owner = client.post("/api/people", json={"display_name": "林夏", "role_name": "负责人"}).json()
    replacement_owner = client.post("/api/people", json={"display_name": "周屿", "role_name": "交付负责人"}).json()
    definition = client.post("/api/node-definitions", json={"name": "评审", "domain_id": domain["id"]}).json()
    template = client.post("/api/templates", json={"name": "编辑模板"}).json()
    client.post(f"/api/templates/{template['id']}/nodes", json={"definition_id": definition["id"]})
    requirement = client.post("/api/requirements", json={"name": "原始需求", "owner_id": owner["id"], "template_id": template["id"]}).json()

    updated = client.patch(
        f"/api/requirements/{requirement['id']}",
        json={"name": "更新后的需求", "owner_id": replacement_owner["id"], "target_version": "v2.0", "archived": True},
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "更新后的需求"
    assert updated.json()["owner_id"] == replacement_owner["id"]
    assert updated.json()["target_version"] == "v2.0"
    assert updated.json()["archived"] is True

    restored = client.patch(f"/api/requirements/{requirement['id']}", json={"archived": False})
    assert restored.status_code == 200
    assert restored.json()["archived"] is False


def test_manual_notification_run_refreshes_generates_and_delivers(monkeypatch, tmp_path: Path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'manual-notification.db'}"
    client = TestClient(create_app(database_url))
    domain = client.post("/api/domains", json={"name": "产品"}).json()
    owner = client.post("/api/people", json={"display_name": "林夏"}).json()
    definition = client.post("/api/node-definitions", json={"name": "评审", "domain_id": domain["id"]}).json()
    template = client.post("/api/templates", json={"name": "通知模板"}).json()
    client.post(f"/api/templates/{template['id']}/nodes", json={"definition_id": definition["id"]})
    client.post("/api/requirements", json={"name": "待投递需求", "owner_id": owner["id"], "template_id": template["id"]})
    job = client.post("/api/jobs", json={"name": "通知投递", "job_type": "outbox_delivery", "notification_scope": "all", "schedule_kind": "cron", "cron_expression": "42 18 * * 1-5", "timezone": "Asia/Shanghai"}).json()
    scheduled_next_run = job["next_run_at"]
    settings = client.patch("/api/webhook-settings", json={"enabled": True, "test_webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test-token"})
    assert settings.status_code == 200

    class FakeSender:
        def __init__(self, *_args, **_kwargs):
            pass

        def send(self, _payload):
            return SendResult(success=True, attempts=1, status_code=200, feishu_code=0)

        def close(self):
            pass

    import requirement_monitor.worker as worker

    monkeypatch.setattr(worker, "WebhookSender", FakeSender)
    response = client.post(f"/api/jobs/{job['id']}/run", json={})

    assert response.status_code == 200
    assert response.json()["summary"]["refreshed"] == 1
    assert response.json()["summary"]["enqueued"] == 1
    assert response.json()["summary"]["delivered"] == 1
    assert client.get("/api/notifications").json()[0]["status"] == "sent"
    refreshed_job = next(item for item in client.get("/api/jobs").json() if item["id"] == job["id"])
    assert refreshed_job["next_run_at"] == scheduled_next_run


def test_deployment_update_is_enabled_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("REQUIREMENT_MONITOR_DEPLOY_UPDATE_ENABLED", raising=False)
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'deploy.db'}"))
    status = client.get("/api/deployment/update-status")
    assert status.status_code == 200
    assert status.json()["enabled"] is True


def test_trigger_outbox_delivery_makes_pending_notifications_due(tmp_path: Path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'outbox-trigger.db'}"
    client = TestClient(create_app(database_url))
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    with session_scope(database_url) as session:
        job = ScheduledJobRow(name="通知投递", job_type="outbox_delivery", interval_seconds=30, next_run_at=future)
        notification = NotificationOutboxRow(deduplication_key="manual-retry", payload={"msg_type": "text", "content": {"text": "hello"}}, available_at=future)
        session.add_all([job, notification])
        session.flush()
        job_id = job.id
        notification_id = notification.id

    from requirement_monitor.service import trigger_job

    assert trigger_job(database_url, job_id) is not None
    with session_scope(database_url) as session:
        notification = session.get(NotificationOutboxRow, notification_id)
        assert notification is not None
        assert notification.available_at.replace(tzinfo=timezone.utc) < future


def test_trigger_outbox_delivery_revives_dead_notifications(tmp_path: Path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'outbox-dead-trigger.db'}"
    client = TestClient(create_app(database_url))
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    with session_scope(database_url) as session:
        job = ScheduledJobRow(name="通知投递", job_type="outbox_delivery", interval_seconds=30, next_run_at=future)
        notification = NotificationOutboxRow(deduplication_key="manual-revive", payload={"msg_type": "text", "content": {"text": "hello"}}, status="dead", attempt_count=6, last_error="feishu_error_19024", available_at=future)
        session.add_all([job, notification])
        session.flush()
        job_id = job.id
        notification_id = notification.id

    from requirement_monitor.service import trigger_job

    assert trigger_job(database_url, job_id) is not None
    with session_scope(database_url) as session:
        notification = session.get(NotificationOutboxRow, notification_id)
        assert notification is not None
        assert notification.status == "pending"
        assert notification.attempt_count == 0
        assert notification.last_error is None
        assert notification.available_at.replace(tzinfo=timezone.utc) < future


def test_job_timer_can_be_updated(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'jobs.db'}"))
    created = client.post(
        "/api/jobs",
        json={"name": "AI 自动总结", "job_type": "ai_analysis", "interval_seconds": 3600},
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    updated = client.patch(
        f"/api/jobs/{job_id}",
        json={
            "name": "AI 总结",
            "interval_seconds": 7200,
            "enabled": False,
            "next_run_at": "2026-08-20T09:30:00+08:00",
        },
    )

    assert updated.status_code == 200
    payload = updated.json()
    assert payload["name"] == "AI 总结"
    assert payload["job_type"] == "ai_analysis"
    assert payload["interval_seconds"] == 7200
    assert payload["enabled"] is False
    assert payload["next_run_at"] == "2026-08-20T01:30:00+00:00"

    cron = client.patch(
        f"/api/jobs/{job_id}",
        json={
            "schedule_kind": "cron",
            "cron_expression": "0 10 * * 1-5",
            "timezone": "Asia/Shanghai",
            "next_run_at": None,
            "enabled": True,
        },
    )
    assert cron.status_code == 200
    payload = cron.json()
    assert payload["schedule_kind"] == "cron"
    assert payload["cron_expression"] == "0 10 * * 1-5"
    assert payload["timezone"] == "Asia/Shanghai"
    assert payload["next_run_at"] is not None
    assert payload["next_run_at"].endswith("+00:00")

    overridden = client.patch(
        f"/api/jobs/{job_id}",
        json={"next_run_at": "2026-08-19T08:29:00+00:00"},
    )
    assert overridden.status_code == 200
    assert overridden.json()["next_run_at"] != "2026-08-19T08:29:00+00:00"


def test_person_domain_open_id_and_masked_webhook_settings(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'settings.db'}"))
    domain = client.post("/api/domains", json={"name": "服务端", "color": "#248052"}).json()
    person = client.post("/api/people", json={"display_name": "沈言", "role_name": "研发", "domain_id": domain["id"], "feishu_open_id": "ou_test_person"})
    assert person.status_code == 201
    assert person.json()["domain"]["name"] == "服务端"
    assert person.json()["feishu_open_id"] == "ou_test_person"

    url = "https://open.feishu.cn/open-apis/bot/v2/hook/secret-webhook-token"
    saved = client.patch("/api/webhook-settings", json={"enabled": True, "runtime_environment": "test", "test_webhook_url": url, "bot_keyword": "交付提醒"})
    assert saved.status_code == 200
    payload = saved.json()
    assert payload["test_configured"] is True
    assert payload["test_webhook_url"].endswith("-token")
    assert "secret-webhook" not in payload["test_webhook_url"]
    assert url not in client.get("/api/webhook-settings").text


def test_webhook_rejects_non_official_url(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'invalid-webhook.db'}"))
    response = client.patch("/api/webhook-settings", json={"test_webhook_url": "https://example.com/hook/secret"})
    assert response.status_code == 400


def test_ai_settings_are_disabled_by_default_and_mask_api_key(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'ai-settings.db'}"))
    defaults = client.get("/api/ai-settings")
    assert defaults.status_code == 200
    assert defaults.json()["enabled"] is False
    assert defaults.json()["provider"] == "chatgpt_plus"
    assert "项目交付风险" in defaults.json()["prompt"]

    saved = client.patch("/api/ai-settings", json={"provider": "openai_compatible", "base_url": "https://ai.example.com/v1", "api_key": "very-secret-key", "model": "risk-model", "enabled": True})
    assert saved.status_code == 200
    assert saved.json()["api_key_configured"] is True
    assert saved.json()["api_key"].endswith("-key")
    assert "very-secret" not in saved.text
    assert "very-secret-key" not in client.get("/api/ai-settings").text


def test_requirement_links_are_optional_and_reject_unsafe_schemes(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'links.db'}"))
    person = client.post("/api/people", json={"display_name": "负责人"}).json()
    domain = client.post("/api/domains", json={"name": "产品"}).json()
    definition = client.post("/api/node-definitions", json={"name": "评审", "domain_id": domain["id"]}).json()
    template = client.post("/api/templates", json={"name": "评审模板"}).json()
    client.post(f"/api/templates/{template['id']}/nodes", json={"definition_id": definition["id"]})
    base = {"name": "链接测试", "owner_id": person["id"], "template_id": template["id"]}
    assert client.post("/api/requirements", json=base).status_code == 201
    unsafe = {**base, "figma_url": "javascript:alert(1)"}
    assert client.post("/api/requirements", json=unsafe).status_code == 422


def test_requirement_ids_increment_without_user_number(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'sequence.db'}"))
    person = client.post("/api/people", json={"display_name": "负责人"}).json()
    domain = client.post("/api/domains", json={"name": "产品"}).json()
    definition = client.post("/api/node-definitions", json={"name": "评审", "domain_id": domain["id"]}).json()
    template = client.post("/api/templates", json={"name": "流程"}).json()
    client.post(f"/api/templates/{template['id']}/nodes", json={"definition_id": definition["id"]})
    payload = {"name": "第一个需求", "owner_id": person["id"], "template_id": template["id"]}
    first = client.post("/api/requirements", json=payload)
    second = client.post("/api/requirements", json={**payload, "name": "第二个需求"})
    assert first.status_code == second.status_code == 201
    assert [first.json()["sequence_id"], second.json()["sequence_id"]] == [1, 2]


def test_admin_export_clear_and_import_roundtrip(tmp_path: Path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'backup.db'}"
    client = TestClient(create_app(database_url))
    person = client.post("/api/people", json={"display_name": "负责人"}).json()
    domain = client.post("/api/domains", json={"name": "产品"}).json()
    definition = client.post("/api/node-definitions", json={"name": "评审", "domain_id": domain["id"]}).json()
    template = client.post("/api/templates", json={"name": "备份流程"}).json()
    client.post(f"/api/templates/{template['id']}/nodes", json={"definition_id": definition["id"]})
    created = client.post("/api/requirements", json={"name": "备份测试", "owner_id": person["id"], "template_id": template["id"]})
    assert created.status_code == 201
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/backup-token"
    client.patch("/api/webhook-settings", json={"enabled": True, "test_webhook_url": webhook})

    exported = client.get("/api/admin/export")
    assert exported.status_code == 200
    assert "pm-project-monitor-backup" in exported.headers["content-disposition"]
    backup = exported.json()
    assert backup["format"] == "pm-project-monitor.backup"
    assert backup["tables"]["requirements"][0]["name"] == "备份测试"
    assert backup["tables"]["webhook_settings"][0]["test_webhook_url"] == webhook
    backup["tables"]["job_runs"] = [{
        "id": "orphaned-run",
        "job_id": "job-from-another-backup",
        "run_key": "job-from-another-backup:2026-08-19T09:00:00+00:00",
        "status": "succeeded",
        "started_at": "2026-08-19T09:00:00+00:00",
        "finished_at": "2026-08-19T09:00:01+00:00",
        "result_summary": {},
        "error_message": None,
    }]

    cleared = client.post("/api/admin/clear", json={"preserve_settings": True})
    assert cleared.status_code == 200
    assert client.get("/api/requirements").json() == []
    assert client.get("/api/webhook-settings").json()["test_configured"] is True

    imported = client.post("/api/admin/import", json={"backup": backup, "preserve_settings": True})
    assert imported.status_code == 200
    assert imported.json()["imported"]["job_runs"] == 0
    restored = client.get("/api/requirements").json()
    assert len(restored) == 1
    assert restored[0]["name"] == "备份测试"
