from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


def _new_id() -> str:
    return uuid.uuid4().hex


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class PersonRow(Base):
    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    display_name: Mapped[str] = mapped_column(String(255), index=True)
    feishu_open_id: Mapped[Optional[str]] = mapped_column(String(128), unique=True)
    email: Mapped[Optional[str]] = mapped_column(String(320))
    role_name: Mapped[str] = mapped_column(String(255), default="")
    domain_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("delivery_domains.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)

    domain: Mapped[Optional["DeliveryDomainRow"]] = relationship()


class DeliveryDomainRow(Base):
    __tablename__ = "delivery_domains"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    color: Mapped[str] = mapped_column(String(16), default="#2f7d57")
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)

    definitions: Mapped[List["NodeDefinitionRow"]] = relationship(back_populates="domain")


class NodeDefinitionRow(Base):
    __tablename__ = "node_definitions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    domain_id: Mapped[str] = mapped_column(String(32), ForeignKey("delivery_domains.id", ondelete="RESTRICT"))
    description: Mapped[str] = mapped_column(Text, default="")
    completion_criteria: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)

    domain: Mapped[DeliveryDomainRow] = relationship(back_populates="definitions")
    domains: Mapped[List[DeliveryDomainRow]] = relationship(secondary="node_definition_domains")


class NodeDefinitionDomainRow(Base):
    __tablename__ = "node_definition_domains"

    definition_id: Mapped[str] = mapped_column(String(32), ForeignKey("node_definitions.id", ondelete="CASCADE"), primary_key=True)
    domain_id: Mapped[str] = mapped_column(String(32), ForeignKey("delivery_domains.id", ondelete="RESTRICT"), primary_key=True)


class WorkflowTemplateRow(Base):
    __tablename__ = "workflow_templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)

    nodes: Mapped[List["WorkflowTemplateNodeRow"]] = relationship(back_populates="template", cascade="all, delete-orphan")
    edges: Mapped[List["WorkflowTemplateEdgeRow"]] = relationship(back_populates="template", cascade="all, delete-orphan")


class WorkflowTemplateNodeRow(Base):
    __tablename__ = "workflow_template_nodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    template_id: Mapped[str] = mapped_column(String(32), ForeignKey("workflow_templates.id", ondelete="CASCADE"), index=True)
    definition_id: Mapped[str] = mapped_column(String(32), ForeignKey("node_definitions.id", ondelete="RESTRICT"))
    position_x: Mapped[float] = mapped_column(Float, default=0)
    position_y: Mapped[float] = mapped_column(Float, default=0)

    template: Mapped[WorkflowTemplateRow] = relationship(back_populates="nodes")
    definition: Mapped[NodeDefinitionRow] = relationship()


class WorkflowTemplateEdgeRow(Base):
    __tablename__ = "workflow_template_edges"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    template_id: Mapped[str] = mapped_column(String(32), ForeignKey("workflow_templates.id", ondelete="CASCADE"), index=True)
    source_node_id: Mapped[str] = mapped_column(String(32), ForeignKey("workflow_template_nodes.id", ondelete="CASCADE"))
    target_node_id: Mapped[str] = mapped_column(String(32), ForeignKey("workflow_template_nodes.id", ondelete="CASCADE"))

    template: Mapped[WorkflowTemplateRow] = relationship(back_populates="edges")
    __table_args__ = (UniqueConstraint("template_id", "source_node_id", "target_node_id", name="uq_template_edge"),)


