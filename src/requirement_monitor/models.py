import re
from enum import Enum, IntEnum
from typing import Annotated, Dict, List, Literal, Optional, Set

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
StrippedStr = Annotated[str, StringConstraints(strip_whitespace=True)]
SkipScope = Literal["record", "requirement", "run"]


class RiskLevel(IntEnum):
    NORMAL = 0
    WARNING = 1
    SEVERE = 2


class RiskFinding(BaseModel):
    reason_code: NonEmptyStr
    reason_text: NonEmptyStr
    stage_refs: List[NonEmptyStr] = Field(default_factory=list)
    domain_refs: List[NonEmptyStr] = Field(default_factory=list)
    level: RiskLevel
    source: NonEmptyStr

    @model_validator(mode="after")
    def deduplicate_references(self):
        self.stage_refs = list(dict.fromkeys(self.stage_refs))
        self.domain_refs = list(dict.fromkeys(self.domain_refs))
        return self


class RiskGroup(BaseModel):
    reason_code: NonEmptyStr
    reason_text: NonEmptyStr
    stage_refs: List[NonEmptyStr] = Field(default_factory=list)
    domain_refs: List[NonEmptyStr] = Field(default_factory=list)
    level: RiskLevel
    source_findings: List[RiskFinding] = Field(default_factory=list)


