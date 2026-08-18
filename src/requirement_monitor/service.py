from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import (
    BaseConfigRow,
    BlockerRow,
    JobRunRow,
    NotificationDeliveryRow,
    NotificationOutboxRow,
    PersonRow,
    ProjectConfigRow,
    ProjectRow,
    RequirementNodeRow,
    RequirementRow,
    ScheduledJobRow,
    _ensure_person,
    _new_id,
    _utc_now,
    session_scope,
)
from .models import Person


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _person_dict(row: PersonRow) -> Dict[str, object]:
    return {
        "id": row.id,
        "feishu_open_id": row.feishu_open_id,
        "feishu_user_id": row.feishu_user_id,
        "display_name": row.display_name,
        "description": row.description,
        "email": row.email,
        "active": row.active,
    }


def _project_dict(row: ProjectRow) -> Dict[str, object]:
    return {
        "id": row.id,
        "source_key": row.source_key,
        "name": row.name,
        "description": row.description,
        "archived": row.archived,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _requirement_dict(row: RequirementRow) -> Dict[str, object]:
    return {
        "id": row.id,
        "source_record_id": row.source_record_id,
        "project_id": row.project_id,
        "requirement_key": row.requirement_key,
        "name": row.name,
        "okr_target": row.okr_target,
        "current_stage": row.current_stage,
        "target_version": row.target_version,
        "requirement_doc_url": row.requirement_doc_url,
        "meego_url": row.meego_url,
        "translation_url": row.translation_url,
        "merge_at": row.merge_at,
        "launch_at": row.launch_at,
        "project_owner_id": row.project_owner_id,
        "product_owner_id": row.product_owner_id,
        "briefing_completed": row.briefing_completed,
        "notification_enabled": row.notification_enabled,
        "archived": row.archived,
        "notes": row.notes,
        "system_risk_level": row.system_risk_level,
        "system_risk_reasons": row.system_risk_reasons,
        "predicted_completion_at": row.predicted_completion_at,
        "buffer_days": row.buffer_days,
        "last_checked_at": row.last_checked_at,
        "last_notified_at": row.last_notified_at,
    }


def _node_dict(row: RequirementNodeRow) -> Dict[str, object]:
    return {
        "id": row.id,
        "source_record_id": row.source_record_id,
        "requirement_id": row.requirement_id,
        "domain": row.domain,
        "work_type": row.work_type,
        "name": row.name,
        "planned_start": row.planned_start,
        "planned_end": row.planned_end,
        "actual_end": row.actual_end,
        "status": row.status,
        "progress_note": row.progress_note,
        "owners": [_person_dict(person) for person in row.owners],
        "risk_level": row.risk_level,
        "risk_reasons": row.risk_reasons,
        "safe_deadline": row.safe_deadline,
    }


def _blocker_dict(row: BlockerRow) -> Dict[str, object]:
    owner = row.owner
    return {
        "id": row.id,
        "source_record_id": row.source_record_id,
        "requirement_id": row.requirement_id,
        "node_source_record_id": row.node_source_record_id,
        "title": row.title,
        "owner": _person_dict(owner),
        "found_at": row.found_at,
        "planned_resolution_at": row.planned_resolution_at,
        "actual_resolution_at": row.actual_resolution_at,
        "status": row.status,
        "affects_merge": row.affects_merge,
        "resolution_note": row.resolution_note,
    }


def list_people(database_url: str) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        return [_person_dict(row) for row in session.scalars(select(PersonRow).order_by(PersonRow.display_name))]


def create_person(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    with session_scope(database_url) as session:
        open_id = str(data.get("feishu_open_id") or "").strip() or None
        row = session.scalar(select(PersonRow).where(PersonRow.feishu_open_id == open_id)) if open_id else None
        if row is None:
            row = PersonRow(feishu_open_id=open_id, display_name=str(data.get("display_name") or "未命名人员"))
            session.add(row)
        row.feishu_user_id = data.get("feishu_user_id") or row.feishu_user_id
        row.display_name = str(data.get("display_name") or row.display_name)
        row.description = str(data.get("description") or "")
        row.email = data.get("email") or row.email
        row.active = bool(data.get("active", True))
        session.flush()
        return _person_dict(row)


def update_person(database_url: str, person_id: str, data: Mapping[str, object]) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(PersonRow, person_id)
        if row is None:
            return None
        for field in ("display_name", "description", "email", "feishu_open_id", "feishu_user_id"):
            if field in data:
                setattr(row, field, data[field] or None if field != "description" else str(data[field] or ""))
        if "active" in data:
            row.active = bool(data["active"])
        session.flush()
        return _person_dict(row)


def delete_person(database_url: str, person_id: str) -> bool:
    with session_scope(database_url) as session:
        row = session.get(PersonRow, person_id)
        if row is None:
            return False
        row.active = False
        return True


def list_projects(database_url: str) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        return [_project_dict(row) for row in session.scalars(select(ProjectRow).order_by(ProjectRow.archived, ProjectRow.name))]


def create_project(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("project name is required")
    with session_scope(database_url) as session:
        row = session.scalar(select(ProjectRow).where(ProjectRow.source_key == name))
        if row is None:
            row = ProjectRow(source_key=name, name=name)
            session.add(row)
        row.name = name
        row.description = str(data.get("description") or "")
        row.archived = bool(data.get("archived", False))
        session.flush()
        return _project_dict(row)


def update_project(database_url: str, project_id: str, data: Mapping[str, object]) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(ProjectRow, project_id)
        if row is None:
            return None
        if "name" in data and str(data["name"]).strip():
            row.name = str(data["name"]).strip()
            row.source_key = row.name
        if "description" in data:
            row.description = str(data["description"] or "")
        if "archived" in data:
            row.archived = bool(data["archived"])
        session.flush()
        return _project_dict(row)


def delete_project(database_url: str, project_id: str) -> bool:
    with session_scope(database_url) as session:
        row = session.get(ProjectRow, project_id)
        if row is None:
            return False
        row.archived = True
        return True


def list_requirements(database_url: str, project_id: Optional[str] = None) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        query = select(RequirementRow).order_by(RequirementRow.archived, RequirementRow.merge_at)
        if project_id:
            query = query.where(RequirementRow.project_id == project_id)
        return [_requirement_dict(row) for row in session.scalars(query)]


def get_requirement(database_url: str, requirement_id: str) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(RequirementRow, requirement_id)
        return _requirement_dict(row) if row else None


def create_requirement(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    project_id = str(data.get("project_id") or "").strip()
    key = str(data.get("requirement_key") or "").strip()
    name = str(data.get("name") or "").strip()
    owner_id = str(data.get("project_owner_id") or "").strip()
    if not project_id or not key or not name or not owner_id:
        raise ValueError("project_id, requirement_key, name and project_owner_id are required")
    with session_scope(database_url) as session:
        project = session.get(ProjectRow, project_id)
        owner = session.get(PersonRow, owner_id)
        if project is None or owner is None:
            raise ValueError("project or project owner not found")
        row = RequirementRow(
            source_record_id=f"local-{_new_id()}",
            project_id=project.id,
            requirement_key=key,
            name=name,
            okr_target=project.name,
            current_stage=str(data.get("current_stage") or "未开始"),
            target_version=str(data.get("target_version") or "未提供"),
            merge_at=_parse_datetime(data.get("merge_at")) or _utc_now(),
            launch_at=_parse_datetime(data.get("launch_at")),
            project_owner_id=owner.id,
            product_owner_id=str(data.get("product_owner_id") or "") or None,
            briefing_completed=bool(data.get("briefing_completed", False)),
            notification_enabled=bool(data.get("notification_enabled", True)),
            archived=bool(data.get("archived", False)),
            notes=str(data.get("notes") or ""),
        )
        session.add(row)
        session.flush()
        return _requirement_dict(row)


def update_requirement(database_url: str, requirement_id: str, data: Mapping[str, object]) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(RequirementRow, requirement_id)
        if row is None:
            return None
        for field in ("name", "requirement_key", "current_stage", "target_version", "notes", "requirement_doc_url", "meego_url", "translation_url"):
            if field in data and data[field] is not None:
                setattr(row, field, str(data[field]))
        for field in ("briefing_completed", "notification_enabled", "archived"):
            if field in data:
                setattr(row, field, bool(data[field]))
        for field in ("merge_at", "launch_at"):
            if field in data:
                setattr(row, field, _parse_datetime(data[field]))
        for field in ("project_owner_id", "product_owner_id"):
            if field in data:
                person = session.get(PersonRow, str(data[field])) if data[field] else None
                if field == "project_owner_id" and person is None:
                    raise ValueError("project owner not found")
                setattr(row, field, person.id if person else None)
        session.flush()
        return _requirement_dict(row)


def list_nodes(database_url: str, requirement_id: Optional[str] = None) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        query = select(RequirementNodeRow).order_by(RequirementNodeRow.planned_end, RequirementNodeRow.name)
        if requirement_id:
            query = query.where(RequirementNodeRow.requirement_id == requirement_id)
        return [_node_dict(row) for row in session.scalars(query)]


def create_node(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    requirement_id = str(data.get("requirement_id") or "").strip()
    name = str(data.get("name") or "").strip()
    owner_ids = [str(item).strip() for item in data.get("owner_ids", []) if str(item).strip()]
    if not requirement_id or not name or not owner_ids:
        raise ValueError("requirement_id, name and owner_ids are required")
    with session_scope(database_url) as session:
        requirement = session.get(RequirementRow, requirement_id)
        owners = [session.get(PersonRow, owner_id) for owner_id in owner_ids]
        if requirement is None or any(owner is None for owner in owners):
            raise ValueError("requirement or node owner not found")
        row = RequirementNodeRow(
            source_record_id=f"local-{_new_id()}",
            requirement_id=requirement.id,
            domain=str(data.get("domain") or "其他"),
            work_type=str(data.get("work_type") or "研发"),
            name=name,
            planned_start=_parse_datetime(data.get("planned_start")),
            planned_end=_parse_datetime(data.get("planned_end")),
            actual_end=_parse_datetime(data.get("actual_end")),
            status=str(data.get("status") or "未开始"),
            progress_note=str(data.get("progress_note") or ""),
            owners=[owner for owner in owners if owner is not None],
        )
        session.add(row)
        session.flush()
        return _node_dict(row)


def list_blockers(database_url: str, requirement_id: Optional[str] = None) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        query = select(BlockerRow).order_by(BlockerRow.affects_merge.desc(), BlockerRow.planned_resolution_at)
        if requirement_id:
            query = query.where(BlockerRow.requirement_id == requirement_id)
        return [_blocker_dict(row) for row in session.scalars(query)]


def create_blocker(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    requirement_id = str(data.get("requirement_id") or "").strip()
    owner_id = str(data.get("owner_id") or "").strip()
    title = str(data.get("title") or "").strip()
    found_at = _parse_datetime(data.get("found_at")) or _utc_now()
    planned = _parse_datetime(data.get("planned_resolution_at")) or found_at
    if not requirement_id or not owner_id or not title:
        raise ValueError("requirement_id, owner_id and title are required")
    with session_scope(database_url) as session:
        requirement = session.get(RequirementRow, requirement_id)
        owner = session.get(PersonRow, owner_id)
        if requirement is None or owner is None:
            raise ValueError("requirement or blocker owner not found")
        row = BlockerRow(source_record_id=f"local-{_new_id()}", requirement_id=requirement.id, title=title, owner_id=owner.id, found_at=found_at, planned_resolution_at=planned, actual_resolution_at=_parse_datetime(data.get("actual_resolution_at")), status=str(data.get("status") or "处理中"), affects_merge=bool(data.get("affects_merge", False)), resolution_note=str(data.get("resolution_note") or ""))
        session.add(row)
        session.flush()
        return _blocker_dict(row)


def list_jobs(database_url: str) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        return [_job_dict(row) for row in session.scalars(select(ScheduledJobRow).order_by(ScheduledJobRow.enabled.desc(), ScheduledJobRow.name))]


def _job_dict(row: ScheduledJobRow) -> Dict[str, object]:
    return {"id": row.id, "name": row.name, "job_type": row.job_type, "interval_seconds": row.interval_seconds, "timezone": row.timezone, "enabled": row.enabled, "target_project_id": row.target_project_id, "payload": row.payload, "next_run_at": row.next_run_at, "last_run_at": row.last_run_at, "last_status": row.last_status}


def create_job(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    name = str(data.get("name") or "").strip()
    job_type = str(data.get("job_type") or "risk_scan").strip()
    if not name:
        raise ValueError("job name is required")
    with session_scope(database_url) as session:
        row = session.scalar(select(ScheduledJobRow).where(ScheduledJobRow.name == name))
        if row is None:
            row = ScheduledJobRow(name=name, job_type=job_type)
            session.add(row)
        row.job_type = job_type
        row.interval_seconds = max(10, int(data.get("interval_seconds") or 86400))
        row.timezone = str(data.get("timezone") or "Asia/Shanghai")
        row.enabled = bool(data.get("enabled", True))
        row.target_project_id = data.get("target_project_id") or None
        row.payload = dict(data.get("payload") or {})
        row.next_run_at = _parse_datetime(data.get("next_run_at")) or _utc_now()
        session.flush()
        return _job_dict(row)


def update_job(database_url: str, job_id: str, data: Mapping[str, object]) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(ScheduledJobRow, job_id)
        if row is None:
            return None
        for field in ("name", "job_type", "timezone"):
            if field in data and data[field] is not None:
                setattr(row, field, str(data[field]))
        if "interval_seconds" in data:
            row.interval_seconds = max(10, int(data["interval_seconds"]))
        if "enabled" in data:
            row.enabled = bool(data["enabled"])
        if "payload" in data:
            row.payload = dict(data["payload"] or {})
        if "next_run_at" in data:
            row.next_run_at = _parse_datetime(data["next_run_at"]) or _utc_now()
        session.flush()
        return _job_dict(row)


def delete_job(database_url: str, job_id: str) -> bool:
    with session_scope(database_url) as session:
        row = session.get(ScheduledJobRow, job_id)
        if row is None:
            return False
        row.enabled = False
        return True


def trigger_job(database_url: str, job_id: str) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(ScheduledJobRow, job_id)
        if row is None:
            return None
        row.next_run_at = _utc_now()
        row.enabled = True
        session.flush()
        return _job_dict(row)


def enqueue_notification(database_url: str, payload: Mapping[str, object], *, channel: str = "default", run_id: Optional[str] = None, deduplication_key: Optional[str] = None) -> Dict[str, object]:
    dedup = deduplication_key or hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default).encode("utf-8")).hexdigest()
    with session_scope(database_url) as session:
        existing = session.scalar(select(NotificationOutboxRow).where(NotificationOutboxRow.deduplication_key == dedup))
        if existing is not None:
            return {"id": existing.id, "status": existing.status, "duplicate": True}
        row = NotificationOutboxRow(run_id=run_id, channel=channel, deduplication_key=dedup, payload=dict(payload), available_at=_utc_now())
        session.add(row)
        session.flush()
        return {"id": row.id, "status": row.status, "duplicate": False}


def list_notifications(database_url: str, limit: int = 100) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        rows = list(session.scalars(select(NotificationOutboxRow).order_by(NotificationOutboxRow.created_at.desc()).limit(limit)))
        return [{"id": row.id, "channel": row.channel, "status": row.status, "attempt_count": row.attempt_count, "last_error": row.last_error, "sent_at": row.sent_at, "created_at": row.created_at} for row in rows]


def _parse_datetime(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