class RequirementRow(Base):
    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    sequence_id: Mapped[Optional[int]] = mapped_column(Integer, unique=True, index=True)
    requirement_key: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(500))
    owner_id: Mapped[str] = mapped_column(String(32), ForeignKey("people.id", ondelete="RESTRICT"))
    template_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("workflow_templates.id", ondelete="SET NULL"))
    template_name: Mapped[str] = mapped_column(String(255), default="")
    template_version: Mapped[Optional[int]] = mapped_column(Integer)
    target_version: Mapped[str] = mapped_column(String(255), default="")
    meego_url: Mapped[Optional[str]] = mapped_column(Text)
    requirement_url: Mapped[Optional[str]] = mapped_column(Text)
    figma_url: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_level: Mapped[int] = mapped_column(Integer, default=0)
    risk_reasons: Mapped[List[str]] = mapped_column(JSON, default=list)
    last_evaluated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ai_analysis: Mapped[Optional[Dict[str, object]]] = mapped_column(JSON)
    ai_analyzed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ai_input_hash: Mapped[Optional[str]] = mapped_column(String(64))
    ai_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)

    owner: Mapped[PersonRow] = relationship()
    template: Mapped[Optional[WorkflowTemplateRow]] = relationship()
    nodes: Mapped[List["RequirementNodeRow"]] = relationship(back_populates="requirement", cascade="all, delete-orphan")
    edges: Mapped[List["RequirementEdgeRow"]] = relationship(back_populates="requirement", cascade="all, delete-orphan")


class RequirementSequenceRow(Base):
    __tablename__ = "requirement_sequence"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="default")
    next_value: Mapped[int] = mapped_column(Integer, default=1)


class RequirementNodeRow(Base):
    __tablename__ = "requirement_nodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    requirement_id: Mapped[str] = mapped_column(String(32), ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    template_node_id: Mapped[Optional[str]] = mapped_column(String(32))
    definition_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("node_definitions.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(255))
    domain_name: Mapped[str] = mapped_column(String(255))
    domain_color: Mapped[str] = mapped_column(String(16), default="#2f7d57")
    position_x: Mapped[float] = mapped_column(Float, default=0)
    position_y: Mapped[float] = mapped_column(Float, default=0)
    planned_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="not_started")
    blocked_reason: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    risk_level: Mapped[int] = mapped_column(Integer, default=0)
    risk_reasons: Mapped[List[str]] = mapped_column(JSON, default=list)

    requirement: Mapped[RequirementRow] = relationship(back_populates="nodes")
    definition: Mapped[Optional[NodeDefinitionRow]] = relationship()
    owners: Mapped[List[PersonRow]] = relationship(secondary="requirement_node_people")


class RequirementNodePersonRow(Base):
    __tablename__ = "requirement_node_people"

    node_id: Mapped[str] = mapped_column(String(32), ForeignKey("requirement_nodes.id", ondelete="CASCADE"), primary_key=True)
    person_id: Mapped[str] = mapped_column(String(32), ForeignKey("people.id", ondelete="RESTRICT"), primary_key=True)


class RequirementEdgeRow(Base):
    __tablename__ = "requirement_edges"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    requirement_id: Mapped[str] = mapped_column(String(32), ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    source_node_id: Mapped[str] = mapped_column(String(32), ForeignKey("requirement_nodes.id", ondelete="CASCADE"))
    target_node_id: Mapped[str] = mapped_column(String(32), ForeignKey("requirement_nodes.id", ondelete="CASCADE"))

    requirement: Mapped[RequirementRow] = relationship(back_populates="edges")
    __table_args__ = (UniqueConstraint("requirement_id", "source_node_id", "target_node_id", name="uq_requirement_edge"),)


class ScheduledJobRow(Base):
    __tablename__ = "scheduled_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    job_type: Mapped[str] = mapped_column(String(64))
    interval_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[Optional[str]] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class JobRunRow(Base):
    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    job_id: Mapped[str] = mapped_column(String(32), ForeignKey("scheduled_jobs.id", ondelete="CASCADE"))
    run_key: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    result_summary: Mapped[Dict[str, object]] = mapped_column(JSON, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class NotificationOutboxRow(Base):
    __tablename__ = "notification_outbox"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    deduplication_key: Mapped[str] = mapped_column(String(500), unique=True)
    payload: Mapped[Dict[str, object]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=6)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class NotificationDeliveryRow(Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    outbox_id: Mapped[str] = mapped_column(String(32), ForeignKey("notification_outbox.id", ondelete="CASCADE"))
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    status_code: Mapped[Optional[int]] = mapped_column(Integer)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class WebhookSettingsRow(Base):
    __tablename__ = "webhook_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    runtime_environment: Mapped[str] = mapped_column(String(16), default="test")
    test_webhook_url: Mapped[Optional[str]] = mapped_column(Text)
    prod_webhook_url: Mapped[Optional[str]] = mapped_column(Text)
    bot_keyword: Mapped[str] = mapped_column(String(255), default="需求交付提醒")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)


class AISettingsRow(Base):
    __tablename__ = "ai_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str] = mapped_column(String(32), default="chatgpt_plus")
    base_url: Mapped[str] = mapped_column(Text, default="https://api.openai.com/v1")
    api_key: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(128), default="gpt-5.4")
    prompt: Mapped[str] = mapped_column(Text, default="")
    include_in_feishu: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_analyze: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)


