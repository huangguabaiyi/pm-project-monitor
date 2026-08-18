from tempfile import NamedTemporaryFile

from fastapi.testclient import TestClient

from requirement_monitor.api import create_app
from requirement_monitor.database import initialize_database


def test_api_can_create_people_projects_requirements_and_jobs():
    with NamedTemporaryFile(suffix=".db") as database_file:
        database_url = "sqlite+pysqlite:///" + database_file.name
        initialize_database(database_url)
        client = TestClient(create_app(database_url))

        assert client.get("/api/health").json()["status"] == "ok"
        person = client.post(
            "/api/people",
            json={"display_name": "项目负责人", "feishu_open_id": "ou-owner"},
        )
        assert person.status_code == 201
        person_id = person.json()["id"]

        project = client.post("/api/projects", json={"name": "项目 A"})
        assert project.status_code == 201
        project_id = project.json()["id"]

        requirement = client.post(
            "/api/requirements",
            json={
                "project_id": project_id,
                "requirement_key": "REQ-1",
                "name": "登录优化",
                "project_owner_id": person_id,
                "merge_at": "2026-08-20T18:00:00+08:00",
            },
        )
        assert requirement.status_code == 201
        requirement_id = requirement.json()["id"]

        node = client.post(
            "/api/nodes",
            json={
                "requirement_id": requirement_id,
                "name": "客户端开发",
                "owner_ids": [person_id],
            },
        )
        assert node.status_code == 201

        job = client.post(
            "/api/jobs",
            json={"name": "每日检查", "interval_seconds": 3600},
        )
        assert job.status_code == 201
        assert client.get("/api/requirements").json()[0]["requirement_key"] == "REQ-1"