class RiskFamily(BaseModel):
    code: NonEmptyStr
    title: NonEmptyStr
    level: RiskLevel
    stage_refs: List[NonEmptyStr]
    domain_refs: List[NonEmptyStr]
    source_findings: List[RiskFinding]


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
    current_value: Optional[StrippedStr] = None
    expected_format: Optional[NonEmptyStr] = None
    fix_suggestion: Optional[NonEmptyStr] = None
    skip_scope: SkipScope = "record"
    message: NonEmptyStr

    @field_validator("requirement_id", mode="before")
    @classmethod
    def normalize_blank_requirement_id(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class Person(BaseModel):
    open_id: NonEmptyStr
    name: NonEmptyStr


class Requirement(BaseModel):
    record_id: NonEmptyStr
    requirement_id: NonEmptyStr
    name: NonEmptyStr
    okr_target: NonEmptyStr
    current_stage: NonEmptyStr
    project_owner_id: NonEmptyStr
    project_owner_name: NonEmptyStr
    product_owner_id: Optional[NonEmptyStr] = None
    product_owner_name: Optional[NonEmptyStr] = None
    target_version: NonEmptyStr
    requirement_doc_url: Optional[NonEmptyStr] = None
    meego_url: Optional[NonEmptyStr] = None
    translation_url: Optional[NonEmptyStr] = None
    merge_at: AwareDatetime
    launch_at: Optional[AwareDatetime] = None
    briefing_completed: bool
    notification_enabled: bool
    archived: bool
    project_config_record_id: Optional[NonEmptyStr] = None
    requirement_notes: StrippedStr = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_project_name(cls, values):
        if not isinstance(values, dict):
            return values
        normalized = dict(values)
        okr_target = normalized.get("okr_target")
        normalized_okr_target = (
            okr_target.strip() if isinstance(okr_target, str) else okr_target
        )
        normalized_legacy_values = {}
        for legacy_name in ("project", "项目名称"):
            legacy_value = normalized.get(legacy_name)
            normalized_legacy_value = (
                legacy_value.strip()
                if isinstance(legacy_value, str)
                else legacy_value
            )
            normalized_legacy_values[legacy_name] = normalized_legacy_value
            if (
                normalized_okr_target
                and normalized_legacy_value
                and normalized_okr_target != normalized_legacy_value
            ):
                raise ValueError(
                    f"okr_target conflicts with legacy field {legacy_name}"
                )

        if not normalized_okr_target:
            project = normalized_legacy_values.get("project")
            project_name = normalized_legacy_values.get("项目名称")
            if project and project_name and project != project_name:
                raise ValueError(
                    "project and 项目名称 contain conflicting values while "
                    "okr_target is empty"
                )
            for legacy_name in ("project", "项目名称"):
                if legacy_name in normalized:
                    normalized["okr_target"] = normalized[legacy_name]
                    break
        return normalized

    @field_validator(
        "requirement_doc_url", "meego_url", "translation_url", mode="before"
    )
    @classmethod
    def normalize_blank_urls(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @computed_field
    @property
    def project(self) -> str:
        return self.okr_target

    @field_validator("launch_at")
    @classmethod
    def validate_launch_schedule(cls, value, info: ValidationInfo):
        merge_at = info.data.get("merge_at")
        if value is not None and merge_at is not None and value > merge_at:
            raise ValueError("launch_at must not follow merge_at")
        return value


class DeliveryNode(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    record_id: NonEmptyStr
    requirement_id: NonEmptyStr
    domain: NonEmptyStr
    work_type: NonEmptyStr
    name: NonEmptyStr
    owners: List[Person] = Field(min_length=1)
    planned_start: Optional[AwareDatetime] = None
    planned_end: Optional[AwareDatetime] = None
    actual_end: Optional[AwareDatetime] = None
    status: NodeStatus
    progress_note: StrippedStr = ""
    updated_at: Optional[AwareDatetime] = None
    risk_level: RiskLevel = RiskLevel.NORMAL
    risk_reasons: List[NonEmptyStr] = Field(default_factory=list)
    safe_deadline: Optional[AwareDatetime] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_owner(cls, values):
        if not isinstance(values, dict):
            return values
        normalized = dict(values)
        if "owners" not in normalized:
            owner_id = normalized.get("owner_id")
            owner_name = normalized.get("owner_name")
            if owner_id is not None or owner_name is not None:
                normalized["owners"] = [{"open_id": owner_id, "name": owner_name}]
        return normalized

    @model_validator(mode="after")
    def validate_schedule(self):
        if (
            self.planned_start is not None
            and self.planned_end is not None
            and self.planned_start > self.planned_end
        ):
            raise ValueError("planned_start must not follow planned_end")
        return self

    @property
    def owner_id(self) -> str:
        if not self.owners:
            raise ValueError("owners must contain at least one owner")
        return self.owners[0].open_id

    @property
    def owner_name(self) -> str:
        if not self.owners:
            raise ValueError("owners must contain at least one owner")
        return self.owners[0].name


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
    at1_days: Optional[int] = Field(default=None, ge=0)
    at2_days: Optional[int] = Field(default=None, ge=0)
    pv1_days: Optional[int] = Field(default=None, ge=0)
    pv2_days: Optional[int] = Field(default=None, ge=0)
    regression_days: Optional[int] = Field(default=None, ge=0)
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


class BaseConfig(BaseModel):
    record_id: NonEmptyStr
    name: NonEmptyStr
    config_type: Literal["环节", "交付域", "工作类型", "测试角色"]
    sort_order: int
    enabled: bool
    notes: StrippedStr = ""


class FixedRules(BaseModel):
    server_launch_weekdays: Set[int]
    server_launch_cutoff: NonEmptyStr
    checklist_days_before: int = Field(ge=0)
    at1_days: Optional[int] = Field(default=None, ge=0)
    at2_days: Optional[int] = Field(default=None, ge=0)
    pv1_days: Optional[int] = Field(default=None, ge=0)
    pv2_days: Optional[int] = Field(default=None, ge=0)
    regression_days: int = Field(default=2, ge=0)
    at_workdays: int = Field(default=8, ge=0)
    at_natural_days: int = Field(default=11, ge=0)
    pv_days: int = Field(default=3, ge=0)

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
    base_configs: List[BaseConfig] = Field(default_factory=list)
    project_config_by_record_id: Dict[str, ProjectConfig] = Field(
        default_factory=dict
    )
    project_config_by_project: Dict[str, ProjectConfig] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def build_project_config_indexes(self):
        return self.rebuild_project_config_indexes()

    def rebuild_project_config_indexes(self):
        by_record_id: Dict[str, List[ProjectConfig]] = {}
        by_project: Dict[str, List[ProjectConfig]] = {}
        for config in self.project_configs:
            by_record_id.setdefault(config.record_id, []).append(config)
            by_project.setdefault(config.project, []).append(config)
        self.project_config_by_record_id = {
            record_id: configs[0]
            for record_id, configs in by_record_id.items()
            if len(configs) == 1
        }
        self.project_config_by_project = {
            project: configs[0]
            for project, configs in by_project.items()
            if len(configs) == 1
        }
        return self

    def eligible_requirements(self) -> List[Requirement]:
        return [
            requirement
            for requirement in self.requirements
            if requirement.briefing_completed
            and requirement.notification_enabled
            and not requirement.archived
        ]

    def enabled_config_names(self, config_type: str) -> List[str]:
        return [
            item.name
            for item in sorted(
                (
                    config
                    for config in self.base_configs
                    if config.config_type == config_type and config.enabled
                ),
                key=lambda config: (config.sort_order, config.name),
            )
        ]


class NodeRisk(BaseModel):
    node_record_id: NonEmptyStr
    requirement_id: NonEmptyStr
    node_name: NonEmptyStr
    domain: NonEmptyStr
    owner_id: NonEmptyStr
    owner_name: NonEmptyStr
    owners: List[Person] = Field(default_factory=list)
    planned_end: Optional[AwareDatetime] = None
    status: NodeStatus
    planned_start: Optional[AwareDatetime] = None
    level: RiskLevel = RiskLevel.NORMAL
    predicted_completion: Optional[AwareDatetime] = None
    safe_deadline: Optional[AwareDatetime] = None
    buffer_days: Optional[float] = None
    reasons: List[NonEmptyStr] = Field(default_factory=list)
    findings: List[RiskFinding] = Field(default_factory=list)
    actions: List[NonEmptyStr] = Field(default_factory=list)
    progress_note: StrippedStr = ""
    planned_end_is_system_managed: bool = False


class ScheduleFormulaTerm(BaseModel):
    label: NonEmptyStr
    days: int = Field(ge=0)
    source: Optional[NonEmptyStr] = None


class ScheduleFormula(BaseModel):
    domain: NonEmptyStr
    started_at: AwareDatetime
    duration_mode: Literal["workday", "natural"]
    terms: List[ScheduleFormulaTerm] = Field(min_length=1)
    predicted_completion: AwareDatetime


class LLMEnrichment(BaseModel):
    available: bool
    rule_level: RiskLevel
    llm_level: Optional[RiskLevel] = None
    effective_level: RiskLevel
    summary: StrippedStr = ""
    reasons: List[NonEmptyStr] = Field(default_factory=list)
    actions: List[NonEmptyStr] = Field(default_factory=list)
    failure_reason: Optional[NonEmptyStr] = None


class RequirementRisk(BaseModel):
    requirement_record_id: NonEmptyStr
    requirement_id: NonEmptyStr
    requirement_name: NonEmptyStr
    project: NonEmptyStr
    current_stage: NonEmptyStr = "未提供"
    target_version: NonEmptyStr
    requirement_doc_url: Optional[NonEmptyStr] = None
    meego_url: Optional[NonEmptyStr] = None
    translation_url: Optional[NonEmptyStr] = None
    merge_at: AwareDatetime
    launch_at: Optional[AwareDatetime] = None
    project_owner_id: NonEmptyStr
    project_owner_name: NonEmptyStr
    level: RiskLevel = RiskLevel.NORMAL
    predicted_completion: Optional[AwareDatetime] = None
    schedule_formula: Optional[ScheduleFormula] = None
    buffer_days: Optional[float] = None
    affected_domains: List[NonEmptyStr] = Field(default_factory=list)
    reasons: List[NonEmptyStr] = Field(default_factory=list)
    findings: List[RiskFinding] = Field(default_factory=list)
    stage_order: Dict[NonEmptyStr, int] = Field(default_factory=dict)
    process_reminders: List[NonEmptyStr] = Field(default_factory=list)
    actions: List[NonEmptyStr] = Field(default_factory=list)
    project_notes: StrippedStr = ""
    requirement_notes: StrippedStr = ""
    sensitive_people: List[Person] = Field(default_factory=list)
    node_risks: List[NodeRisk] = Field(default_factory=list)
    blockers: List[Blocker] = Field(default_factory=list)
    llm_enrichment: Optional[LLMEnrichment] = None

    @field_validator("launch_at")
    @classmethod
    def validate_launch_schedule(cls, value, info: ValidationInfo):
        merge_at = info.data.get("merge_at")
        if value is not None and merge_at is not None and value > merge_at:
            raise ValueError("launch_at must not follow merge_at")
        return value


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
    requirement_risks: List[RequirementRisk] = Field(default_factory=list)
    validation_issues: List[ValidationIssue] = Field(default_factory=list)
    llm_attempted: bool = False
    llm_degraded: bool = False
    llm_failure_reasons: List[NonEmptyStr] = Field(default_factory=list)
    send_results: List[SendResult] = Field(default_factory=list)
    errors: List[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_run_times(self):
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


def _is_hhmm(value: str) -> bool:
    return re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) is not None
