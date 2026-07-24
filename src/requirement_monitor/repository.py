import json
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from requirement_monitor.feishu_cli import FeishuCLI
from requirement_monitor.models import (
    Blocker,
    DataSnapshot,
    DeliveryNode,
    NodeRisk,
    ProjectConfig,
    Requirement,
    RequirementRisk,
    RiskLevel,
    ValidationIssue,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
KEY_TABLE_NAMES = ("需求主表", "进展节点表", "阻塞项表", "项目配置表")
BATCH_SIZE = 500
MIN_MILLISECONDS_TIMESTAMP = 1_000_000_000_000
MAX_MILLISECONDS_TIMESTAMP = 99_999_999_999_999


class RepositorySchemaError(RuntimeError):
    """Raised when required Bitable tables or response metadata are missing."""


class _FieldParseError(ValueError):
    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(message)
        self.field_name = field_name


def parse_snapshot(
    raw_tables: Mapping[str, Sequence[Mapping[str, Any]]]
) -> Tuple[DataSnapshot, List[ValidationIssue]]:
    issues: List[ValidationIssue] = []
    requirements: List[Requirement] = []
    requirement_by_record_id: Dict[str, Requirement] = {}
    raw_requirement_ids: Dict[str, Optional[str]] = {}

    for raw_record in raw_tables.get("需求主表", []):
        record_id = _best_effort_record_id(raw_record)
        requirement_id = _best_effort_field_text(raw_record, "需求编号")
        if record_id is not None:
            raw_requirement_ids[record_id] = requirement_id
        try:
            requirement = _parse_requirement(raw_record)
        except (ValidationError, _FieldParseError, TypeError, ValueError) as error:
            issues.append(
                _validation_issue(
                    "需求主表",
                    raw_record,
                    error,
                    requirement_id=requirement_id,
                    field_names=_REQUIREMENT_FIELD_NAMES,
                )
            )
            continue
        requirements.append(requirement)
        requirement_by_record_id[requirement.record_id] = requirement

    invalid_requirement_record_ids = set(raw_requirement_ids) - set(
        requirement_by_record_id
    )
    nodes: List[DeliveryNode] = []
    for raw_record in raw_tables.get("进展节点表", []):
        relation = _best_effort_first_link(raw_record, "关联需求")
        if relation in invalid_requirement_record_ids:
            continue
        requirement = requirement_by_record_id.get(relation or "")
        requirement_id = (
            requirement.requirement_id
            if requirement is not None
            else raw_requirement_ids.get(relation or "")
        )
        try:
            if requirement is None:
                raise _FieldParseError("关联需求", "must reference a valid requirement")
            nodes.append(_parse_node(raw_record, requirement.requirement_id))
        except (ValidationError, _FieldParseError, TypeError, ValueError) as error:
            issues.append(
                _validation_issue(
                    "进展节点表",
                    raw_record,
                    error,
                    requirement_id=requirement_id,
                    field_names=_NODE_FIELD_NAMES,
                )
            )

    blockers: List[Blocker] = []
    for raw_record in raw_tables.get("阻塞项表", []):
        relation = _best_effort_first_link(raw_record, "关联需求")
        if relation in invalid_requirement_record_ids:
            continue
        requirement = requirement_by_record_id.get(relation or "")
        requirement_id = (
            requirement.requirement_id
            if requirement is not None
            else raw_requirement_ids.get(relation or "")
        )
        try:
            if requirement is None:
                raise _FieldParseError("关联需求", "must reference a valid requirement")
            blockers.append(_parse_blocker(raw_record, requirement.requirement_id))
        except (ValidationError, _FieldParseError, TypeError, ValueError) as error:
            issues.append(
                _validation_issue(
                    "阻塞项表",
                    raw_record,
                    error,
                    requirement_id=requirement_id,
                    field_names=_BLOCKER_FIELD_NAMES,
                )
            )

    project_configs: List[ProjectConfig] = []
    for raw_record in raw_tables.get("项目配置表", []):
        try:
            project_configs.append(_parse_project_config(raw_record))
        except (ValidationError, _FieldParseError, TypeError, ValueError) as error:
            issues.append(
                _validation_issue(
                    "项目配置表",
                    raw_record,
                    error,
                    field_names=_PROJECT_CONFIG_FIELD_NAMES,
                )
            )

    return (
        DataSnapshot(
            requirements=requirements,
            nodes=nodes,
            blockers=blockers,
            project_configs=project_configs,
        ),
        issues,
    )


class BitableRepository:
    def __init__(
        self,
        url_or_token: str,
        *,
        client: Optional[FeishuCLI] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.url_or_token = url_or_token
        self.client = client or FeishuCLI()
        self._now = now or (lambda: datetime.now(SHANGHAI))
        self._app_token: Optional[str] = None
        self._table_ids: Optional[Dict[str, str]] = None

    def load_snapshot(self) -> Tuple[DataSnapshot, List[ValidationIssue]]:
        table_ids = self._require_tables(KEY_TABLE_NAMES)
        raw_tables = {
            table_name: self._all_records(table_ids[table_name])
            for table_name in KEY_TABLE_NAMES
        }
        return parse_snapshot(raw_tables)

    def write_requirement_risks(
        self, results: Sequence[RequirementRisk]
    ) -> None:
        if not results:
            return
        table_id = self._require_tables(("需求主表",))["需求主表"]
        checked_at = _datetime_to_milliseconds(self._now())
        records = [
            {
                "id": result.requirement_record_id,
                "fields": {
                    "当前风险等级": _risk_level_text(result.level),
                    "风险原因": "\n".join(result.reasons),
                    "预计完成时间": _optional_datetime_to_milliseconds(
                        result.predicted_completion
                    ),
                    "剩余缓冲天数": result.buffer_days,
                    "最近检查时间": checked_at,
                },
            }
            for result in results
        ]
        self._batch_update(table_id, records)

    def write_node_risks(self, results: Sequence[NodeRisk]) -> None:
        if not results:
            return
        table_id = self._require_tables(("进展节点表",))["进展节点表"]
        records = [
            {
                "id": result.node_record_id,
                "fields": {
                    "系统风险等级": _risk_level_text(result.level),
                    "系统风险原因": "\n".join(result.reasons),
                    "最晚安全DDL": _optional_datetime_to_milliseconds(
                        result.safe_deadline
                    ),
                },
            }
            for result in results
        ]
        self._batch_update(table_id, records)

    def append_notification_records(
        self, records: Sequence[Mapping[str, Any]]
    ) -> None:
        if not records:
            return
        table_id = self._require_tables(("通知记录表",))["通知记录表"]
        normalized = [_notification_fields(record) for record in records]
        app_token = self._required_app_token()
        for chunk in _chunks(normalized):
            self._write_batch_file(
                self.client.batch_create, app_token, table_id, chunk
            )

    def _batch_update(
        self, table_id: str, records: Sequence[Mapping[str, Any]]
    ) -> None:
        app_token = self._required_app_token()
        for chunk in _chunks(records):
            self._write_batch_file(
                self.client.batch_update, app_token, table_id, chunk
            )

    def _write_batch_file(
        self,
        operation: Callable[..., Mapping[str, Any]],
        app_token: str,
        table_id: str,
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        file_descriptor, file_path = tempfile.mkstemp(
            prefix="requirement-monitor-batch-", suffix=".json"
        )
        try:
            os.chmod(file_path, 0o600)
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as output_file:
                json.dump(
                    records,
                    output_file,
                    ensure_ascii=False,
                    allow_nan=False,
                )
            operation(app_token, table_id, file_path=file_path)
        finally:
            try:
                os.unlink(file_path)
            except FileNotFoundError:
                pass

    def _all_records(self, table_id: str) -> List[Mapping[str, Any]]:
        records: List[Mapping[str, Any]] = []
        page_token: Optional[str] = None
        seen_page_tokens = set()
        while True:
            response = self.client.records(
                self._required_app_token(),
                table_id,
                page_size=BATCH_SIZE,
                page_token=page_token,
                automatic_fields=True,
            )
            data = _data_object(response)
            page_records = data.get("records", data.get("items"))
            if not isinstance(page_records, list):
                raise RepositorySchemaError(
                    "Bitable records response did not include a record list"
                )
            if any(not isinstance(record, Mapping) for record in page_records):
                raise RepositorySchemaError(
                    "Bitable record list contained an invalid record"
                )
            records.extend(page_records)
            if not data.get("has_more"):
                return records
            next_page_token = data.get("page_token")
            if not isinstance(next_page_token, str) or not next_page_token:
                raise RepositorySchemaError(
                    "Record pagination omitted the next page token"
                )
            if next_page_token in seen_page_tokens:
                raise RepositorySchemaError(
                    "Record pagination repeated a previously seen page token"
                )
            seen_page_tokens.add(next_page_token)
            page_token = next_page_token

    def _require_tables(self, names: Iterable[str]) -> Dict[str, str]:
        if self._table_ids is None:
            self._discover_tables()
        assert self._table_ids is not None
        missing = [name for name in names if name not in self._table_ids]
        if missing:
            raise RepositorySchemaError(
                "Missing required Bitable tables: {}".format(", ".join(missing))
            )
        return self._table_ids

    def _discover_tables(self) -> None:
        meta = self.client.meta(self.url_or_token)
        data = _data_object(meta)
        app_token = data.get("app_token")
        if not isinstance(app_token, str) or not app_token:
            raise RepositorySchemaError("Bitable metadata did not include app_token")
        tables = data.get("tables", data.get("items"))
        if not isinstance(tables, list):
            raise RepositorySchemaError(
                "Bitable metadata did not include a table list"
            )
        table_ids: Dict[str, str] = {}
        for table in tables:
            if not isinstance(table, Mapping):
                continue
            name = table.get("name", table.get("table_name"))
            table_id = table.get("table_id")
            if not isinstance(name, str) or not isinstance(table_id, str):
                continue
            if name in table_ids:
                raise RepositorySchemaError(
                    "Duplicate Bitable table name is ambiguous: {}".format(name)
                )
            table_ids[name] = table_id
        self._app_token = app_token
        self._table_ids = table_ids

    def _required_app_token(self) -> str:
        if self._app_token is None:
            self._discover_tables()
        assert self._app_token is not None
        return self._app_token


def _parse_requirement(raw_record: Mapping[str, Any]) -> Requirement:
    fields = _record_fields(raw_record)
    project_owner_id, project_owner_name = _person(
        fields.get("项目负责人"), "项目负责人", required=True
    )
    product_owner_id, product_owner_name = _person(
        fields.get("产品负责人"), "产品负责人", required=False
    )
    notes = _optional_text(fields, "需求补充说明")
    if not notes:
        notes = _optional_text(fields, "备注")
    return Requirement(
        record_id=_record_id(raw_record),
        requirement_id=_required_text(fields, "需求编号"),
        name=_required_text(fields, "需求名称"),
        project=_required_text(fields, "项目名称"),
        current_stage=_single_select(fields.get("当前环节"), "当前环节"),
        project_owner_id=project_owner_id,
        project_owner_name=project_owner_name,
        product_owner_id=product_owner_id,
        product_owner_name=product_owner_name,
        target_version=_required_text(fields, "目标版本"),
        merge_at=_date_time(fields.get("合板时间"), "合板时间"),
        launch_at=_optional_date_time(fields.get("计划上线时间"), "计划上线时间"),
        briefing_completed=_checkbox(fields.get("需求宣讲是否完成"), "需求宣讲是否完成"),
        notification_enabled=_checkbox(fields.get("是否允许通知"), "是否允许通知"),
        archived=_checkbox(fields.get("是否归档"), "是否归档"),
        project_config_record_id=_optional_first_link(
            fields.get("项目配置关联"), "项目配置关联"
        ),
        requirement_notes=notes,
    )


def _parse_node(
    raw_record: Mapping[str, Any], requirement_id: str
) -> DeliveryNode:
    fields = _record_fields(raw_record)
    owner_id, owner_name = _person(fields.get("负责人"), "负责人", required=True)
    return DeliveryNode(
        record_id=_record_id(raw_record),
        requirement_id=requirement_id,
        domain=_single_select(fields.get("交付域"), "交付域"),
        work_type=_single_select(fields.get("工作类型"), "工作类型"),
        name=_required_text(fields, "节点名称"),
        owner_id=owner_id,
        owner_name=owner_name,
        planned_start=_optional_date_time(fields.get("计划开始时间"), "计划开始时间"),
        planned_end=_date_time(fields.get("计划完成时间"), "计划完成时间"),
        actual_end=_optional_date_time(fields.get("实际完成时间"), "实际完成时间"),
        status=_single_select(fields.get("当前状态"), "当前状态"),
        progress_note=_optional_text(fields, "进展说明"),
        updated_at=_optional_date_time(fields.get("最后更新时间"), "最后更新时间"),
        risk_level=_optional_risk_level(fields.get("系统风险等级")),
        risk_reasons=_reason_list(fields.get("系统风险原因")),
        safe_deadline=_optional_date_time(fields.get("最晚安全DDL"), "最晚安全DDL"),
    )


def _parse_blocker(
    raw_record: Mapping[str, Any], requirement_id: str
) -> Blocker:
    fields = _record_fields(raw_record)
    owner_id, owner_name = _person(fields.get("责任人"), "责任人", required=True)
    return Blocker(
        record_id=_record_id(raw_record),
        requirement_id=requirement_id,
        node_record_id=_optional_first_link(fields.get("关联节点"), "关联节点"),
        title=_required_text(fields, "阻塞事项"),
        owner_id=owner_id,
        owner_name=owner_name,
        found_at=_date_time(fields.get("发现时间"), "发现时间"),
        planned_resolution_at=_date_time(
            fields.get("计划解决时间"), "计划解决时间"
        ),
        actual_resolution_at=_optional_date_time(
            fields.get("实际解决时间"), "实际解决时间"
        ),
        status=_single_select(fields.get("当前状态"), "当前状态"),
        affects_merge=_checkbox(fields.get("是否影响合板"), "是否影响合板"),
        resolution_note=_optional_text(fields, "处理说明"),
    )


def _parse_project_config(raw_record: Mapping[str, Any]) -> ProjectConfig:
    fields = _record_fields(raw_record)
    return ProjectConfig(
        record_id=_record_id(raw_record),
        project=_required_text(fields, "项目名称"),
        duration_mode=_duration_mode(fields.get("周期计算方式")),
        at_days=_optional_number(fields.get("AT 最少测试天数"), "AT 最少测试天数"),
        pv_days=_optional_number(fields.get("PV 最少测试天数"), "PV 最少测试天数"),
        bugfix_days=_optional_number(
            fields.get("Bug 修复预留天数"), "Bug 修复预留天数"
        ),
        regression_days=_optional_number(
            fields.get("线上回归最少天数"), "线上回归最少天数"
        ),
        server_special_days=_optional_number(
            fields.get("服务端专项测试天数"), "服务端专项测试天数"
        ),
        client_special_days=_optional_number(
            fields.get("客户端专项测试天数"), "客户端专项测试天数"
        ),
        vehicle_special_days=_optional_number(
            fields.get("车辆专项测试天数"), "车辆专项测试天数"
        ),
        launch_weekdays=_launch_weekdays(fields.get("可上线日期")),
        launch_cutoff=_optional_text_value(fields.get("上线截止时间")),
        llm_enabled=_checkbox(fields.get("是否启用 LLM"), "是否启用 LLM"),
        llm_notes=_optional_text(fields, "项目补充说明"),
    )


def _validation_issue(
    table_name: str,
    raw_record: Mapping[str, Any],
    error: Exception,
    *,
    requirement_id: Optional[str] = None,
    field_names: Mapping[str, str],
) -> ValidationIssue:
    if isinstance(error, _FieldParseError):
        field_name = error.field_name
        message = str(error)
    elif isinstance(error, ValidationError):
        detail = error.errors()[0]
        location = detail.get("loc", ())
        model_field = location[0] if location else ""
        field_name = field_names.get(str(model_field), "记录")
        message = str(detail.get("msg") or error)
    else:
        field_name = "记录"
        message = str(error) or error.__class__.__name__
    return ValidationIssue(
        table_name=table_name,
        record_id=_best_effort_record_id(raw_record),
        requirement_id=requirement_id,
        field_name=field_name,
        message=message,
    )


def _record_fields(raw_record: Mapping[str, Any]) -> Mapping[str, Any]:
    fields = raw_record.get("fields")
    if not isinstance(fields, Mapping):
        raise _FieldParseError("fields", "record must include a fields object")
    return fields


def _record_id(raw_record: Mapping[str, Any]) -> str:
    record_id = _best_effort_record_id(raw_record)
    if record_id is None:
        raise _FieldParseError("record_id", "record must include record_id")
    return record_id


def _best_effort_record_id(raw_record: Mapping[str, Any]) -> Optional[str]:
    if not isinstance(raw_record, Mapping):
        return None
    value = raw_record.get("record_id", raw_record.get("id"))
    return value if isinstance(value, str) and value.strip() else None


def _best_effort_field_text(
    raw_record: Mapping[str, Any], field_name: str
) -> Optional[str]:
    try:
        fields = _record_fields(raw_record)
        return _optional_text_value(fields.get(field_name))
    except (TypeError, ValueError):
        return None


def _best_effort_first_link(
    raw_record: Mapping[str, Any], field_name: str
) -> Optional[str]:
    try:
        fields = _record_fields(raw_record)
        return _optional_first_link(fields.get(field_name), field_name)
    except (TypeError, ValueError):
        return None


def _required_text(fields: Mapping[str, Any], field_name: str) -> str:
    value = _optional_text_value(fields.get(field_name))
    if value is None or not value.strip():
        raise _FieldParseError(field_name, "must not be empty")
    return value.strip()


def _optional_text(fields: Mapping[str, Any], field_name: str) -> str:
    return _optional_text_value(fields.get(field_name)) or ""


def _optional_text_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("text", "name", "value"):
            nested = value.get(key)
            if isinstance(nested, str):
                return nested.strip()
    if isinstance(value, list):
        parts = [
            text
            for item in value
            for text in [_optional_text_value(item)]
            if text
        ]
        return "".join(parts) if parts else None
    return None


def _person(
    value: Any, field_name: str, *, required: bool
) -> Tuple[Optional[str], Optional[str]]:
    if value in (None, [], ""):
        if required:
            raise _FieldParseError(field_name, "must include a person")
        return None, None
    if isinstance(value, Mapping) and isinstance(value.get("users"), list):
        value = value["users"]
    people = value if isinstance(value, list) else [value]
    if len(people) != 1 or not isinstance(people[0], Mapping):
        raise _FieldParseError(field_name, "must include a valid person")
    person = people[0]
    person_id = next(
        (
            person[key]
            for key in ("open_id", "id", "user_id")
            if isinstance(person.get(key), str) and person[key].strip()
        ),
        None,
    )
    person_name = next(
        (
            person[key]
            for key in ("name", "display_name", "en_name")
            if isinstance(person.get(key), str) and person[key].strip()
        ),
        None,
    )
    if person_id is None or person_name is None:
        raise _FieldParseError(field_name, "person must include id and name")
    return person_id.strip(), person_name.strip()


def _single_select(value: Any, field_name: str) -> str:
    if isinstance(value, list):
        if len(value) != 1:
            raise _FieldParseError(field_name, "must include one selected value")
        value = value[0]
    text = _optional_text_value(value)
    if not text:
        raise _FieldParseError(field_name, "must include one selected value")
    return text


def _checkbox(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise _FieldParseError(field_name, "must be a checkbox value")


def _date_time(value: Any, field_name: str) -> datetime:
    parsed = _optional_date_time(value, field_name)
    if parsed is None:
        raise _FieldParseError(field_name, "must include a date and time")
    return parsed


def _optional_date_time(value: Any, field_name: str) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, Mapping):
        for key in ("timestamp", "value", "date"):
            if key in value:
                value = value[key]
                break
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, (int, float)):
            return _milliseconds_datetime(value, field_name)
        if isinstance(value, str):
            stripped = value.strip()
            if re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
                return _milliseconds_datetime(float(stripped), field_name)
            normalized = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=SHANGHAI)
            return parsed.astimezone(SHANGHAI)
    except (OverflowError, OSError, ValueError):
        pass
    raise _FieldParseError(field_name, "must be milliseconds or an ISO datetime")


def _milliseconds_datetime(value: Any, field_name: str) -> datetime:
    milliseconds = float(value)
    if not MIN_MILLISECONDS_TIMESTAMP <= milliseconds <= MAX_MILLISECONDS_TIMESTAMP:
        raise _FieldParseError(
            field_name, "numeric datetime must be a millisecond timestamp"
        )
    return datetime.fromtimestamp(
        milliseconds / 1000, timezone.utc
    ).astimezone(SHANGHAI)


def _links(value: Any, field_name: str) -> List[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, Mapping):
        for key in ("record_ids", "link_record_ids", "ids"):
            if key in value:
                return _links(value[key], field_name)
        for key in ("record_id", "link_record_id", "id"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return [nested.strip()]
        raise _FieldParseError(field_name, "must include linked record IDs")
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        result: List[str] = []
        for item in value:
            result.extend(_links(item, field_name))
        return result
    raise _FieldParseError(field_name, "must include linked record IDs")


def _optional_first_link(value: Any, field_name: str) -> Optional[str]:
    links = _links(value, field_name)
    if len(links) > 1:
        raise _FieldParseError(field_name, "must include at most one linked record")
    return links[0] if links else None


def _duration_mode(value: Any) -> str:
    selected = _single_select(value, "周期计算方式")
    modes = {
        "工作日": "workday",
        "workday": "workday",
        "自然日": "natural",
        "natural": "natural",
    }
    if selected not in modes:
        raise _FieldParseError("周期计算方式", "must be 工作日 or 自然日")
    return modes[selected]


def _optional_number(value: Any, field_name: str) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise _FieldParseError(field_name, "must be a number")
    if isinstance(value, float) and not value.is_integer():
        raise _FieldParseError(field_name, "must be a whole number")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise _FieldParseError(field_name, "must be a number") from None


def _launch_weekdays(value: Any) -> Optional[set]:
    text = _optional_text_value(value)
    if not text:
        return None
    names = {
        "周一": 0,
        "星期一": 0,
        "周二": 1,
        "星期二": 1,
        "周三": 2,
        "星期三": 2,
        "周四": 3,
        "星期四": 3,
        "周五": 4,
        "星期五": 4,
        "周六": 5,
        "星期六": 5,
        "周日": 6,
        "周天": 6,
        "星期日": 6,
        "星期天": 6,
    }
    result = set()
    for token in re.split(r"[\s,，、;/]+", text):
        if not token:
            continue
        if token in names:
            result.add(names[token])
        elif token.isdigit() and 0 <= int(token) <= 6:
            result.add(int(token))
        else:
            raise _FieldParseError("可上线日期", "contains an invalid weekday")
    return result or None


def _optional_risk_level(value: Any) -> RiskLevel:
    if value in (None, "", []):
        return RiskLevel.NORMAL
    selected = _single_select(value, "系统风险等级")
    levels = {
        "正常": RiskLevel.NORMAL,
        "普通": RiskLevel.NORMAL,
        "预警": RiskLevel.WARNING,
        "严重": RiskLevel.SEVERE,
        "0": RiskLevel.NORMAL,
        "1": RiskLevel.WARNING,
        "2": RiskLevel.SEVERE,
    }
    if selected not in levels:
        raise _FieldParseError("系统风险等级", "contains an invalid risk level")
    return levels[selected]


def _reason_list(value: Any) -> List[str]:
    text = _optional_text_value(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[\n；;]+", text) if item.strip()]


def _risk_level_text(level: Any) -> str:
    normalized = RiskLevel(level)
    return {
        RiskLevel.NORMAL: "正常",
        RiskLevel.WARNING: "预警",
        RiskLevel.SEVERE: "严重",
    }[normalized]


def _datetime_to_milliseconds(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return int(value.timestamp() * 1000)


def _optional_datetime_to_milliseconds(value: Optional[datetime]) -> Optional[int]:
    return None if value is None else _datetime_to_milliseconds(value)


def _notification_fields(record: Mapping[str, Any]) -> Dict[str, Any]:
    fields = {
        "通知指纹": _notification_value(record, "通知指纹", "fingerprint", default=""),
        "需求": _notification_link(record),
        "通知类型": _notification_value(
            record, "通知类型", "notification_type", default=""
        ),
        "风险等级": _notification_risk_level(record),
        "消息摘要": _notification_value(record, "消息摘要", "summary", default=""),
        "通知对象": _notification_people(record),
        "发送时间": _notification_datetime(record),
        "发送结果": _notification_value(
            record, "发送结果", "send_result", default=""
        ),
        "错误信息": _notification_value(record, "错误信息", "error", default=""),
        "是否使用 LLM": bool(
            _notification_value(record, "是否使用 LLM", "llm_used", default=False)
        ),
        "LLM 降级原因": _notification_value(
            record,
            "LLM 降级原因",
            "llm_degradation_reason",
            default="",
        ),
    }
    return fields


def _notification_value(
    record: Mapping[str, Any], chinese_key: str, english_key: str, *, default: Any
) -> Any:
    if chinese_key in record:
        return record[chinese_key]
    return record.get(english_key, default)


def _notification_link(record: Mapping[str, Any]) -> List[str]:
    value = _notification_value(
        record, "需求", "requirement_record_id", default=None
    )
    return _links(value, "需求")


def _notification_risk_level(record: Mapping[str, Any]) -> str:
    value = _notification_value(record, "风险等级", "risk_level", default=0)
    if isinstance(value, str) and value in {"正常", "普通", "预警", "严重"}:
        return "正常" if value == "普通" else value
    return _risk_level_text(value)


def _notification_people(record: Mapping[str, Any]) -> List[Dict[str, str]]:
    value = _notification_value(record, "通知对象", "recipient_ids", default=[])
    people = value if isinstance(value, list) else [value]
    normalized = []
    for person in people:
        if isinstance(person, str) and person.strip():
            normalized.append({"id": person.strip()})
        elif isinstance(person, Mapping):
            person_id = next(
                (
                    person[key]
                    for key in ("id", "open_id", "user_id")
                    if isinstance(person.get(key), str) and person[key].strip()
                ),
                None,
            )
            if person_id is not None:
                normalized.append({"id": person_id.strip()})
    return normalized


def _notification_datetime(record: Mapping[str, Any]) -> Optional[int]:
    value = _notification_value(record, "发送时间", "sent_at", default=None)
    if value is None:
        return None
    if isinstance(value, datetime):
        return _datetime_to_milliseconds(value)
    return _datetime_to_milliseconds(_date_time(value, "发送时间"))


def _chunks(records: Sequence[Mapping[str, Any]]) -> Iterable[List[Mapping[str, Any]]]:
    for start in range(0, len(records), BATCH_SIZE):
        yield list(records[start : start + BATCH_SIZE])


def _data_object(value: Mapping[str, Any]) -> Mapping[str, Any]:
    data = value.get("data")
    return data if isinstance(data, Mapping) else value


_REQUIREMENT_FIELD_NAMES = {
    "record_id": "record_id",
    "requirement_id": "需求编号",
    "name": "需求名称",
    "project": "项目名称",
    "current_stage": "当前环节",
    "project_owner_id": "项目负责人",
    "project_owner_name": "项目负责人",
    "product_owner_id": "产品负责人",
    "product_owner_name": "产品负责人",
    "target_version": "目标版本",
    "merge_at": "合板时间",
    "launch_at": "计划上线时间",
    "briefing_completed": "需求宣讲是否完成",
    "notification_enabled": "是否允许通知",
    "archived": "是否归档",
    "project_config_record_id": "项目配置关联",
    "requirement_notes": "需求补充说明",
}
_NODE_FIELD_NAMES = {
    "record_id": "record_id",
    "requirement_id": "关联需求",
    "domain": "交付域",
    "work_type": "工作类型",
    "name": "节点名称",
    "owner_id": "负责人",
    "owner_name": "负责人",
    "planned_start": "计划开始时间",
    "planned_end": "计划完成时间",
    "actual_end": "实际完成时间",
    "status": "当前状态",
    "progress_note": "进展说明",
    "updated_at": "最后更新时间",
    "risk_level": "系统风险等级",
    "risk_reasons": "系统风险原因",
    "safe_deadline": "最晚安全DDL",
}
_BLOCKER_FIELD_NAMES = {
    "record_id": "record_id",
    "requirement_id": "关联需求",
    "node_record_id": "关联节点",
    "title": "阻塞事项",
    "owner_id": "责任人",
    "owner_name": "责任人",
    "found_at": "发现时间",
    "planned_resolution_at": "计划解决时间",
    "actual_resolution_at": "实际解决时间",
    "status": "当前状态",
    "affects_merge": "是否影响合板",
    "resolution_note": "处理说明",
}
_PROJECT_CONFIG_FIELD_NAMES = {
    "record_id": "record_id",
    "project": "项目名称",
    "duration_mode": "周期计算方式",
    "at_days": "AT 最少测试天数",
    "pv_days": "PV 最少测试天数",
    "bugfix_days": "Bug 修复预留天数",
    "regression_days": "线上回归最少天数",
    "server_special_days": "服务端专项测试天数",
    "client_special_days": "客户端专项测试天数",
    "vehicle_special_days": "车辆专项测试天数",
    "launch_weekdays": "可上线日期",
    "launch_cutoff": "上线截止时间",
    "llm_enabled": "是否启用 LLM",
    "llm_notes": "项目补充说明",
}
