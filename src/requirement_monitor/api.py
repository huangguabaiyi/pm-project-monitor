from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AnyHttpUrl, BaseModel, Field

from .ai_analysis import plus_login_manager
from .database import initialize_database
from .deployment import deployment_updates
from .service import (
    add_template_edge,
    add_template_node,
    analyze_requirement,
    create_definition,
    create_domain,
    create_job,
    create_person,
    create_requirement,
    create_template,
    clear_data,
    dashboard_summary,
    deactivate_definition,
    deactivate_domain,
    deactivate_person,
    delete_template_edge,
    delete_template_node,
    export_data,
    get_requirement,
    get_ai_settings,
    get_template,
    get_webhook_settings,
    import_data,
    list_definitions,
    list_domains,
    list_jobs,
    list_notifications,
    list_people,
    list_requirements,
    list_templates,
    move_template_node,
    trigger_job,
    update_definition,
    update_ai_settings,
    update_domain,
    update_job,
    update_person,
    update_requirement,
    update_requirement_node,
    update_template,
    update_webhook_settings,
)


class PersonInput(BaseModel):
    display_name: str = Field(min_length=1)
    role_name: str = ""
    domain_id: Optional[str] = None
    feishu_open_id: Optional[str] = None
    email: Optional[str] = None
    description: str = ""
    active: bool = True


class PersonPatch(BaseModel):
    display_name: Optional[str] = None
    role_name: Optional[str] = None
    domain_id: Optional[str] = None
    feishu_open_id: Optional[str] = None
    email: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None


class DomainInput(BaseModel):
    name: str = Field(min_length=1)
    color: str = "#2f7d57"
    description: str = ""
    sort_order: int = 0
    active: bool = True


