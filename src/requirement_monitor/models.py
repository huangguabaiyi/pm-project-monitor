from datetime import datetime
from enum import Enum, IntEnum
from typing import List, Literal, Optional, Set

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RiskLevel(IntEnum):
    NORMAL = 0
    WARNING = 1
    SEVERE = 2


class NodeStatus(str, Enum):
    NOT_STARTED = "未开始"
    IN_PROGRESS = "进行中"
    COMPLETED = "已完成"
    SKIPPED = "已跳过"
    CANCELLED = "已取消"


class DomainModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    @model_validator(mode="after")
    def validate_ids_names_and_datetimes(self):
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if _is_id_or_name_field(field_name) and isinstance(value, str):
                if not value.strip():
                    raise ValueError("{} must not be empty".format(field_name))
            if isinstance(value, datetime) and not _is_timezone_aware(value):
                raise ValueError("{} must be timezone-aware".format(field_name))
        return self


class ValidationIssue(DomainModel):
    table_name: str
    record_id: Optional[str] = None
    requirement_id: Optional[str] = None
    field_name: str
    message: str


class Person(DomainModel):
    open_id: str
    name: str


class Requirement(DomainModel):
    record_id: str
    requirement_id: str
    name: str
    project: str
    current_stage: str
    project_owner_id: str
    project_owner_name: str
    product_owner_id: Optional[str] = None
    product_owner_name: Optional[str] = None
    target_version: str
    merge_at: datetime
    launch_at: Optional[datetime] = None
    briefing_completed: bool
    notification_enabled: bool
    archived: bool
    project_config_record_id: Optional[str] = None
    requirement_notes: str = ""


class DeliveryNode(DomainModel):
    record_id: str
    requirement_id: str
    domain: str
    work_type: str
    name: str
    owner_id: str
    owner_name: str
    planned_start: Optional[datetime] = None
    planned_end: datetime
    actual_end: Optional[datetime] = None
    status: NodeStatus
    progress_note: str = ""
    updated_at: Optional[datetime] = None
    risk_level: RiskLevel = RiskLevel.NORMAL
    risk_reasons: List[str] = Field(default_factory=list)
    safe_deadline: Optional[datetime] = None


class Blocker(DomainModel):
    record_id: str
    requirement_id: str
    node_record_id: Optional[str] = None
    title: str
    owner_id: str
    owner_name: str
    found_at: datetime
    planned_resolution_at: datetime
    actual_resolution_at: Optional[datetime] = None
    status: str
    affects_merge: bool
    resolution_note: str = ""


class ProjectConfig(DomainModel):
    record_id: str
    project: str
    duration_mode: Literal["workday", "natural"]
    at_days: Optional[int] = Field(default=None, ge=0)
    pv_days: Optional[int] = Field(default=None, ge=0)
    bugfix_days: Optional[int] = Field(default=None, ge=0)
    regression_days: Optional[int] = Field(default=None, ge=0)
    server_special_days: Optional[int] = Field(default=None, ge=0)
    client_special_days: Optional[int] = Field(default=None, ge=0)
    vehicle_special_days: Optional[int] = Field(default=None, ge=0)
    launch_weekdays: Optional[Set[int]] = None
    launch_cutoff: Optional[str] = None
    llm_enabled: bool = False
    llm_notes: str = ""

    @model_validator(mode="after")
    def validate_launch_weekdays(self):
        if self.launch_weekdays is not None and not self.launch_weekdays <= set(range(7)):
            raise ValueError("launch_weekdays must contain values from 0 to 6")
        return self


class FixedRules(DomainModel):
    server_launch_weekdays: Set[int]
    server_launch_cutoff: str
    checklist_days_before: int = Field(ge=0)
    at_workdays: int = Field(ge=0)
    at_natural_days: int = Field(ge=0)
    pv_days: int = Field(ge=0)
    bugfix_days: int = Field(ge=0)
    regression_days: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_server_launch_weekdays(self):
        if not self.server_launch_weekdays <= set(range(7)):
            raise ValueError("server_launch_weekdays must contain values from 0 to 6")
        return self


class DataSnapshot(DomainModel):
    requirements: List[Requirement] = Field(default_factory=list)
    nodes: List[DeliveryNode] = Field(default_factory=list)
    blockers: List[Blocker] = Field(default_factory=list)
    project_configs: List[ProjectConfig] = Field(default_factory=list)

    def eligible_requirements(self) -> List[Requirement]:
        return [
            requirement
            for requirement in self.requirements
            if requirement.briefing_completed
            and requirement.notification_enabled
            and not requirement.archived
        ]


class NodeRisk(DomainModel):
    node_record_id: str
    requirement_id: str
    node_name: str
    domain: str
    owner_id: str
    owner_name: str
    level: RiskLevel = RiskLevel.NORMAL
    predicted_completion: Optional[datetime] = None
    safe_deadline: Optional[datetime] = None
    buffer_days: Optional[float] = None
    reasons: List[str] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)


class RequirementRisk(DomainModel):
    requirement_record_id: str
    requirement_id: str
    requirement_name: str
    project: str
    level: RiskLevel = RiskLevel.NORMAL
    predicted_completion: Optional[datetime] = None
    buffer_days: Optional[float] = None
    affected_domains: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)
    node_risks: List[NodeRisk] = Field(default_factory=list)


class LLMEnrichment(DomainModel):
    available: bool
    rule_level: RiskLevel
    llm_level: Optional[RiskLevel] = None
    effective_level: RiskLevel
    summary: str = ""
    reasons: List[str] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)
    failure_reason: Optional[str] = None


class SendResult(DomainModel):
    success: bool
    attempts: int = Field(ge=0)
    format_used: Literal["card", "compact_card", "text"]
    status_code: Optional[int] = None
    feishu_code: Optional[int] = None
    error: Optional[str] = None


class RunReport(DomainModel):
    trigger: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    total_requirements: int = Field(default=0, ge=0)
    eligible_requirement_count: int = Field(default=0, ge=0)
    processed_requirements: int = Field(default=0, ge=0)
    invalid_records: int = Field(default=0, ge=0)
    normal_requirements: int = Field(default=0, ge=0)
    warning_requirements: int = Field(default=0, ge=0)
    severe_requirements: int = Field(default=0, ge=0)
    sent_cards: int = Field(default=0, ge=0)
    severe_cards: int = Field(default=0, ge=0)
    failed_sends: int = Field(default=0, ge=0)
    llm_degraded: bool = False
    send_results: List[SendResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


def _is_id_or_name_field(field_name: str) -> bool:
    return (
        field_name == "name"
        or field_name == "project"
        or field_name == "title"
        or field_name.endswith("_name")
        or field_name.endswith("_id")
    )


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
