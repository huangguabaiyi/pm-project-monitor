import re
from enum import Enum, IntEnum
from typing import Annotated, List, Literal, Optional, Set

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
StrippedStr = Annotated[str, StringConstraints(strip_whitespace=True)]


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


class ValidationIssue(BaseModel):
    table_name: NonEmptyStr
    record_id: Optional[NonEmptyStr] = None
    requirement_id: Optional[NonEmptyStr] = None
    field_name: NonEmptyStr
    message: NonEmptyStr


class Person(BaseModel):
    open_id: NonEmptyStr
    name: NonEmptyStr


class Requirement(BaseModel):
    record_id: NonEmptyStr
    requirement_id: NonEmptyStr
    name: NonEmptyStr
    project: NonEmptyStr
    current_stage: NonEmptyStr
    project_owner_id: NonEmptyStr
    project_owner_name: NonEmptyStr
    product_owner_id: Optional[NonEmptyStr] = None
    product_owner_name: Optional[NonEmptyStr] = None
    target_version: NonEmptyStr
    merge_at: AwareDatetime
    launch_at: Optional[AwareDatetime] = None
    briefing_completed: bool
    notification_enabled: bool
    archived: bool
    project_config_record_id: Optional[NonEmptyStr] = None
    requirement_notes: StrippedStr = ""

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.launch_at is not None and self.launch_at < self.merge_at:
            raise ValueError("launch_at must not precede merge_at")
        return self


class DeliveryNode(BaseModel):
    record_id: NonEmptyStr
    requirement_id: NonEmptyStr
    domain: NonEmptyStr
    work_type: NonEmptyStr
    name: NonEmptyStr
    owner_id: NonEmptyStr
    owner_name: NonEmptyStr
    planned_start: Optional[AwareDatetime] = None
    planned_end: AwareDatetime
    actual_end: Optional[AwareDatetime] = None
    status: NodeStatus
    progress_note: StrippedStr = ""
    updated_at: Optional[AwareDatetime] = None
    risk_level: RiskLevel = RiskLevel.NORMAL
    risk_reasons: List[NonEmptyStr] = Field(default_factory=list)
    safe_deadline: Optional[AwareDatetime] = None

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.planned_start is not None and self.planned_start > self.planned_end:
            raise ValueError("planned_start must not follow planned_end")
        return self


class Blocker(BaseModel):
    record_id: NonEmptyStr
    requirement_id: NonEmptyStr
    node_record_id: Optional[NonEmptyStr] = None
    title: NonEmptyStr
    owner_id: NonEmptyStr
    owner_name: NonEmptyStr
    found_at: AwareDatetime
    planned_resolution_at: AwareDatetime
    actual_resolution_at: Optional[AwareDatetime] = None
    status: NonEmptyStr
    affects_merge: bool
    resolution_note: StrippedStr = ""

    @model_validator(mode="after")
    def validate_resolution_times(self):
        if self.planned_resolution_at < self.found_at:
            raise ValueError("planned_resolution_at must not precede found_at")
        if (
            self.actual_resolution_at is not None
            and self.actual_resolution_at < self.found_at
        ):
            raise ValueError("actual_resolution_at must not precede found_at")
        return self


class ProjectConfig(BaseModel):
    record_id: NonEmptyStr
    project: NonEmptyStr
    duration_mode: Literal["workday", "natural"]
    at_days: Optional[int] = Field(default=None, ge=0)
    pv_days: Optional[int] = Field(default=None, ge=0)
    bugfix_days: Optional[int] = Field(default=None, ge=0)
    regression_days: Optional[int] = Field(default=None, ge=0)
    server_special_days: Optional[int] = Field(default=None, ge=0)
    client_special_days: Optional[int] = Field(default=None, ge=0)
    vehicle_special_days: Optional[int] = Field(default=None, ge=0)
    launch_weekdays: Optional[Set[int]] = None
    launch_cutoff: Optional[NonEmptyStr] = None
    llm_enabled: bool = False
    llm_notes: StrippedStr = ""

    @field_validator("launch_weekdays")
    @classmethod
    def validate_launch_weekdays(cls, value: Optional[Set[int]]) -> Optional[Set[int]]:
        if value is not None and (not value or not value <= set(range(7))):
            raise ValueError("launch_weekdays must be non-empty values from 0 to 6")
        return value

    @field_validator("launch_cutoff")
    @classmethod
    def validate_launch_cutoff(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _is_hhmm(value):
            raise ValueError("launch_cutoff must use HH:MM")
        return value


class FixedRules(BaseModel):
    server_launch_weekdays: Set[int]
    server_launch_cutoff: NonEmptyStr
    checklist_days_before: int = Field(ge=0)
    at_workdays: int = Field(ge=0)
    at_natural_days: int = Field(ge=0)
    pv_days: int = Field(ge=0)
    bugfix_days: int = Field(ge=0)
    regression_days: int = Field(ge=0)

    @field_validator("server_launch_weekdays")
    @classmethod
    def validate_server_launch_weekdays(cls, value: Set[int]) -> Set[int]:
        if not value or not value <= set(range(7)):
            raise ValueError(
                "server_launch_weekdays must be non-empty values from 0 to 6"
            )
        return value

    @field_validator("server_launch_cutoff")
    @classmethod
    def validate_server_launch_cutoff(cls, value: str) -> str:
        if not _is_hhmm(value):
            raise ValueError("server_launch_cutoff must use HH:MM")
        return value


class DataSnapshot(BaseModel):
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


class NodeRisk(BaseModel):
    node_record_id: NonEmptyStr
    requirement_id: NonEmptyStr
    node_name: NonEmptyStr
    domain: NonEmptyStr
    owner_id: NonEmptyStr
    owner_name: NonEmptyStr
    level: RiskLevel = RiskLevel.NORMAL
    predicted_completion: Optional[AwareDatetime] = None
    safe_deadline: Optional[AwareDatetime] = None
    buffer_days: Optional[float] = None
    reasons: List[NonEmptyStr] = Field(default_factory=list)
    actions: List[NonEmptyStr] = Field(default_factory=list)


class RequirementRisk(BaseModel):
    requirement_record_id: NonEmptyStr
    requirement_id: NonEmptyStr
    requirement_name: NonEmptyStr
    project: NonEmptyStr
    level: RiskLevel = RiskLevel.NORMAL
    predicted_completion: Optional[AwareDatetime] = None
    buffer_days: Optional[float] = None
    affected_domains: List[NonEmptyStr] = Field(default_factory=list)
    reasons: List[NonEmptyStr] = Field(default_factory=list)
    actions: List[NonEmptyStr] = Field(default_factory=list)
    node_risks: List[NodeRisk] = Field(default_factory=list)


class LLMEnrichment(BaseModel):
    available: bool
    rule_level: RiskLevel
    llm_level: Optional[RiskLevel] = None
    effective_level: RiskLevel
    summary: StrippedStr = ""
    reasons: List[NonEmptyStr] = Field(default_factory=list)
    actions: List[NonEmptyStr] = Field(default_factory=list)
    failure_reason: Optional[NonEmptyStr] = None


class SendResult(BaseModel):
    success: bool
    attempts: int = Field(ge=0)
    format_used: Literal["card", "compact_card", "text"]
    status_code: Optional[int] = None
    feishu_code: Optional[int] = None
    error: Optional[NonEmptyStr] = None


class RunReport(BaseModel):
    trigger: NonEmptyStr
    started_at: AwareDatetime
    finished_at: Optional[AwareDatetime] = None
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
    errors: List[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_run_times(self):
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


def _is_hhmm(value: str) -> bool:
    return re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) is not None
