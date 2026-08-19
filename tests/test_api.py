from pathlib import Path

from fastapi.testclient import TestClient

from requirement_monitor.api import create_app


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
    detail = client.get(f"/api/requirements/{requirement['id']}").json()
    assert detail["risk_level"] == 1
    assert any("前置节点" in reason and "尚未设置计划结束时间" in reason for reason in detail["risk_reasons"])


def test_removed_project_and_blocker_routes_do_not_exist(tmp_path: Path):
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'removed.db'}"))
    assert client.get("/api/projects").status_code == 404
    assert client.get("/api/blockers").status_code == 404
    assert client.get("/api/nodes").status_code == 404


def test_deployment_update_is_disabled_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("REQUIREMENT_MONITOR_DEPLOY_UPDATE_ENABLED", raising=False)
    client = TestClient(create_app(f"sqlite+pysqlite:///{tmp_path / 'deploy.db'}"))
    status = client.get("/api/deployment/update-status")
    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert client.post("/api/deployment/check-updates", json={}).status_code == 400
    assert client.post("/api/deployment/apply-update", json={}).status_code == 400


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