class DomainPatch(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    active: Optional[bool] = None


class DefinitionInput(BaseModel):
    name: str = Field(min_length=1)
    domain_id: Optional[str] = None
    domain_ids: Optional[List[str]] = None
    description: str = ""
    completion_criteria: str = ""
    active: bool = True


class DefinitionPatch(BaseModel):
    name: Optional[str] = None
    domain_id: Optional[str] = None
    domain_ids: Optional[List[str]] = None
    description: Optional[str] = None
    completion_criteria: Optional[str] = None
    active: Optional[bool] = None


class TemplateInput(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    active: bool = True


class TemplatePatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None


class TemplateNodeInput(BaseModel):
    definition_id: str
    position_x: float = 0
    position_y: float = 0


class TemplateNodePosition(BaseModel):
    position_x: float
    position_y: float


class EdgeInput(BaseModel):
    source: str
    target: str


class RequirementInput(BaseModel):
    name: str = Field(min_length=1)
    owner_id: str
    template_id: str
    target_version: str = ""
    meego_url: Optional[AnyHttpUrl] = None
    requirement_url: Optional[AnyHttpUrl] = None
    figma_url: Optional[AnyHttpUrl] = None
    notes: str = ""


class RequirementPatch(BaseModel):
    name: Optional[str] = None
    owner_id: Optional[str] = None
    target_version: Optional[str] = None
    meego_url: Optional[AnyHttpUrl] = None
    requirement_url: Optional[AnyHttpUrl] = None
    figma_url: Optional[AnyHttpUrl] = None
    notes: Optional[str] = None
    archived: Optional[bool] = None


class RequirementNodePatch(BaseModel):
    planned_start: Optional[str] = None
    planned_end: Optional[str] = None
    status: Optional[str] = None
    blocked_reason: Optional[str] = None
    notes: Optional[str] = None
    owner_ids: Optional[List[str]] = None


class JobInput(BaseModel):
    name: str
    job_type: str = "risk_scan"
    interval_seconds: int = Field(default=86400, ge=10)
    enabled: bool = True
    next_run_at: Optional[str] = None


class JobPatch(BaseModel):
    name: Optional[str] = None
    interval_seconds: Optional[int] = Field(default=None, ge=10)
    enabled: Optional[bool] = None
    next_run_at: Optional[str] = None


class WebhookSettingsPatch(BaseModel):
    enabled: Optional[bool] = None
    runtime_environment: Optional[str] = None
    test_webhook_url: Optional[str] = None
    prod_webhook_url: Optional[str] = None
    bot_keyword: Optional[str] = None


class AISettingsPatch(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    prompt: Optional[str] = None
    include_in_feishu: Optional[bool] = None
    auto_analyze: Optional[bool] = None


class DeploymentUpdateInput(BaseModel):
    skip_backup: bool = False


class DataClearInput(BaseModel):
    preserve_settings: bool = True


class DataImportInput(BaseModel):
    backup: Dict[str, object]
    preserve_settings: bool = False


def create_app(database_url: Optional[str] = None) -> FastAPI:
    resolved_url = database_url or os.getenv("REQUIREMENT_MONITOR_DATABASE_URL", "sqlite+pysqlite:///./.state/pulse.db")
    initialize_database(resolved_url)
    app = FastAPI(title="Pulse 需求交付管理", version="1.0.0")
    app.state.database_url = resolved_url
    db = lambda: app.state.database_url

    @app.get("/api/health")
    def health():
        return {"status": "ok", "database": "ready", "version": "1.0.0"}

    @app.get("/api/dashboard")
    def dashboard():
        return dashboard_summary(db())

    @app.get("/api/people")
    def people():
        return list_people(db())

    @app.post("/api/people", status_code=201)
    def people_create(payload: PersonInput):
        try:
            return create_person(db(), payload.model_dump())
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.patch("/api/people/{person_id}")
    def people_update(person_id: str, payload: PersonPatch):
        try:
            result = update_person(db(), person_id, payload.model_dump(exclude_unset=True))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        if result is None:
            raise HTTPException(404, "person not found")
        return result

    @app.delete("/api/people/{person_id}")
    def people_delete(person_id: str):
        if not deactivate_person(db(), person_id):
            raise HTTPException(404, "person not found")
        return {"ok": True}

    @app.get("/api/domains")
    def domains():
        return list_domains(db())

    @app.post("/api/domains", status_code=201)
    def domains_create(payload: DomainInput):
        try:
            return create_domain(db(), payload.model_dump())
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.patch("/api/domains/{domain_id}")
    def domains_update(domain_id: str, payload: DomainPatch):
        result = update_domain(db(), domain_id, payload.model_dump(exclude_unset=True))
        if result is None:
            raise HTTPException(404, "domain not found")
        return result

    @app.delete("/api/domains/{domain_id}")
    def domains_delete(domain_id: str):
        if not deactivate_domain(db(), domain_id):
            raise HTTPException(404, "domain not found")
        return {"ok": True}

    @app.get("/api/node-definitions")
    def definitions():
        return list_definitions(db())

    @app.post("/api/node-definitions", status_code=201)
    def definitions_create(payload: DefinitionInput):
        try:
            return create_definition(db(), payload.model_dump())
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.patch("/api/node-definitions/{definition_id}")
    def definitions_update(definition_id: str, payload: DefinitionPatch):
        try:
            result = update_definition(db(), definition_id, payload.model_dump(exclude_unset=True))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        if result is None:
            raise HTTPException(404, "node definition not found")
        return result

    @app.delete("/api/node-definitions/{definition_id}")
    def definitions_delete(definition_id: str):
        if not deactivate_definition(db(), definition_id):
            raise HTTPException(404, "node definition not found")
        return {"ok": True}

    @app.get("/api/templates")
    def templates():
        return list_templates(db())

    @app.post("/api/templates", status_code=201)
    def templates_create(payload: TemplateInput):
        try:
            return create_template(db(), payload.model_dump())
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/templates/{template_id}")
    def templates_get(template_id: str):
        result = get_template(db(), template_id)
        if result is None:
            raise HTTPException(404, "template not found")
        return result

    @app.patch("/api/templates/{template_id}")
    def templates_update(template_id: str, payload: TemplatePatch):
        result = update_template(db(), template_id, payload.model_dump(exclude_unset=True))
        if result is None:
            raise HTTPException(404, "template not found")
        return result

    @app.post("/api/templates/{template_id}/nodes", status_code=201)
    def template_nodes_create(template_id: str, payload: TemplateNodeInput):
        try:
            return add_template_node(db(), template_id, payload.model_dump())
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.patch("/api/template-nodes/{node_id}")
    def template_nodes_move(node_id: str, payload: TemplateNodePosition):
        result = move_template_node(db(), node_id, payload.model_dump())
        if result is None:
            raise HTTPException(404, "template node not found")
        return result

    @app.delete("/api/template-nodes/{node_id}")
    def template_nodes_delete(node_id: str):
        if not delete_template_node(db(), node_id):
            raise HTTPException(404, "template node not found")
        return {"ok": True}

    @app.post("/api/templates/{template_id}/edges", status_code=201)
    def template_edges_create(template_id: str, payload: EdgeInput):
        try:
            return add_template_edge(db(), template_id, payload.model_dump())
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.delete("/api/template-edges/{edge_id}")
    def template_edges_delete(edge_id: str):
        if not delete_template_edge(db(), edge_id):
            raise HTTPException(404, "template edge not found")
        return {"ok": True}

    @app.get("/api/requirements")
    def requirements():
        return list_requirements(db())

    @app.post("/api/requirements", status_code=201)
    def requirements_create(payload: RequirementInput):
        try:
            return create_requirement(db(), payload.model_dump())
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/requirements/{requirement_id}")
    def requirements_get(requirement_id: str):
        result = get_requirement(db(), requirement_id)
        if result is None:
            raise HTTPException(404, "requirement not found")
        return result

    @app.patch("/api/requirements/{requirement_id}")
    def requirements_update(requirement_id: str, payload: RequirementPatch):
        try:
            result = update_requirement(db(), requirement_id, payload.model_dump(exclude_unset=True))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        if result is None:
            raise HTTPException(404, "requirement not found")
        return result

    @app.delete("/api/requirements/{requirement_id}")
    def requirements_archive(requirement_id: str):
        result = update_requirement(db(), requirement_id, {"archived": True})
        if result is None:
            raise HTTPException(404, "requirement not found")
        return {"ok": True}

    @app.patch("/api/requirement-nodes/{node_id}")
    def requirement_nodes_update(node_id: str, payload: RequirementNodePatch):
        try:
            result = update_requirement_node(db(), node_id, payload.model_dump(exclude_unset=True))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        if result is None:
            raise HTTPException(404, "requirement node not found")
        return result

    @app.post("/api/requirements/{requirement_id}/ai-analysis")
    def requirement_ai_analyze(requirement_id: str):
        try:
            return analyze_requirement(db(), requirement_id, force=True)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/jobs")
    def jobs():
        return list_jobs(db())

    @app.post("/api/jobs", status_code=201)
    def jobs_create(payload: JobInput):
        return create_job(db(), payload.model_dump())

    @app.patch("/api/jobs/{job_id}")
    def jobs_update(job_id: str, payload: JobPatch):
        try:
            result = update_job(db(), job_id, payload.model_dump(exclude_unset=True))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        if result is None:
            raise HTTPException(404, "job not found")
        return result

    @app.post("/api/jobs/{job_id}/run")
    def jobs_run(job_id: str):
        result = trigger_job(db(), job_id)
        if result is None:
            raise HTTPException(404, "job not found")
        return result

    @app.get("/api/deployment/update-status")
    def deployment_update_status():
        return deployment_updates.status()

    @app.post("/api/deployment/check-updates")
    def deployment_update_check():
        try:
            return deployment_updates.check_updates()
        except RuntimeError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/deployment/apply-update")
    def deployment_update_apply(payload: DeploymentUpdateInput):
        try:
            return deployment_updates.start_update(skip_backup=payload.skip_backup)
        except RuntimeError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/admin/export")
    def admin_export():
        payload = export_data(db())
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return JSONResponse(
            payload,
            headers={"Content-Disposition": f'attachment; filename="pm-project-monitor-backup-{stamp}.json"'},
        )

    @app.post("/api/admin/import")
    def admin_import(payload: DataImportInput):
        try:
            return import_data(db(), payload.backup, preserve_settings=payload.preserve_settings)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/admin/clear")
    def admin_clear(payload: DataClearInput):
        return clear_data(db(), preserve_settings=payload.preserve_settings)

    @app.get("/api/notifications")
    def notifications(limit: int = Query(default=100, ge=1, le=500)):
        return list_notifications(db(), limit)

    @app.get("/api/webhook-settings")
    def webhook_settings_get():
        return get_webhook_settings(db())

    @app.patch("/api/webhook-settings")
    def webhook_settings_update(payload: WebhookSettingsPatch):
        try:
            return update_webhook_settings(db(), payload.model_dump(exclude_unset=True))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/ai-settings")
    def ai_settings_get():
        return get_ai_settings(db())

    @app.patch("/api/ai-settings")
    def ai_settings_update(payload: AISettingsPatch):
        try:
            return update_ai_settings(db(), payload.model_dump(exclude_unset=True))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/ai-settings/plus/status")
    def ai_plus_status():
        return plus_login_manager.status()

    @app.post("/api/ai-settings/plus/login")
    def ai_plus_login():
        try:
            return plus_login_manager.start()
        except RuntimeError as error:
            raise HTTPException(400, str(error)) from error

    web_dir = Path(__file__).resolve().parents[2] / "web"
    if web_dir.exists():
        assets_dir = web_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/", response_class=FileResponse)
        def index():
            return FileResponse(web_dir / "index.html")

        @app.get("/{full_path:path}", response_class=FileResponse)
        def spa_fallback(full_path: str):
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(404, "not found")
            return FileResponse(web_dir / "index.html")
    return app


app = create_app()
