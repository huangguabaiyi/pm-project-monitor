from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Table, Text, UniqueConstraint, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .models import BaseConfig, Blocker, DataSnapshot, DeliveryNode, NodeStatus, Person, ProjectConfig, Requirement, RiskLevel


def _new_id() -> str:
    return uuid.uuid4().hex


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


node_people = Table(
    "requirement_node_people",
    Base.metadata,
    Column("node_id", String(32), ForeignKey("requirement_nodes.id", ondelete="CASCADE"), primary_key=True),
    Column("person_id", String(32), ForeignKey("people.id", ondelete="RESTRICT"), primary_key=True),
)


class PersonRow(Base):
    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    feishu_open_id: Mapped[Optional[str]] = mapped_column(String(128), unique=True)
    feishu_user_id: Mapped[Optional[str]] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    email: Mapped[Optional[str]] = mapped_column(String(320))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    source_key: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)

    requirements: Mapped[List["RequirementRow"]] = relationship(back_populates="project")
    config: Mapped[Optional["ProjectConfigRow"]] = relationship(back_populates="project", uselist=False)


class RequirementRow(Base):
    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    source_record_id: Mapped[str] = mapped_column(String(128), unique=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="RESTRICT"))
    requirement_key: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(500))
    okr_target: Mapped[str] = mapped_column(String(500))
    current_stage: Mapped[str] = mapped_column(String(255))
    target_version: Mapped[str] = mapped_column(String(255))
    requirement_doc_url: Mapped[Optional[str]] = mapped_column(Text)
    meego_url: Mapped[Optional[str]] = mapped_column(Text)
    translation_url: Mapped[Optional[str]] = mapped_column(Text)
    merge_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    launch_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    project_owner_id: Mapped[str] = mapped_column(String(32), ForeignKey("people.id", ondelete="RESTRICT"))
    product_owner_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("people.id", ondelete="RESTRICT"))
    briefing_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    notification_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    source_project_config_record_id: Mapped[Optional[str]] = mapped_column(String(128))
    notes: Mapped[str] = mapped_column(Text, default="")
    system_risk_level: Mapped[int] = mapped_column(Integer, default=int(RiskLevel.NORMAL))
    system_risk_reasons: Mapped[List[str]] = mapped_column(JSON, default=list)
    predicted_completion_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    buffer_days: Mapped[Optional[float]] = mapped_column()
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)

    project: Mapped[ProjectRow] = relationship(back_populates="requirements")
    project_owner: Mapped[PersonRow] = relationship(foreign_keys=[project_owner_id])
    product_owner: Mapped[Optional[PersonRow]] = relationship(foreign_keys=[product_owner_id])
    nodes: Mapped[List["RequirementNodeRow"]] = relationship(back_populates="requirement", cascade="all, delete-orphan")
    blockers: Mapped[List["BlockerRow"]] = relationship(back_populates="requirement", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("project_id", "requirement_key", name="uq_requirement_project_key"),)


class RequirementNodeRow(Base):
    __tablename__ = "requirement_nodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    source_record_id: Mapped[str] = mapped_column(String(128), unique=True)
    requirement_id: Mapped[str] = mapped_column(String(32), ForeignKey("requirements.id", ondelete="CASCADE"))
    domain: Mapped[str] = mapped_column(String(255))
    work_type: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(500))
    planned_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(64))
    progress_note: Mapped[str] = mapped_column(Text, default="")
    updated_at_source: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    risk_level: Mapped[int] = mapped_column(Integer, default=int(RiskLevel.NORMAL))
    risk_reasons: Mapped[List[str]] = mapped_column(JSON, default=list)
    safe_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    requirement: Mapped[RequirementRow] = relationship(back_populates="nodes")
    owners: Mapped[List[PersonRow]] = relationship(secondary=node_people)