def normalize_database_url(database_url: str) -> str:
    normalized = database_url.strip()
    if normalized.startswith("postgres://"):
        return "postgresql+psycopg://" + normalized[len("postgres://"):]
    if normalized.startswith("postgresql://"):
        return "postgresql+psycopg://" + normalized[len("postgresql://"):]
    return normalized


@lru_cache(maxsize=16)
def create_database_engine(database_url: str, echo: bool = False) -> Engine:
    normalized = normalize_database_url(database_url)
    connect_args = {}
    if normalized.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" not in normalized:
            database_path = normalized.split(":///", 1)[-1].split("?", 1)[0]
            Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(normalized, echo=echo, pool_pre_ping=True, connect_args=connect_args)
    if normalized.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def initialize_database(database_url: str, *, echo: bool = False) -> None:
    engine = create_database_engine(database_url, echo)
    Base.metadata.create_all(engine)
    # Compatibility migration for databases created before people had a domain.
    columns = {column["name"] for column in inspect(engine).get_columns("people")}
    if "domain_id" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE people ADD COLUMN domain_id VARCHAR(32)"))
    requirement_columns = {column["name"] for column in inspect(engine).get_columns("requirements")}
    if "sequence_id" not in requirement_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE requirements ADD COLUMN sequence_id INTEGER"))
            rows = connection.execute(text("SELECT id FROM requirements ORDER BY created_at, id")).fetchall()
            for sequence_id, row in enumerate(rows, start=1):
                connection.execute(text("UPDATE requirements SET sequence_id=:sequence_id WHERE id=:id"), {"sequence_id": sequence_id, "id": row[0]})
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_requirements_sequence_id ON requirements (sequence_id)"))
        requirement_columns.add("sequence_id")
    missing_links = {"meego_url", "requirement_url", "figma_url"} - requirement_columns
    if missing_links:
        with engine.begin() as connection:
            for column in sorted(missing_links):
                connection.execute(text(f"ALTER TABLE requirements ADD COLUMN {column} TEXT"))
    requirement_columns = {column["name"] for column in inspect(engine).get_columns("requirements")}
    ai_columns = {
        "ai_analysis": "JSON",
        "ai_analyzed_at": "TIMESTAMP",
        "ai_input_hash": "VARCHAR(64)",
        "ai_error": "TEXT",
    }
    with engine.begin() as connection:
        for column, sql_type in ai_columns.items():
            if column not in requirement_columns:
                connection.execute(text(f"ALTER TABLE requirements ADD COLUMN {column} {sql_type}"))
        connection.execute(text("INSERT OR IGNORE INTO node_definition_domains (definition_id, domain_id) SELECT id, domain_id FROM node_definitions WHERE domain_id IS NOT NULL")) if engine.dialect.name == "sqlite" else connection.execute(text("INSERT INTO node_definition_domains (definition_id, domain_id) SELECT id, domain_id FROM node_definitions WHERE domain_id IS NOT NULL ON CONFLICT DO NOTHING"))


@contextmanager
def session_scope(database_url: str) -> Iterator[Session]:
    factory = sessionmaker(bind=create_database_engine(database_url), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
