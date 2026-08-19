from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Mapping, Optional, Sequence

from sqlalchemy import Boolean, DateTime, func, insert, select
from sqlalchemy.orm import selectinload

from .ai_analysis import (
    DEFAULT_AI_PROMPT,
    analyze_with_chatgpt_plus,
    analyze_with_compatible_api,
    input_fingerprint,
    requirement_ai_input,
)
from .database import (
    AISettingsRow,
    Base,
    DeliveryDomainRow,
    JobRunRow,
    NodeDefinitionRow,
    NotificationOutboxRow,
    PersonRow,
    RequirementEdgeRow,
    RequirementNodeRow,
    RequirementRow,
    RequirementSequenceRow,
    ScheduledJobRow,
    WorkflowTemplateEdgeRow,
    WorkflowTemplateNodeRow,
    WorkflowTemplateRow,
    WebhookSettingsRow,
    create_database_engine,
    _new_id,
    _utc_now,
    session_scope,
)
from .schedule_risk import RiskLevel, evaluate_schedule
from .scheduler import next_cron_run, validate_cron
from .webhook_url import is_allowed_webhook_url


NODE_STATUSES = {"not_started", "in_progress", "blocked", "completed", "skipped"}
JOB_TYPES = {"risk_scan", "outbox_delivery", "ai_analysis"}
SCHEDULE_KINDS = {"interval", "cron"}
NOTIFICATION_SCOPES = {"all", "risk_only"}
BACKUP_FORMAT_VERSION = 1
SETTINGS_TABLES = {"webhook_settings", "ai_settings", "scheduled_jobs"}


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _database_tables():
    return list(Base.metadata.sorted_tables)


def _restore_value(value: object, column) -> object:
    if value is None:
        return None
    if isinstance(column.type, DateTime) and isinstance(value, str):
        return _datetime(value)
    if isinstance(column.type, Boolean) and isinstance(value, bool):
        return value
    return value


def _datetime(value) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _api_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat()


def export_data(database_url: str) -> Dict[str, object]:
    engine = create_database_engine(database_url)
    tables: Dict[str, List[Dict[str, object]]] = {}
    with engine.connect() as connection:
        for table in _database_tables():
            rows = connection.execute(select(table)).mappings().all()
            tables[table.name] = [
                {key: _json_value(value) for key, value in row.items()}
                for row in rows
            ]
    return {
        "format": "pm-project-monitor.backup",
        "version": BACKUP_FORMAT_VERSION,
        "exported_at": _utc_now().isoformat(),
        "tables": tables,
    }


def clear_data(database_url: str, *, preserve_settings: bool = True) -> Dict[str, object]:
    engine = create_database_engine(database_url)
    deleted: Dict[str, int] = {}
    with engine.begin() as connection:
        for table in reversed(_database_tables()):
            if preserve_settings and table.name in SETTINGS_TABLES:
                continue
            result = connection.execute(table.delete())
            deleted[table.name] = result.rowcount or 0
    return {"ok": True, "preserve_settings": preserve_settings, "deleted": deleted}


def import_data(database_url: str, backup: Mapping[str, object], *, preserve_settings: bool = False) -> Dict[str, object]:
    if backup.get("format") != "pm-project-monitor.backup":
        raise ValueError("invalid backup format")
    if int(backup.get("version") or 0) != BACKUP_FORMAT_VERSION:
        raise ValueError("unsupported backup version")
    raw_tables = backup.get("tables")
    if not isinstance(raw_tables, Mapping):
        raise ValueError("backup tables are missing")
    tables_by_name = {table.name: table for table in _database_tables()}
    unknown = sorted(set(raw_tables) - set(tables_by_name))
    if unknown:
        raise ValueError("backup contains unknown tables: {}".format(", ".join(unknown)))

    engine = create_database_engine(database_url)
    imported: Dict[str, int] = {}
    with engine.begin() as connection:
        for table in reversed(_database_tables()):
            if preserve_settings and table.name in SETTINGS_TABLES:
                continue
            connection.execute(table.delete())
        for table in _database_tables():
            if preserve_settings and table.name in SETTINGS_TABLES:
                continue
            rows = raw_tables.get(table.name, [])
            if not isinstance(rows, list):
                raise ValueError("backup table {} must be a list".format(table.name))
            payload = []
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ValueError("backup table {} contains invalid row".format(table.name))
                payload.append(
                    {
                        column.name: _restore_value(row[column.name], column)
                        for column in table.columns
                        if column.name in row
                    }
                )
            if payload:
                connection.execute(insert(table), payload)
            imported[table.name] = len(payload)
    return {"ok": True, "preserve_settings": preserve_settings, "imported": imported}


def _person(row: PersonRow) -> Dict[str, object]:
    domain = _domain(row.domain) if row.domain is not None else None
    return {"id": row.id, "display_name": row.display_name, "feishu_open_id": row.feishu_open_id, "email": row.email, "role_name": row.role_name, "domain_id": row.domain_id, "domain": domain, "description": row.description, "active": row.active}


def list_people(database_url: str) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        return [_person(row) for row in session.scalars(select(PersonRow).order_by(PersonRow.active.desc(), PersonRow.display_name))]