class BlockerRow(Base):
    __tablename__ = "blockers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    source_record_id: Mapped[str] = mapped_column(String(128), unique=True)
    requirement_id: Mapped[str] = mapped_column(String(32), ForeignKey("requirements.id", ondelete="CASCADE"))
    node_source_record_id: Mapped[Optional[str]] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(500))
    owner_id: Mapped[str] = mapped_column(String(32), ForeignKey("people.id", ondelete="RESTRICT"))
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    planned_resolution_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_resolution_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(64))
    affects_merge: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution_note: Mapped[str] = mapped_column(Text, default="")

    requirement: Mapped[RequirementRow] = relationship(back_populates="blockers")
    owner: Mapped[PersonRow] = relationship()


class ProjectConfigRow(Base):
    __tablename__ = "project_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    source_record_id: Mapped[str] = mapped_column(String(128), unique=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    duration_mode: Mapped[str] = mapped_column(String(32))
    at1_days: Mapped[Optional[int]] = mapped_column(Integer)
    at2_days: Mapped[Optional[int]] = mapped_column(Integer)
    pv1_days: Mapped[Optional[int]] = mapped_column(Integer)
    pv2_days: Mapped[Optional[int]] = mapped_column(Integer)
    regression_days: Mapped[Optional[int]] = mapped_column(Integer)
    launch_weekdays: Mapped[Optional[List[int]]] = mapped_column(JSON)
    launch_cutoff: Mapped[Optional[str]] = mapped_column(String(16))
    llm_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")

    project: Mapped[ProjectRow] = relationship(back_populates="config")


class BaseConfigRow(Base):
    __tablename__ = "base_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    source_record_id: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    config_type: Mapped[str] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class MigrationRunRow(Base):
    __tablename__ = "migration_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    source: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32))
    imported_counts: Mapped[Dict[str, int]] = mapped_column(JSON, default=dict)
    validation_issues: Mapped[List[Dict[str, object]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class ScheduledJobRow(Base):
    __tablename__ = "scheduled_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    job_type: Mapped[str] = mapped_column(String(64))
    interval_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    target_project_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("projects.id", ondelete="SET NULL"))
    payload: Mapped[Dict[str, object]] = mapped_column(JSON, default=dict)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[Optional[str]] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)


