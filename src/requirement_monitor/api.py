from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .database import initialize_database
from .service import (
    create_blocker,
    create_job,
    create_node,
    create_person,
    create_project,
    create_requirement,
    delete_job,
    delete_person,
    delete_project,
    get_requirement,
    list_blockers,
    list_jobs,
    list_notifications,
    list_nodes,
    list_people,
    list_projects,
    list_requirements,
    trigger_job,
    update_job,
    update_person,
    update_project,
    update_requirement,
)


class PersonInput(BaseModel):
    feishu_open_id: Optional[str] = None
    feishu_user_id: Optional[str] = None
    display_name: str = Field(min_length=1)
    description: str = ""
    email: Optional[str] = None
    active: bool = True


class ProjectInput(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    archived: bool = False


class RequirementInput(BaseModel):
    project_id: str
    requirement_key: str
    name: str
    project_owner_id: str
    product_owner_id: Optional[str] = None
    current_stage: str = "未开始"
    target_version: str = "未提供"
    merge_at: Optional[str] = None
    launch_at: Optional[str] = None
    briefing_completed: bool = False
    notification_enabled: bool = True
    archived: bool = False
    notes: str = ""


class RequirementPatch(BaseModel):
    requirement_key: Optional[str] = None
    name: Optional[str] = None
    project_owner_id: Optional[str] = None
    product_owner_id: Optional[str] = None
    current_stage: Optional[str] = None
    target_version: Optional[str] = None
    merge_at: Optional[str] = None
    launch_at: Optional[str] = None
    briefing_completed: Optional[bool] = None
    notification_enabled: Optional[bool] = None
    archived: Optional[bool] = None
    notes: Optional[str] = None
    requirement_doc_url: Optional[str] = None
    meego_url: Optional[str] = None
    translation_url: Optional[str] = None


class NodeInput(BaseModel):
    requirement_id: str
    name: str
    domain: str = "其他"
    work_type: str = "研发"
    owner_ids: List[str] = Field(min_length=1)
    planned_start: Optional[str] = None
    planned_end: Optional[str] = None
    actual_end: Optional[str] = None
    status: str = "未开始"
    progress_note: str = ""


class BlockerInput(BaseModel):
    requirement_id: str
    owner_id: str
    title: str
    found_at: Optional[str] = None
    planned_resolution_at: Optional[str] = None
    actual_resolution_at: Optional[str] = None
    status: str = "处理中"
    affects_merge: bool = False
    resolution_note: str = ""


class JobInput(BaseModel):
    name: str
    job_type: str = "risk_scan"
    interval_seconds: int = Field(default=86400, ge=10)
    timezone: str = "Asia/Shanghai"
    enabled: bool = True
    target_project_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    next_run_at: Optional[str] = None


def create_app(database_url: Optional[str] = None) -> FastAPI:
    resolved_database_url = database_url or os.getenv(
        "REQUIREMENT_MONITOR_DATABASE_URL",
        "sqlite+pysqlite:///./.state/requirement-monitor.db",
    )
    initialize_database(resolved_database_url)
    app = FastAPI(title="需求进展监控", version="0.2.0")
    app.state.database_url = resolved_database_url
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def db() -> str:
        return app.state.database_url

    @app.get("/api/health")
    def health():
        return {"status": "ok", "database": "ready"}

    @app.get("/api/people")
    def people():
        return list_people(db())

    @app.post("/api/people", status_code=201)
    def people_create(payload: PersonInput):
        return create_person(db(), payload.model_dump())

    @app.patch("/api/people/{person_id}")
    def people_patch(person_id: str, payload: PersonInput):
        result = update_person(db(), person_id, payload.model_dump(exclude_unset=True))
        if result is None:
            raise HTTPException(status_code=404, detail="person not found")
        return result

    @app.delete("/api/people/{person_id}")
    def people_delete(person_id: str):
        if not delete_person(db(), person_id):
            raise HTTPException(status_code=404, detail="person not found")
        return {"ok": True}

    @app.get("/api/projects")
    def projects():
        return list_projects(db())

    @app.post("/api/projects", status_code=201)
    def projects_create(payload: ProjectInput):
        return create_project(db(), payload.model_dump())

    @app.patch("/api/projects/{project_id}")
    def projects_patch(project_id: str, payload: ProjectInput):
        result = update_project(db(), project_id, payload.model_dump(exclude_unset=True))
        if result is None:
            raise HTTPException(status_code=404, detail="project not found")
        return result

    @app.delete("/api/projects/{project_id}")
    def projects_delete(project_id: str):
        if not delete_project(db(), project_id):
            raise HTTPException(status_code=404, detail="project not found")
        return {"ok": True}

    @app.get("/api/projects/{project_id}/requirements")
    def project_requirements(project_id: str):
        return list_requirements(db(), project_id)

    @app.get("/api/requirements")
    def requirements(project_id: Optional[str] = Query(default=None)):
        return list_requirements(db(), project_id)

    @app.post("/api/requirements", status_code=201)
    def requirements_create(payload: RequirementInput):
        try:
            return create_requirement(db(), payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/requirements/{requirement_id}")
    def requirement_detail(requirement_id: str):
        result = get_requirement(db(), requirement_id)
        if result is None:
            raise HTTPException(status_code=404, detail="requirement not found")
        return result

    @app.patch("/api/requirements/{requirement_id}")
    def requirement_patch(requirement_id: str, payload: RequirementPatch):
        try:
            result = update_requirement(db(), requirement_id, payload.model_dump(exclude_unset=True))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if result is None:
            raise HTTPException(status_code=404, detail="requirement not found")
        return result

    @app.get("/api/requirements/{requirement_id}/nodes")
    def requirement_nodes(requirement_id: str):
        return list_nodes(db(), requirement_id)

    @app.post("/api/nodes", status_code=201)
    def nodes_create(payload: NodeInput):
        try:
            return create_node(db(), payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/requirements/{requirement_id}/blockers")
    def requirement_blockers(requirement_id: str):
        return list_blockers(db(), requirement_id)

    @app.post("/api/blockers", status_code=201)
    def blockers_create(payload: BlockerInput):
        try:
            return create_blocker(db(), payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/jobs")
    def jobs():
        return list_jobs(db())

    @app.post("/api/jobs", status_code=201)
    def jobs_create(payload: JobInput):
        try:
            return create_job(db(), payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.patch("/api/jobs/{job_id}")
    def jobs_patch(job_id: str, payload: JobInput):
        result = update_job(db(), job_id, payload.model_dump(exclude_unset=True))
        if result is None:
            raise HTTPException(status_code=404, detail="job not found")
        return result

    @app.delete("/api/jobs/{job_id}")
    def jobs_delete(job_id: str):
        if not delete_job(db(), job_id):
            raise HTTPException(status_code=404, detail="job not found")
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/run")
    def jobs_run(job_id: str):
        result = trigger_job(db(), job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="job not found")
        return result

    @app.get("/api/notifications")
    def notifications(limit: int = Query(default=100, ge=1, le=500)):
        return list_notifications(db(), limit)

    web_dir = Path(__file__).resolve().parents[2] / "web"
    if web_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(web_dir)), name="assets")

        @app.get("/", response_class=FileResponse)
        def index():
            return FileResponse(web_dir / "index.html")

    return app


app = create_app()