def create_person(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    name = str(data.get("display_name") or "").strip()
    if not name:
        raise ValueError("display_name is required")
    with session_scope(database_url) as session:
        domain_id = str(data.get("domain_id") or "") or None
        if domain_id and session.get(DeliveryDomainRow, domain_id) is None:
            raise ValueError("delivery domain not found")
        row = PersonRow(display_name=name, domain_id=domain_id)
        session.add(row)
        for field in ("feishu_open_id", "email", "role_name", "description"):
            setattr(row, field, str(data.get(field) or "") or None if field in {"feishu_open_id", "email"} else str(data.get(field) or ""))
        row.active = bool(data.get("active", True))
        session.flush()
        if domain_id:
            session.refresh(row, ["domain"])
        return _person(row)


def update_person(database_url: str, person_id: str, data: Mapping[str, object]) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(PersonRow, person_id)
        if row is None:
            return None
        for field in ("display_name", "feishu_open_id", "email", "role_name", "description"):
            if field in data:
                value = str(data[field] or "").strip()
                setattr(row, field, value or None if field in {"feishu_open_id", "email"} else value)
        if "domain_id" in data:
            domain_id = str(data["domain_id"] or "") or None
            if domain_id and session.get(DeliveryDomainRow, domain_id) is None:
                raise ValueError("delivery domain not found")
            row.domain_id = domain_id
        if "active" in data:
            row.active = bool(data["active"])
        session.flush()
        if row.domain_id:
            session.refresh(row, ["domain"])
        return _person(row)


def deactivate_person(database_url: str, person_id: str) -> bool:
    return update_person(database_url, person_id, {"active": False}) is not None


def _domain(row: DeliveryDomainRow) -> Dict[str, object]:
    return {"id": row.id, "name": row.name, "color": row.color, "description": row.description, "sort_order": row.sort_order, "active": row.active}


def list_domains(database_url: str) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        return [_domain(row) for row in session.scalars(select(DeliveryDomainRow).order_by(DeliveryDomainRow.sort_order, DeliveryDomainRow.name))]


def create_domain(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("domain name is required")
    with session_scope(database_url) as session:
        if session.scalar(select(DeliveryDomainRow).where(DeliveryDomainRow.name == name)):
            raise ValueError("domain name already exists")
        row = DeliveryDomainRow(name=name, color=str(data.get("color") or "#2f7d57"), description=str(data.get("description") or ""), sort_order=int(data.get("sort_order") or 0))
        session.add(row)
        session.flush()
        return _domain(row)


def update_domain(database_url: str, domain_id: str, data: Mapping[str, object]) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(DeliveryDomainRow, domain_id)
        if row is None:
            return None
        for field in ("name", "color", "description"):
            if field in data and data[field] is not None:
                setattr(row, field, str(data[field]).strip())
        if "sort_order" in data:
            row.sort_order = int(data["sort_order"])
        if "active" in data:
            row.active = bool(data["active"])
        session.flush()
        return _domain(row)


def deactivate_domain(database_url: str, domain_id: str) -> bool:
    return update_domain(database_url, domain_id, {"active": False}) is not None


def _definition(row: NodeDefinitionRow) -> Dict[str, object]:
    domain_rows = list(row.domains or [])
    if row.domain and all(domain.id != row.domain_id for domain in domain_rows):
        domain_rows.insert(0, row.domain)
    domain_rows.sort(key=lambda domain: (0 if domain.id == row.domain_id else 1, domain.sort_order, domain.name))
    domains = [_domain(domain) for domain in (domain_rows or [row.domain])]
    return {"id": row.id, "name": row.name, "domain_id": row.domain_id, "domain_ids": [domain["id"] for domain in domains], "domain": _domain(row.domain), "domains": domains, "description": row.description, "completion_criteria": row.completion_criteria, "active": row.active}


def list_definitions(database_url: str) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        rows = session.scalars(select(NodeDefinitionRow).options(selectinload(NodeDefinitionRow.domain), selectinload(NodeDefinitionRow.domains)).order_by(NodeDefinitionRow.active.desc(), NodeDefinitionRow.name))
        return [_definition(row) for row in rows]


def _definition_domain_ids(data: Mapping[str, object]) -> List[str]:
    raw_ids = data.get("domain_ids")
    if raw_ids is None:
        raw_ids = [data.get("domain_id")]
    if isinstance(raw_ids, str):
        values = [raw_ids]
    else:
        values = list(raw_ids or [])
    return list(dict.fromkeys(str(value) for value in values if str(value or "").strip()))


def create_definition(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    name = str(data.get("name") or "").strip()
    domain_ids = _definition_domain_ids(data)
    if not name or not domain_ids:
        raise ValueError("name and domain_ids are required")
    with session_scope(database_url) as session:
        domains = [session.get(DeliveryDomainRow, domain_id) for domain_id in domain_ids]
        if any(domain is None for domain in domains):
            raise ValueError("delivery domain not found")
        if session.scalar(select(NodeDefinitionRow).where(NodeDefinitionRow.name == name)):
            raise ValueError("node name already exists")
        row = NodeDefinitionRow(name=name, domain_id=domain_ids[0], description=str(data.get("description") or ""), completion_criteria=str(data.get("completion_criteria") or ""), domains=[domain for domain in domains if domain])
        session.add(row)
        session.flush()
        session.refresh(row, ["domain", "domains"])
        return _definition(row)


def update_definition(database_url: str, definition_id: str, data: Mapping[str, object]) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(NodeDefinitionRow, definition_id)
        if row is None:
            return None
        for field in ("name", "description", "completion_criteria"):
            if field in data and data[field] is not None:
                setattr(row, field, str(data[field]).strip())
        if "domain_id" in data or "domain_ids" in data:
            domain_ids = _definition_domain_ids(data)
            if not domain_ids:
                raise ValueError("domain_ids are required")
            domains = [session.get(DeliveryDomainRow, domain_id) for domain_id in domain_ids]
            if any(domain is None for domain in domains):
                raise ValueError("delivery domain not found")
            row.domain_id = domain_ids[0]
            row.domains = [domain for domain in domains if domain]
        if "active" in data:
            row.active = bool(data["active"])
        session.flush()
        session.refresh(row, ["domain", "domains"])
        return _definition(row)


def deactivate_definition(database_url: str, definition_id: str) -> bool:
    return update_definition(database_url, definition_id, {"active": False}) is not None


def _template_summary(row: WorkflowTemplateRow) -> Dict[str, object]:
    return {"id": row.id, "name": row.name, "description": row.description, "version": row.version, "active": row.active, "node_count": len(row.nodes), "edge_count": len(row.edges), "updated_at": row.updated_at}


def list_templates(database_url: str) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        rows = session.scalars(select(WorkflowTemplateRow).options(selectinload(WorkflowTemplateRow.nodes), selectinload(WorkflowTemplateRow.edges)).order_by(WorkflowTemplateRow.active.desc(), WorkflowTemplateRow.name))
        return [_template_summary(row) for row in rows]


def create_template(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("template name is required")
    with session_scope(database_url) as session:
        if session.scalar(select(WorkflowTemplateRow).where(WorkflowTemplateRow.name == name)):
            raise ValueError("template name already exists")
        row = WorkflowTemplateRow(name=name, description=str(data.get("description") or ""))
        session.add(row)
        session.flush()
        return _template_summary(row)


def update_template(database_url: str, template_id: str, data: Mapping[str, object]) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(WorkflowTemplateRow, template_id)
        if row is None:
            return None
        for field in ("name", "description"):
            if field in data and data[field] is not None:
                setattr(row, field, str(data[field]).strip())
        if "active" in data:
            row.active = bool(data["active"])
        session.flush()
        return _template_summary(row)


def _template_node(row: WorkflowTemplateNodeRow) -> Dict[str, object]:
    domain = row.domain or row.definition.domain
    return {"id": row.id, "definition_id": row.definition_id, "domain_id": domain.id, "name": row.definition.name, "description": row.definition.description, "completion_criteria": row.definition.completion_criteria, "domain": _domain(domain), "domains": [_domain(domain)], "position": {"x": row.position_x, "y": row.position_y}}


def get_template(database_url: str, template_id: str) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.scalar(select(WorkflowTemplateRow).where(WorkflowTemplateRow.id == template_id).options(selectinload(WorkflowTemplateRow.nodes).selectinload(WorkflowTemplateNodeRow.definition).selectinload(NodeDefinitionRow.domain), selectinload(WorkflowTemplateRow.nodes).selectinload(WorkflowTemplateNodeRow.domain), selectinload(WorkflowTemplateRow.edges)))
        if row is None:
            return None
        return {**_template_summary(row), "nodes": [_template_node(node) for node in row.nodes], "edges": [{"id": edge.id, "source": edge.source_node_id, "target": edge.target_node_id} for edge in row.edges]}


def add_template_node(database_url: str, template_id: str, data: Mapping[str, object]) -> Dict[str, object]:
    definition_id = str(data.get("definition_id") or "")
    requested_domain_id = str(data.get("domain_id") or "")
    with session_scope(database_url) as session:
        template = session.get(WorkflowTemplateRow, template_id)
        definition = session.scalar(select(NodeDefinitionRow).where(NodeDefinitionRow.id == definition_id).options(selectinload(NodeDefinitionRow.domain), selectinload(NodeDefinitionRow.domains)))
        if template is None or definition is None:
            raise ValueError("template or node definition not found")
        domain_id = requested_domain_id or definition.domain_id
        domain = session.get(DeliveryDomainRow, domain_id)
        definition_domain_ids = {item.id for item in (definition.domains or [definition.domain])}
        if domain is None or domain_id not in definition_domain_ids:
            raise ValueError("domain is not assigned to this node definition")
        duplicate = session.scalar(select(WorkflowTemplateNodeRow).where(WorkflowTemplateNodeRow.template_id == template_id, WorkflowTemplateNodeRow.definition_id == definition_id, WorkflowTemplateNodeRow.domain_id == domain_id))
        if duplicate is not None:
            raise ValueError("node domain already exists in this template")
        row = WorkflowTemplateNodeRow(template_id=template_id, definition_id=definition_id, domain_id=domain_id, position_x=float(data.get("position_x") or 0), position_y=float(data.get("position_y") or 0))
        session.add(row)
        template.version += 1
        session.flush()
        session.refresh(row, ["definition", "domain"])
        _ = row.definition.domain
        _ = row.definition.domains
        return _template_node(row)


def move_template_node(database_url: str, node_id: str, data: Mapping[str, object]) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(WorkflowTemplateNodeRow, node_id)
        if row is None:
            return None
        row.position_x = float(data.get("position_x", row.position_x))
        row.position_y = float(data.get("position_y", row.position_y))
        session.flush()
        session.refresh(row, ["definition", "domain"])
        _ = row.definition.domain
        _ = row.definition.domains
        return _template_node(row)


def delete_template_node(database_url: str, node_id: str) -> bool:
    with session_scope(database_url) as session:
        row = session.get(WorkflowTemplateNodeRow, node_id)
        if row is None:
            return False
        template = session.get(WorkflowTemplateRow, row.template_id)
        session.delete(row)
        if template:
            template.version += 1
        return True


def _would_cycle(edges: Sequence[WorkflowTemplateEdgeRow], source: str, target: str) -> bool:
    graph: Dict[str, List[str]] = {}
    for edge in edges:
        graph.setdefault(edge.source_node_id, []).append(edge.target_node_id)
    graph.setdefault(source, []).append(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for next_node in graph.get(node, []):
            if visit(next_node):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in list(graph))


def add_template_edge(database_url: str, template_id: str, data: Mapping[str, object]) -> Dict[str, object]:
    source = str(data.get("source") or "")
    target = str(data.get("target") or "")
    if not source or not target or source == target:
        raise ValueError("source and target must be different nodes")
    with session_scope(database_url) as session:
        template = session.scalar(select(WorkflowTemplateRow).where(WorkflowTemplateRow.id == template_id).options(selectinload(WorkflowTemplateRow.edges)))
        source_node = session.get(WorkflowTemplateNodeRow, source)
        target_node = session.get(WorkflowTemplateNodeRow, target)
        if template is None or source_node is None or target_node is None or source_node.template_id != template_id or target_node.template_id != template_id:
            raise ValueError("template node not found")
        if any(edge.source_node_id == source and edge.target_node_id == target for edge in template.edges):
            raise ValueError("edge already exists")
        if _would_cycle(template.edges, source, target):
            raise ValueError("edge would create a cycle")
        row = WorkflowTemplateEdgeRow(template_id=template_id, source_node_id=source, target_node_id=target)
        session.add(row)
        template.version += 1
        session.flush()
        return {"id": row.id, "source": source, "target": target}


def delete_template_edge(database_url: str, edge_id: str) -> bool:
    with session_scope(database_url) as session:
        row = session.get(WorkflowTemplateEdgeRow, edge_id)
        if row is None:
            return False
        template = session.get(WorkflowTemplateRow, row.template_id)
        session.delete(row)
        if template:
            template.version += 1
        return True


def _node_dict(row: RequirementNodeRow) -> Dict[str, object]:
    return {"id": row.id, "requirement_id": row.requirement_id, "definition_id": row.definition_id, "name": row.name, "domain_name": row.domain_name, "domain_color": row.domain_color, "position": {"x": row.position_x, "y": row.position_y}, "planned_start": row.planned_start, "planned_end": row.planned_end, "actual_start": row.actual_start, "actual_end": row.actual_end, "status": row.status, "blocked_reason": row.blocked_reason, "notes": row.notes, "owners": [_person(person) for person in row.owners], "risk_level": row.risk_level, "risk_reasons": list(row.risk_reasons or [])}


def _evaluate_requirement(row: RequirementRow, *, persist: bool = False) -> Dict[str, object]:
    result = evaluate_schedule(row.nodes, row.edges)
    if persist:
        row.risk_level = int(result.level)
        row.risk_reasons = result.reasons
        row.last_evaluated_at = _utc_now()
        for node in row.nodes:
            node_result = result.node_results[node.id]
            node.risk_level = int(node_result.level)
            node.risk_reasons = node_result.reasons
    current = [node for node in row.nodes if node.id in result.current_node_ids]
    return {"risk_level": int(result.level), "risk_reasons": result.reasons, "completed_nodes": result.completed_nodes, "total_nodes": result.total_nodes, "progress": round(result.completed_nodes / result.total_nodes * 100) if result.total_nodes else 0, "current_nodes": [node.name for node in current], "current_node_ids": [node.id for node in current], "planned_completion": result.planned_completion}


def _requirement_dict(row: RequirementRow, *, with_graph: bool = False, persist: bool = False) -> Dict[str, object]:
    schedule = _evaluate_requirement(row, persist=persist)
    schedule_level = int(schedule["risk_level"])
    ai_analysis = dict(row.ai_analysis) if row.ai_analysis else None
    ai_level = {"normal": 0, "warning": 1, "severe": 2}.get(str((ai_analysis or {}).get("risk_level")), 0)
    payload = {"id": row.id, "sequence_id": row.sequence_id, "name": row.name, "owner": _person(row.owner), "owner_id": row.owner_id, "template_id": row.template_id, "template_name": row.template_name, "template_version": row.template_version, "target_version": row.target_version, "meego_url": row.meego_url, "requirement_url": row.requirement_url, "figma_url": row.figma_url, "notes": row.notes, "archived": row.archived, "created_at": row.created_at, "updated_at": row.updated_at, **schedule, "schedule_risk_level": schedule_level, "risk_level": max(schedule_level, ai_level), "ai_risk_level": ai_level if ai_analysis else None, "ai_analysis": ai_analysis, "ai_analyzed_at": row.ai_analyzed_at, "ai_error": row.ai_error}
    graph_nodes = [_node_dict(node) for node in row.nodes]
    graph_edges = [{"id": edge.id, "source": edge.source_node_id, "target": edge.target_node_id} for edge in row.edges]
    analysis_source = {**payload, "nodes": graph_nodes, "edges": graph_edges}
    payload["ai_stale"] = bool(row.ai_input_hash and row.ai_input_hash != input_fingerprint(requirement_ai_input(analysis_source)))
    if with_graph:
        payload["nodes"] = graph_nodes
        payload["edges"] = graph_edges
    return payload


def list_requirements(database_url: str) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        rows = session.scalars(select(RequirementRow).options(selectinload(RequirementRow.owner), selectinload(RequirementRow.nodes).selectinload(RequirementNodeRow.owners), selectinload(RequirementRow.edges)).order_by(RequirementRow.archived, RequirementRow.updated_at.desc()))
        return [_requirement_dict(row, persist=True) for row in rows]


def get_requirement(database_url: str, requirement_id: str) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.scalar(select(RequirementRow).where(RequirementRow.id == requirement_id).options(selectinload(RequirementRow.owner), selectinload(RequirementRow.nodes).selectinload(RequirementNodeRow.owners), selectinload(RequirementRow.edges)))
        return _requirement_dict(row, with_graph=True, persist=True) if row else None


def create_requirement(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    name = str(data.get("name") or "").strip()
    owner_id = str(data.get("owner_id") or "")
    template_id = str(data.get("template_id") or "")
    if not name or not owner_id or not template_id:
        raise ValueError("name, owner_id and template_id are required")
    with session_scope(database_url) as session:
        owner = session.get(PersonRow, owner_id)
        template = session.scalar(select(WorkflowTemplateRow).where(WorkflowTemplateRow.id == template_id).options(selectinload(WorkflowTemplateRow.nodes).selectinload(WorkflowTemplateNodeRow.definition).selectinload(NodeDefinitionRow.domain), selectinload(WorkflowTemplateRow.nodes).selectinload(WorkflowTemplateNodeRow.domain), selectinload(WorkflowTemplateRow.edges)))
        if owner is None or template is None or not template.active:
            raise ValueError("owner or active template not found")
        if not template.nodes:
            raise ValueError("workflow template has no nodes")
        counter = session.scalar(select(RequirementSequenceRow).where(RequirementSequenceRow.id == "default").with_for_update())
        if counter is None:
            next_value = int(session.scalar(select(func.max(RequirementRow.sequence_id))) or 0) + 1
            counter = RequirementSequenceRow(id="default", next_value=next_value + 1)
            session.add(counter)
        else:
            next_value = counter.next_value
            counter.next_value += 1
        row = RequirementRow(sequence_id=next_value, requirement_key=f"internal-{next_value}-{_new_id()}", name=name, owner_id=owner_id, template_id=template.id, template_name=template.name, template_version=template.version, target_version=str(data.get("target_version") or ""), meego_url=str(data.get("meego_url") or "") or None, requirement_url=str(data.get("requirement_url") or "") or None, figma_url=str(data.get("figma_url") or "") or None, notes=str(data.get("notes") or ""))
        session.add(row)
        session.flush()
        node_map: Dict[str, RequirementNodeRow] = {}
        for template_node in template.nodes:
            definition = template_node.definition
            domain = template_node.domain or definition.domain
            node = RequirementNodeRow(requirement_id=row.id, template_node_id=template_node.id, definition_id=definition.id, name=definition.name, domain_name=domain.name, domain_color=domain.color, position_x=template_node.position_x, position_y=template_node.position_y, owners=[owner])
            session.add(node)
            session.flush()
            node_map[template_node.id] = node
        for edge in template.edges:
            session.add(RequirementEdgeRow(requirement_id=row.id, source_node_id=node_map[edge.source_node_id].id, target_node_id=node_map[edge.target_node_id].id))
        session.flush()
        session.refresh(row, ["owner", "nodes", "edges"])
        for node in row.nodes:
            _ = node.owners
        return _requirement_dict(row, with_graph=True, persist=True)


def update_requirement(database_url: str, requirement_id: str, data: Mapping[str, object]) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.scalar(select(RequirementRow).where(RequirementRow.id == requirement_id).options(selectinload(RequirementRow.owner), selectinload(RequirementRow.nodes).selectinload(RequirementNodeRow.owners), selectinload(RequirementRow.edges)))
        if row is None:
            return None
        for field in ("name", "target_version", "notes"):
            if field in data and data[field] is not None:
                setattr(row, field, str(data[field]).strip())
        for field in ("meego_url", "requirement_url", "figma_url"):
            if field in data:
                setattr(row, field, str(data[field] or "").strip() or None)
        if "owner_id" in data:
            owner = session.get(PersonRow, str(data["owner_id"]))
            if owner is None:
                raise ValueError("owner not found")
            row.owner_id = owner.id
        if "archived" in data:
            row.archived = bool(data["archived"])
        session.flush()
        return _requirement_dict(row, with_graph=True)


def update_requirement_node(database_url: str, node_id: str, data: Mapping[str, object]) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.scalar(select(RequirementNodeRow).where(RequirementNodeRow.id == node_id).options(selectinload(RequirementNodeRow.owners)))
        if row is None:
            return None
        if "position_x" in data and data["position_x"] is not None:
            row.position_x = float(data["position_x"])
        if "position_y" in data and data["position_y"] is not None:
            row.position_y = float(data["position_y"])
        for field in ("planned_start", "planned_end"):
            if field in data:
                setattr(row, field, _datetime(data[field]))
        for field in ("blocked_reason", "notes"):
            if field in data:
                setattr(row, field, str(data[field] or ""))
        if "owner_ids" in data:
            owner_ids = [str(item) for item in data["owner_ids"] or []]
            owners = [session.get(PersonRow, owner_id) for owner_id in owner_ids]
            if any(owner is None for owner in owners):
                raise ValueError("node owner not found")
            row.owners = [owner for owner in owners if owner]
        if "status" in data:
            status = str(data["status"])
            if status not in NODE_STATUSES:
                raise ValueError("invalid node status")
            if status == "in_progress" and row.actual_start is None:
                row.actual_start = _utc_now()
            if status == "completed" and row.actual_end is None:
                row.actual_end = _utc_now()
                row.actual_start = row.actual_start or row.actual_end
                row.blocked_reason = ""
            if status != "blocked" and row.status == "blocked":
                row.blocked_reason = ""
            row.status = status
        session.flush()
        requirement = session.get(RequirementRow, row.requirement_id)
        if requirement is not None:
            _evaluate_requirement(requirement, persist=True)
        return _node_dict(row)


def add_requirement_node(database_url: str, requirement_id: str, data: Mapping[str, object]) -> Dict[str, object]:
    definition_id = str(data.get("definition_id") or "")
    requested_domain_id = str(data.get("domain_id") or "")
    with session_scope(database_url) as session:
        requirement = session.scalar(select(RequirementRow).where(RequirementRow.id == requirement_id).options(selectinload(RequirementRow.nodes).selectinload(RequirementNodeRow.owners)))
        definition = session.scalar(select(NodeDefinitionRow).where(NodeDefinitionRow.id == definition_id).options(selectinload(NodeDefinitionRow.domain), selectinload(NodeDefinitionRow.domains)))
        if requirement is None or definition is None:
            raise ValueError("requirement or node definition not found")
        domain_id = requested_domain_id or definition.domain_id
        domain = session.get(DeliveryDomainRow, domain_id)
        allowed_domains = {item.id for item in (definition.domains or [definition.domain])}
        if domain is None or domain_id not in allowed_domains:
            raise ValueError("domain is not assigned to this node definition")
        if any(node.definition_id == definition_id and node.domain_name == domain.name for node in requirement.nodes):
            raise ValueError("node domain already exists in this requirement")
        node = RequirementNodeRow(requirement=requirement, template_node_id=str(data.get("template_node_id") or "") or None, definition_id=definition.id, name=definition.name, domain_name=domain.name, domain_color=domain.color, position_x=float(data.get("position_x") or 0), position_y=float(data.get("position_y") or 0), owners=[requirement.owner])
        session.add(node)
        session.flush()
        session.refresh(node, ["owners"])
        _evaluate_requirement(requirement, persist=True)
        return _node_dict(node)


def delete_requirement_node(database_url: str, node_id: str) -> bool:
    with session_scope(database_url) as session:
        row = session.get(RequirementNodeRow, node_id)
        if row is None:
            return False
        requirement = session.get(RequirementRow, row.requirement_id)
        session.delete(row)
        session.flush()
        if requirement is not None:
            _evaluate_requirement(requirement, persist=True)
        return True


def _would_requirement_cycle(edges: Sequence[RequirementEdgeRow], source: str, target: str) -> bool:
    graph: Dict[str, List[str]] = {}
    for edge in edges:
        graph.setdefault(edge.source_node_id, []).append(edge.target_node_id)
    graph.setdefault(source, []).append(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for next_node in graph.get(node, []):
            if visit(next_node):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in list(graph))


def add_requirement_edge(database_url: str, requirement_id: str, data: Mapping[str, object]) -> Dict[str, object]:
    source = str(data.get("source") or "")
    target = str(data.get("target") or "")
    if not source or not target or source == target:
        raise ValueError("source and target must be different nodes")
    with session_scope(database_url) as session:
        requirement = session.scalar(select(RequirementRow).where(RequirementRow.id == requirement_id).options(selectinload(RequirementRow.edges)))
        source_node = session.get(RequirementNodeRow, source)
        target_node = session.get(RequirementNodeRow, target)
        if requirement is None or source_node is None or target_node is None or source_node.requirement_id != requirement_id or target_node.requirement_id != requirement_id:
            raise ValueError("requirement node not found")
        if any(edge.source_node_id == source and edge.target_node_id == target for edge in requirement.edges):
            raise ValueError("edge already exists")
        if _would_requirement_cycle(requirement.edges, source, target):
            raise ValueError("edge would create a cycle")
        row = RequirementEdgeRow(requirement_id=requirement_id, source_node_id=source, target_node_id=target)
        session.add(row)
        session.flush()
        requirement.edges.append(row)
        _evaluate_requirement(requirement, persist=True)
        return {"id": row.id, "source": source, "target": target}


def delete_requirement_edge(database_url: str, edge_id: str) -> bool:
    with session_scope(database_url) as session:
        row = session.get(RequirementEdgeRow, edge_id)
        if row is None:
            return False
        requirement = session.get(RequirementRow, row.requirement_id)
        session.delete(row)
        session.flush()
        if requirement is not None:
            _evaluate_requirement(requirement, persist=True)
        return True


def evaluate_all_requirements(database_url: str) -> Dict[str, int]:
    with session_scope(database_url) as session:
        rows = list(session.scalars(select(RequirementRow).where(RequirementRow.archived.is_(False)).options(selectinload(RequirementRow.owner), selectinload(RequirementRow.nodes).selectinload(RequirementNodeRow.owners), selectinload(RequirementRow.edges))))
        counts = {"normal": 0, "warning": 0, "severe": 0}
        for row in rows:
            summary = _evaluate_requirement(row, persist=True)
            counts[{0: "normal", 1: "warning", 2: "severe"}[summary["risk_level"]]] += 1
        return counts


def dashboard_summary(database_url: str) -> Dict[str, object]:
    requirements = [row for row in list_requirements(database_url) if not row["archived"]]
    return {"counts": {"requirements": len(requirements), "normal": sum(row["risk_level"] == 0 for row in requirements), "warning": sum(row["risk_level"] == 1 for row in requirements), "severe": sum(row["risk_level"] == 2 for row in requirements), "active_nodes": sum(bool(row["current_nodes"]) for row in requirements)}, "requirements": requirements[:12], "jobs": list_jobs(database_url), "notifications": list_notifications(database_url, 10)}


def _job(row: ScheduledJobRow) -> Dict[str, object]:
    return {"id": row.id, "name": row.name, "job_type": row.job_type, "schedule_kind": row.schedule_kind or "interval", "cron_expression": row.cron_expression, "timezone": row.timezone or "Asia/Shanghai", "interval_seconds": row.interval_seconds, "notification_scope": row.notification_scope or "risk_only", "enabled": row.enabled, "next_run_at": _api_datetime(row.next_run_at), "last_run_at": _api_datetime(row.last_run_at), "last_status": row.last_status}


def list_jobs(database_url: str) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        return [_job(row) for row in session.scalars(select(ScheduledJobRow).order_by(ScheduledJobRow.name))]


def create_job(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    name = str(data.get("name") or "").strip()
    job_type = str(data.get("job_type") or "risk_scan")
    if not name or job_type not in JOB_TYPES:
        raise ValueError("invalid job")
    with session_scope(database_url) as session:
        row = session.scalar(select(ScheduledJobRow).where(ScheduledJobRow.name == name))
        if row is None:
            row = ScheduledJobRow(name=name, job_type=job_type)
            session.add(row)
        row.job_type = job_type
        row.schedule_kind = str(data.get("schedule_kind") or row.schedule_kind or "interval")
        if row.schedule_kind not in SCHEDULE_KINDS:
            raise ValueError("invalid schedule kind")
        row.timezone = str(data.get("timezone") or row.timezone or "Asia/Shanghai")
        row.cron_expression = str(data.get("cron_expression") or row.cron_expression or "").strip() or None
        if row.schedule_kind == "cron":
            if not row.cron_expression:
                raise ValueError("cron_expression is required")
            validate_cron(row.cron_expression, row.timezone)
        row.interval_seconds = max(10, int(data.get("interval_seconds") or 86400))
        row.notification_scope = str(data.get("notification_scope") or row.notification_scope or "risk_only")
        if row.notification_scope not in NOTIFICATION_SCOPES:
            raise ValueError("invalid notification scope")
        row.enabled = bool(data.get("enabled", True))
        if row.schedule_kind == "cron" and row.cron_expression:
            row.next_run_at = next_cron_run(row.cron_expression, row.timezone)
        else:
            row.next_run_at = _datetime(data.get("next_run_at")) or _utc_now()
        session.flush()
        return _job(row)


def update_job(database_url: str, job_id: str, data: Mapping[str, object]) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(ScheduledJobRow, job_id)
        if row is None:
            return None
        if "name" in data and data["name"] is not None:
            name = str(data["name"]).strip()
            if not name:
                raise ValueError("job name is required")
            row.name = name
        if "job_type" in data and data["job_type"] is not None:
            job_type = str(data["job_type"])
            if job_type not in JOB_TYPES:
                raise ValueError("invalid job")
            row.job_type = job_type
        if "interval_seconds" in data and data["interval_seconds"] is not None:
            row.interval_seconds = max(10, int(data["interval_seconds"]))
        if "notification_scope" in data and data["notification_scope"] is not None:
            notification_scope = str(data["notification_scope"])
            if notification_scope not in NOTIFICATION_SCOPES:
                raise ValueError("invalid notification scope")
            row.notification_scope = notification_scope
        if "schedule_kind" in data and data["schedule_kind"] is not None:
            schedule_kind = str(data["schedule_kind"])
            if schedule_kind not in SCHEDULE_KINDS:
                raise ValueError("invalid schedule kind")
            row.schedule_kind = schedule_kind
        if "timezone" in data and data["timezone"] is not None:
            row.timezone = str(data["timezone"] or "Asia/Shanghai")
        if "cron_expression" in data:
            row.cron_expression = str(data["cron_expression"] or "").strip() or None
        if (row.schedule_kind or "interval") == "cron":
            if not row.cron_expression:
                raise ValueError("cron_expression is required")
            validate_cron(row.cron_expression, row.timezone or "Asia/Shanghai")
        if "enabled" in data and data["enabled"] is not None:
            row.enabled = bool(data["enabled"])
        if (row.schedule_kind or "interval") == "cron":
            if any(field in data for field in ("schedule_kind", "cron_expression", "timezone", "next_run_at")):
                row.next_run_at = next_cron_run(row.cron_expression, row.timezone or "Asia/Shanghai")
        elif "next_run_at" in data:
            row.next_run_at = _datetime(data["next_run_at"]) or _utc_now()
        session.flush()
        return _job(row)


def trigger_job(database_url: str, job_id: str) -> Optional[Dict[str, object]]:
    with session_scope(database_url) as session:
        row = session.get(ScheduledJobRow, job_id)
        if row is None:
            return None
        now = _utc_now()
        row.enabled = True
        row.next_run_at = now
        if row.job_type == "outbox_delivery":
            retryable = session.scalars(select(NotificationOutboxRow).where(NotificationOutboxRow.status.in_(["pending", "dead"])))
            for notification in retryable:
                if notification.status == "dead":
                    notification.status = "pending"
                    notification.attempt_count = 0
                    notification.last_error = None
                notification.available_at = now
        session.flush()
        return _job(row)


def enqueue_notification(database_url: str, payload: Mapping[str, object], *, deduplication_key: Optional[str] = None) -> Dict[str, object]:
    dedup = deduplication_key or hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    with session_scope(database_url) as session:
        existing = session.scalar(select(NotificationOutboxRow).where(NotificationOutboxRow.deduplication_key == dedup))
        if existing:
            return {"id": existing.id, "status": existing.status, "duplicate": True}
        row = NotificationOutboxRow(deduplication_key=dedup, payload=dict(payload))
        session.add(row)
        session.flush()
        return {"id": row.id, "status": row.status, "duplicate": False}


def list_notifications(database_url: str, limit: int = 100) -> List[Dict[str, object]]:
    with session_scope(database_url) as session:
        rows = session.scalars(select(NotificationOutboxRow).order_by(NotificationOutboxRow.created_at.desc()).limit(limit))
        return [{"id": row.id, "status": row.status, "attempt_count": row.attempt_count, "last_error": row.last_error, "available_at": row.available_at, "sent_at": row.sent_at, "created_at": row.created_at} for row in rows]


def _masked_url(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    token = value.rsplit("/", 1)[-1]
    suffix = token[-6:] if len(token) >= 6 else token
    return f"••••••••{suffix}"


def get_webhook_settings(database_url: str, *, include_secrets: bool = False) -> Dict[str, object]:
    with session_scope(database_url) as session:
        row = session.get(WebhookSettingsRow, "default")
        if row is None:
            row = WebhookSettingsRow(id="default")
            session.add(row)
            session.flush()
        test_url = row.test_webhook_url if include_secrets else _masked_url(row.test_webhook_url)
        prod_url = row.prod_webhook_url if include_secrets else _masked_url(row.prod_webhook_url)
        return {
            "enabled": row.enabled,
            "runtime_environment": row.runtime_environment,
            "test_webhook_url": test_url,
            "prod_webhook_url": prod_url,
            "test_configured": bool(row.test_webhook_url),
            "prod_configured": bool(row.prod_webhook_url),
            "bot_keyword": row.bot_keyword,
            "updated_at": row.updated_at,
        }


def update_webhook_settings(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    with session_scope(database_url) as session:
        row = session.get(WebhookSettingsRow, "default")
        if row is None:
            row = WebhookSettingsRow(id="default")
            session.add(row)
        if "runtime_environment" in data:
            environment = str(data["runtime_environment"])
            if environment not in {"test", "prod"}:
                raise ValueError("runtime_environment must be test or prod")
            row.runtime_environment = environment
        for field in ("test_webhook_url", "prod_webhook_url"):
            if field not in data:
                continue
            value = str(data[field] or "").strip() or None
            if value and not is_allowed_webhook_url(value):
                raise ValueError("Webhook URL must use an official Feishu/Lark endpoint")
            setattr(row, field, value)
        if "bot_keyword" in data:
            row.bot_keyword = str(data["bot_keyword"] or "").strip()
        if "enabled" in data:
            row.enabled = bool(data["enabled"])
        session.flush()
    return get_webhook_settings(database_url)


def _masked_key(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return "••••••••" + value[-4:]


def get_ai_settings(database_url: str, *, include_secrets: bool = False) -> Dict[str, object]:
    with session_scope(database_url) as session:
        row = session.get(AISettingsRow, "default")
        if row is None:
            row = AISettingsRow(id="default", prompt=DEFAULT_AI_PROMPT)
            session.add(row)
            session.flush()
        return {
            "enabled": row.enabled,
            "provider": row.provider,
            "base_url": row.base_url,
            "api_key": row.api_key if include_secrets else _masked_key(row.api_key),
            "api_key_configured": bool(row.api_key),
            "model": row.model,
            "prompt": row.prompt or DEFAULT_AI_PROMPT,
            "include_in_feishu": row.include_in_feishu,
            "auto_analyze": row.auto_analyze,
            "updated_at": row.updated_at,
        }


def update_ai_settings(database_url: str, data: Mapping[str, object]) -> Dict[str, object]:
    with session_scope(database_url) as session:
        row = session.get(AISettingsRow, "default")
        if row is None:
            row = AISettingsRow(id="default", prompt=DEFAULT_AI_PROMPT)
            session.add(row)
        if "provider" in data:
            provider = str(data["provider"])
            if provider not in {"chatgpt_plus", "openai_compatible"}:
                raise ValueError("provider must be chatgpt_plus or openai_compatible")
            row.provider = provider
        if "base_url" in data:
            base_url = str(data["base_url"] or "").strip().rstrip("/")
            if not base_url.startswith(("https://", "http://")):
                raise ValueError("Base URL 必须是 HTTP(S) 地址")
            row.base_url = base_url
        if "api_key" in data:
            row.api_key = str(data["api_key"] or "").strip() or None
        if "model" in data:
            model = str(data["model"] or "").strip()
            if not model:
                raise ValueError("模型不能为空")
            row.model = model
        if "prompt" in data:
            row.prompt = str(data["prompt"] or "").strip() or DEFAULT_AI_PROMPT
        for field in ("enabled", "include_in_feishu", "auto_analyze"):
            if field in data:
                setattr(row, field, bool(data[field]))
        session.flush()
    return get_ai_settings(database_url)


def analyze_requirement(database_url: str, requirement_id: str, *, force: bool = False) -> Dict[str, object]:
    settings = get_ai_settings(database_url, include_secrets=True)
    if not settings["enabled"]:
        raise ValueError("AI 分析尚未开启")
    requirement = get_requirement(database_url, requirement_id)
    if requirement is None:
        raise LookupError("requirement not found")
    payload = requirement_ai_input(requirement)
    fingerprint = input_fingerprint(payload)
    if not force and requirement.get("ai_analysis") and not requirement.get("ai_stale"):
        return requirement
    try:
        if settings["provider"] == "chatgpt_plus":
            analysis = analyze_with_chatgpt_plus(model=str(settings["model"]), prompt=str(settings["prompt"]), payload=payload)
        else:
            analysis = analyze_with_compatible_api(base_url=str(settings["base_url"]), api_key=str(settings["api_key"] or ""), model=str(settings["model"]), prompt=str(settings["prompt"]), payload=payload)
        schedule_level = int(requirement.get("schedule_risk_level") or 0)
        names = ["normal", "warning", "severe"]
        analysis["risk_level"] = names[max(schedule_level, names.index(str(analysis["risk_level"])))]
        with session_scope(database_url) as session:
            row = session.get(RequirementRow, requirement_id)
            if row is None:
                raise LookupError("requirement not found")
            row.ai_analysis = analysis
            row.ai_analyzed_at = _utc_now()
            row.ai_input_hash = fingerprint
            row.ai_error = None
    except Exception as error:
        with session_scope(database_url) as session:
            row = session.get(RequirementRow, requirement_id)
            if row is not None:
                row.ai_error = str(error)[:1000]
        raise
    result = get_requirement(database_url, requirement_id)
    assert result is not None
    return result


def analyze_all_requirements(database_url: str) -> Dict[str, int]:
    settings = get_ai_settings(database_url)
    counts = {"analyzed": 0, "skipped": 0, "failed": 0}
    if not settings["enabled"] or not settings["auto_analyze"]:
        return counts
    for requirement in list_requirements(database_url):
        if requirement["archived"]:
            continue
        try:
            before = requirement.get("ai_analyzed_at")
            result = analyze_requirement(database_url, str(requirement["id"]))
            counts["analyzed" if result.get("ai_analyzed_at") != before else "skipped"] += 1
        except Exception:
            counts["failed"] += 1
    return counts


def seed_demo(database_url: str) -> Dict[str, object]:
    if list_templates(database_url):
        return dashboard_summary(database_url)["counts"]
    owner = create_person(database_url, {"display_name": "林夏", "role_name": "需求负责人"})
    client_owner = create_person(database_url, {"display_name": "周屿", "role_name": "客户端研发"})
    server_owner = create_person(database_url, {"display_name": "沈言", "role_name": "服务端研发"})
    test_owner = create_person(database_url, {"display_name": "陈默", "role_name": "测试负责人"})
    product = create_domain(database_url, {"name": "产品", "color": "#7c5ce5", "sort_order": 1})
    client = create_domain(database_url, {"name": "客户端", "color": "#3478c7", "sort_order": 2})
    server = create_domain(database_url, {"name": "服务端", "color": "#2f7d57", "sort_order": 3})
    testing = create_domain(database_url, {"name": "测试", "color": "#d27a24", "sort_order": 4})
    update_person(database_url, owner["id"], {"domain_id": product["id"]})
    update_person(database_url, client_owner["id"], {"domain_id": client["id"]})
    update_person(database_url, server_owner["id"], {"domain_id": server["id"]})
    update_person(database_url, test_owner["id"], {"domain_id": testing["id"]})
    definitions = {}
    for name, domain, criteria in (("需求评审", product, "需求范围和验收标准已确认"), ("客户端开发", client, "客户端代码完成并自测通过"), ("服务端开发", server, "接口开发完成并通过单元测试"), ("联调", testing, "端到端主流程联调通过"), ("AT 测试", testing, "AT 测试用例全部执行完成")):
        definitions[name] = create_definition(database_url, {"name": name, "domain_id": domain["id"], "completion_criteria": criteria})
    template = create_template(database_url, {"name": "标准需求交付流程", "description": "评审后客户端和服务端并行开发，再进入联调和 AT 测试"})
    positions = {"需求评审": (40, 180), "客户端开发": (300, 70), "服务端开发": (300, 290), "联调": (580, 180), "AT 测试": (840, 180)}
    graph_nodes = {name: add_template_node(database_url, template["id"], {"definition_id": definition["id"], "position_x": positions[name][0], "position_y": positions[name][1]}) for name, definition in definitions.items()}
    for source, target in (("需求评审", "客户端开发"), ("需求评审", "服务端开发"), ("客户端开发", "联调"), ("服务端开发", "联调"), ("联调", "AT 测试")):
        add_template_edge(database_url, template["id"], {"source": graph_nodes[source]["id"], "target": graph_nodes[target]["id"]})
    requirement = create_requirement(database_url, {"name": "行程卡片体验升级", "owner_id": owner["id"], "template_id": template["id"], "target_version": "v4.8.0", "notes": "客户端和服务端并行交付。"})
    now = _utc_now()
    details = get_requirement(database_url, requirement["id"])
    assert details is not None
    node_by_name = {node["name"]: node for node in details["nodes"]}
    schedule = {
        "需求评审": (now - timedelta(days=6), now - timedelta(days=5), "completed", [owner["id"]]),
        "客户端开发": (now - timedelta(days=4), now + timedelta(days=1), "in_progress", [client_owner["id"]]),
        "服务端开发": (now - timedelta(days=4), now + timedelta(days=2), "in_progress", [server_owner["id"]]),
        "联调": (now + timedelta(days=1), now + timedelta(days=3), "not_started", [client_owner["id"], server_owner["id"]]),
        "AT 测试": (now + timedelta(days=3), now + timedelta(days=5), "not_started", [test_owner["id"]]),
    }
    for name, (start, end, status, owners) in schedule.items():
        update_requirement_node(database_url, node_by_name[name]["id"], {"planned_start": start, "planned_end": end, "status": status, "owner_ids": owners})
    evaluate_all_requirements(database_url)
    create_job(database_url, {"name": "风险扫描", "job_type": "risk_scan", "interval_seconds": 3600})
    create_job(database_url, {"name": "通知投递", "job_type": "outbox_delivery", "interval_seconds": 30})
    return dashboard_summary(database_url)["counts"]