class JobRunRow(Base):
    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    job_id: Mapped[str] = mapped_column(String(32), ForeignKey("scheduled_jobs.id", ondelete="CASCADE"))
    run_key: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    result_summary: Mapped[Dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class NotificationOutboxRow(Base):
    __tablename__ = "notification_outbox"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    run_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("job_runs.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(String(64), default="default")
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
    response_body: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


def normalize_database_url(database_url: str) -> str:
    normalized = database_url.strip()
    if normalized.startswith("postgres://"):
        return "postgresql+psycopg://" + normalized[len("postgres://") :]
    if normalized.startswith("postgresql://"):
        return "postgresql+psycopg://" + normalized[len("postgresql://") :]
    return normalized


def create_database_engine(database_url: str, *, echo: bool = False) -> Engine:
    return create_engine(normalize_database_url(database_url), echo=echo, pool_pre_ping=True)


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=create_database_engine(database_url), expire_on_commit=False)


def initialize_database(database_url: str, *, echo: bool = False) -> None:
    engine = create_database_engine(database_url, echo=echo)
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(database_url: str) -> Iterator[Session]:
    factory = create_session_factory(database_url)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _ensure_person(session: Session, person: Person) -> PersonRow:
    row = session.scalar(select(PersonRow).where(PersonRow.feishu_open_id == person.open_id))
    if row is None:
        row = PersonRow(feishu_open_id=person.open_id, display_name=person.name)
        session.add(row)
        session.flush()
    else:
        row.display_name = person.name
        row.active = True
    return row


def _all_people(snapshot: DataSnapshot) -> Iterable[Person]:
    seen = set()
    for requirement in snapshot.requirements:
        people = [
            Person(open_id=requirement.project_owner_id, name=requirement.project_owner_name)
        ]
        if requirement.product_owner_id and requirement.product_owner_name:
            people.append(Person(open_id=requirement.product_owner_id, name=requirement.product_owner_name))
        for person in people:
            if person.open_id not in seen:
                seen.add(person.open_id)
                yield person
    for node in snapshot.nodes:
        for person in node.owners:
            if person.open_id not in seen:
                seen.add(person.open_id)
                yield person
    for blocker in snapshot.blockers:
        person = Person(open_id=blocker.owner_id, name=blocker.owner_name)
        if person.open_id not in seen:
            seen.add(person.open_id)
            yield person


class SnapshotImporter:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def import_snapshot(
        self,
        snapshot: DataSnapshot,
        *,
        validation_issues: Sequence[Mapping[str, object]] = (),
        source: str = "feishu",
        mode: str = "upsert",
    ) -> Dict[str, int]:
        counts = {
            "people": 0,
            "projects": 0,
            "requirements": 0,
            "nodes": 0,
            "blockers": 0,
            "project_configs": 0,
            "base_configs": 0,
        }
        with session_scope(self.database_url) as session:
            people = {person.open_id: _ensure_person(session, person) for person in _all_people(snapshot)}
            counts["people"] = len(people)
            projects: Dict[str, ProjectRow] = {}
            for name in {item.project for item in snapshot.project_configs} | {item.okr_target for item in snapshot.requirements}:
                row = session.scalar(select(ProjectRow).where(ProjectRow.source_key == name))
                if row is None:
                    row = ProjectRow(source_key=name, name=name)
                    session.add(row)
                    session.flush()
                projects[name] = row
            counts["projects"] = len(projects)

            configs_by_record = {}
            for config in snapshot.project_configs:
                project = projects[config.project]
                row = session.scalar(select(ProjectConfigRow).where(ProjectConfigRow.source_record_id == config.record_id))
                if row is None:
                    row = ProjectConfigRow(source_record_id=config.record_id, project_id=project.id)
                    session.add(row)
                self._copy_project_config(row, config, project.id)
                configs_by_record[config.record_id] = row
            counts["project_configs"] = len(snapshot.project_configs)

            requirements_by_source = {}
            for requirement in snapshot.requirements:
                project = projects[requirement.okr_target]
                row = session.scalar(select(RequirementRow).where(RequirementRow.source_record_id == requirement.record_id))
                if row is None:
                    row = RequirementRow(source_record_id=requirement.record_id)
                    session.add(row)
                self._copy_requirement(row, requirement, project, people)
                requirements_by_source[requirement.record_id] = row
                requirements_by_source[requirement.requirement_id] = row
            session.flush()
            counts["requirements"] = len(snapshot.requirements)

            for node in snapshot.nodes:
                requirement = requirements_by_source.get(node.requirement_id)
                if requirement is None:
                    continue
                row = session.scalar(select(RequirementNodeRow).where(RequirementNodeRow.source_record_id == node.record_id))
                if row is None:
                    row = RequirementNodeRow(source_record_id=node.record_id, requirement_id=requirement.id)
                    session.add(row)
                row.requirement_id = requirement.id
                row.domain = node.domain
                row.work_type = node.work_type
                row.name = node.name
                row.planned_start = node.planned_start
                row.planned_end = node.planned_end
                row.actual_end = node.actual_end
                row.status = node.status.value
                row.progress_note = node.progress_note
                row.updated_at_source = node.updated_at
                row.risk_level = int(node.risk_level)
                row.risk_reasons = list(node.risk_reasons)
                row.safe_deadline = node.safe_deadline
                row.owners = [people[person.open_id] for person in node.owners]
            counts["nodes"] = len(snapshot.nodes)

            for blocker in snapshot.blockers:
                requirement = requirements_by_source.get(blocker.requirement_id)
                if requirement is None:
                    continue
                row = session.scalar(select(BlockerRow).where(BlockerRow.source_record_id == blocker.record_id))
                if row is None:
                    row = BlockerRow(source_record_id=blocker.record_id, requirement_id=requirement.id)
                    session.add(row)
                row.requirement_id = requirement.id
                row.node_source_record_id = blocker.node_record_id
                row.title = blocker.title
                row.owner_id = people[blocker.owner_id].id
                row.found_at = blocker.found_at
                row.planned_resolution_at = blocker.planned_resolution_at
                row.actual_resolution_at = blocker.actual_resolution_at
                row.status = blocker.status
                row.affects_merge = blocker.affects_merge
                row.resolution_note = blocker.resolution_note
            counts["blockers"] = len(snapshot.blockers)

            for item in snapshot.base_configs:
                row = session.scalar(select(BaseConfigRow).where(BaseConfigRow.source_record_id == item.record_id))
                if row is None:
                    row = BaseConfigRow(source_record_id=item.record_id)
                    session.add(row)
                row.name = item.name
                row.config_type = item.config_type
                row.sort_order = item.sort_order
                row.enabled = item.enabled
                row.notes = item.notes
            counts["base_configs"] = len(snapshot.base_configs)

            session.add(
                MigrationRunRow(
                    source=source,
                    mode=mode,
                    imported_counts=counts,
                    validation_issues=[dict(issue) for issue in validation_issues],
                )
            )
        return counts

    @staticmethod
    def _copy_project_config(row: ProjectConfigRow, config: ProjectConfig, project_id: str) -> None:
        row.project_id = project_id
        row.duration_mode = config.duration_mode
        row.at1_days = config.at1_days
        row.at2_days = config.at2_days
        row.pv1_days = config.pv1_days
        row.pv2_days = config.pv2_days
        row.regression_days = config.regression_days
        row.launch_weekdays = sorted(config.launch_weekdays) if config.launch_weekdays is not None else None
        row.launch_cutoff = config.launch_cutoff
        row.llm_enabled = config.llm_enabled
        row.notes = config.llm_notes

    @staticmethod
    def _copy_requirement(row: RequirementRow, requirement: Requirement, project: ProjectRow, people: Mapping[str, PersonRow]) -> None:
        row.project_id = project.id
        row.requirement_key = requirement.requirement_id
        row.name = requirement.name
        row.okr_target = requirement.okr_target
        row.current_stage = requirement.current_stage
        row.target_version = requirement.target_version
        row.requirement_doc_url = requirement.requirement_doc_url
        row.meego_url = requirement.meego_url
        row.translation_url = requirement.translation_url
        row.merge_at = requirement.merge_at
        row.launch_at = requirement.launch_at
        row.project_owner_id = people[requirement.project_owner_id].id
        row.product_owner_id = people[requirement.product_owner_id].id if requirement.product_owner_id else None
        row.briefing_completed = requirement.briefing_completed
        row.notification_enabled = requirement.notification_enabled
        row.archived = requirement.archived
        row.source_project_config_record_id = requirement.project_config_record_id
        row.notes = requirement.requirement_notes


class DatabaseRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def load_snapshot(self) -> Tuple[DataSnapshot, List[object]]:
        requirements: List[Requirement] = []
        nodes: List[DeliveryNode] = []
        blockers: List[Blocker] = []
        project_configs: List[ProjectConfig] = []
        base_configs: List[BaseConfig] = []
        with session_scope(self.database_url) as session:
            requirement_rows = list(session.scalars(select(RequirementRow).order_by(RequirementRow.id)))
            node_rows = list(session.scalars(select(RequirementNodeRow).order_by(RequirementNodeRow.id)))
            blocker_rows = list(session.scalars(select(BlockerRow).order_by(BlockerRow.id)))
            config_rows = list(session.scalars(select(ProjectConfigRow).order_by(ProjectConfigRow.id)))
            base_rows = list(session.scalars(select(BaseConfigRow).order_by(BaseConfigRow.sort_order, BaseConfigRow.id)))
            for row in config_rows:
                project = session.get(ProjectRow, row.project_id)
                if project is not None:
                    project_configs.append(ProjectConfig(record_id=row.source_record_id, project=project.name, duration_mode=row.duration_mode, at1_days=row.at1_days, at2_days=row.at2_days, pv1_days=row.pv1_days, pv2_days=row.pv2_days, regression_days=row.regression_days, launch_weekdays=set(_json_value(row.launch_weekdays) or []) or None, launch_cutoff=row.launch_cutoff, llm_enabled=row.llm_enabled, llm_notes=row.notes))
            for row in requirement_rows:
                project_owner = session.get(PersonRow, row.project_owner_id)
                product_owner = session.get(PersonRow, row.product_owner_id) if row.product_owner_id else None
                if project_owner is None:
                    continue
                requirements.append(Requirement(record_id=row.source_record_id, requirement_id=row.requirement_key, name=row.name, okr_target=row.okr_target, current_stage=row.current_stage, project_owner_id=project_owner.feishu_open_id or project_owner.id, project_owner_name=project_owner.display_name, product_owner_id=(product_owner.feishu_open_id or product_owner.id) if product_owner else None, product_owner_name=product_owner.display_name if product_owner else None, target_version=row.target_version, requirement_doc_url=row.requirement_doc_url, meego_url=row.meego_url, translation_url=row.translation_url, merge_at=_as_aware(row.merge_at), launch_at=_as_aware(row.launch_at), briefing_completed=row.briefing_completed, notification_enabled=row.notification_enabled, archived=row.archived, project_config_record_id=row.source_project_config_record_id, requirement_notes=row.notes))
            for row in node_rows:
                people = [Person(open_id=person.feishu_open_id or person.id, name=person.display_name) for person in row.owners]
                if people:
                    nodes.append(DeliveryNode(record_id=row.source_record_id, requirement_id=row.requirement.source_record_id, domain=row.domain, work_type=row.work_type, name=row.name, owners=people, planned_start=_as_aware(row.planned_start), planned_end=_as_aware(row.planned_end), actual_end=_as_aware(row.actual_end), status=NodeStatus(row.status), progress_note=row.progress_note, updated_at=_as_aware(row.updated_at_source), risk_level=RiskLevel(row.risk_level), risk_reasons=list(_json_value(row.risk_reasons) or []), safe_deadline=_as_aware(row.safe_deadline)))
            for row in blocker_rows:
                owner = session.get(PersonRow, row.owner_id)
                if owner is not None:
                    blockers.append(Blocker(record_id=row.source_record_id, requirement_id=row.requirement.source_record_id, node_record_id=row.node_source_record_id, title=row.title, owner_id=owner.feishu_open_id or owner.id, owner_name=owner.display_name, found_at=_as_aware(row.found_at), planned_resolution_at=_as_aware(row.planned_resolution_at), actual_resolution_at=_as_aware(row.actual_resolution_at), status=row.status, affects_merge=row.affects_merge, resolution_note=row.resolution_note))
            base_configs = [BaseConfig(record_id=row.source_record_id, name=row.name, config_type=row.config_type, sort_order=row.sort_order, enabled=row.enabled, notes=row.notes) for row in base_rows]
        return DataSnapshot(requirements=requirements, nodes=nodes, blockers=blockers, project_configs=project_configs, base_configs=base_configs), []

    def write_requirement_notification_times(self, record_ids: Sequence[str], notified_at: datetime) -> None:
        with session_scope(self.database_url) as session:
            rows = list(session.scalars(select(RequirementRow).where(RequirementRow.source_record_id.in_(list(record_ids)))))
            for row in rows:
                row.last_notified_at = notified_at

    def write_requirement_risks(self, results: Sequence[object]) -> None:
        with session_scope(self.database_url) as session:
            for result in results:
                row = session.scalar(select(RequirementRow).where(RequirementRow.source_record_id == result.requirement_record_id))
                if row is not None:
                    row.current_stage = result.current_stage
                    row.system_risk_level = int(result.level)
                    row.system_risk_reasons = list(result.reasons)
                    row.predicted_completion_at = result.predicted_completion
                    row.buffer_days = result.buffer_days
                    row.last_checked_at = datetime.now(timezone.utc)

    def write_node_risks(self, results: Sequence[object]) -> None:
        with session_scope(self.database_url) as session:
            for result in results:
                row = session.scalar(select(RequirementNodeRow).where(RequirementNodeRow.source_record_id == result.node_record_id))
                if row is not None:
                    row.risk_level = int(result.level)
                    row.risk_reasons = list(result.reasons)
                    row.safe_deadline = result.safe_deadline
                    if result.planned_end_is_system_managed:
                        row.planned_end = result.planned_end
