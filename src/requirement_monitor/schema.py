import json
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from requirement_monitor.feishu_cli import FeishuCLI, FeishuCLIError


TEXT = 1
NUMBER = 2
SINGLE_SELECT = 3
DATE_TIME = 5
MODIFIED_TIME = 1002
CHECKBOX = 7
USER = 11
SINGLE_LINK = 18


class SchemaError(RuntimeError):
    """Raised when the existing Bitable schema cannot be reconciled safely."""


@dataclass(frozen=True)
class FieldSpec:
    name: str
    field_type: int
    target_table: Optional[str] = None
    compatible_types: Tuple[int, ...] = ()
    property: Optional[Mapping[str, Any]] = None

    def accepts_type(self, field_type: int) -> bool:
        return field_type in (self.field_type, *self.compatible_types)


@dataclass(frozen=True)
class TableSpec:
    name: str
    fields: Tuple[FieldSpec, ...]

    @property
    def primary_field_name(self) -> str:
        return self.fields[0].name


@dataclass(frozen=True)
class Operation:
    kind: str
    payload: Mapping[str, Any]

    def __str__(self) -> str:
        return "{} {}".format(
            self.kind,
            json.dumps(self.payload, ensure_ascii=False, sort_keys=True),
        )


SCHEMA = (
    TableSpec(
        "需求主表",
        (
            FieldSpec("需求编号", TEXT),
            FieldSpec("需求名称", TEXT),
            FieldSpec("OKR目标", TEXT),
            FieldSpec("当前环节", SINGLE_SELECT),
            FieldSpec("项目负责人", USER),
            FieldSpec("产品负责人", USER),
            FieldSpec("目标版本", TEXT),
            FieldSpec("需求文档链接", TEXT),
            FieldSpec("Meego链接", TEXT),
            FieldSpec("多语言翻译链接", TEXT),
            FieldSpec("合板时间", DATE_TIME),
            FieldSpec("计划上线时间", DATE_TIME),
            FieldSpec("需求宣讲是否完成", CHECKBOX),
            FieldSpec("是否允许通知", CHECKBOX),
            FieldSpec("是否归档", CHECKBOX),
            FieldSpec("项目配置关联", SINGLE_LINK, "项目配置表"),
            FieldSpec("需求补充说明", TEXT),
            FieldSpec("备注", TEXT),
            FieldSpec("当前风险等级", SINGLE_SELECT),
            FieldSpec("风险原因", TEXT),
            FieldSpec("预计完成时间", DATE_TIME),
            FieldSpec("剩余缓冲天数", NUMBER),
            FieldSpec("最近检查时间", DATE_TIME),
            FieldSpec("最近通知时间", DATE_TIME),
        ),
    ),
    TableSpec(
        "进展节点表",
        (
            FieldSpec("节点名称", SINGLE_SELECT),
            FieldSpec("关联需求", SINGLE_LINK, "需求主表"),
            FieldSpec("交付域", SINGLE_SELECT),
            FieldSpec("工作类型", SINGLE_SELECT),
            FieldSpec("负责人", USER, property={"multiple": True}),
            FieldSpec("计划开始时间", DATE_TIME),
            FieldSpec("计划完成时间", DATE_TIME),
            FieldSpec("实际完成时间", DATE_TIME),
            FieldSpec("当前状态", SINGLE_SELECT),
            FieldSpec("进展说明", TEXT),
            FieldSpec("最后更新时间", DATE_TIME, compatible_types=(MODIFIED_TIME,)),
            FieldSpec("系统风险等级", SINGLE_SELECT),
            FieldSpec("系统风险原因", TEXT),
            FieldSpec("最晚安全DDL", DATE_TIME),
        ),
    ),
    TableSpec(
        "阻塞项表",
        (
            FieldSpec("阻塞事项", TEXT),
            FieldSpec("关联需求", SINGLE_LINK, "需求主表"),
            FieldSpec("关联节点", SINGLE_LINK, "进展节点表"),
            FieldSpec("责任人", USER),
            FieldSpec("发现时间", DATE_TIME),
            FieldSpec("计划解决时间", DATE_TIME),
            FieldSpec("实际解决时间", DATE_TIME),
            FieldSpec("当前状态", SINGLE_SELECT),
            FieldSpec("是否影响合板", CHECKBOX),
            FieldSpec("处理说明", TEXT),
        ),
    ),
    TableSpec(
        "项目配置表",
        (
            FieldSpec("项目名称", TEXT),
            FieldSpec("周期计算方式", SINGLE_SELECT),
            FieldSpec("AT 第一轮默认天数", NUMBER),
            FieldSpec("AT 第二轮默认天数", NUMBER),
            FieldSpec("PV 第一轮默认天数", NUMBER),
            FieldSpec("PV 第二轮默认天数", NUMBER),
            FieldSpec("线上回归默认天数", NUMBER),
            FieldSpec("可上线日期", TEXT),
            FieldSpec("上线截止时间", TEXT),
            FieldSpec("是否启用 LLM", CHECKBOX),
            FieldSpec("项目补充说明", TEXT),
        ),
    ),
    TableSpec(
        "基础配置表",
        (
            FieldSpec("配置名称", TEXT),
            FieldSpec("配置类型", SINGLE_SELECT),
            FieldSpec("排序", NUMBER),
            FieldSpec("是否启用", CHECKBOX),
            FieldSpec("备注", TEXT),
        ),
    ),
    TableSpec(
        "通知记录表",
        (
            FieldSpec("通知指纹", TEXT),
            FieldSpec("需求", SINGLE_LINK, "需求主表"),
            FieldSpec("通知类型", SINGLE_SELECT),
            FieldSpec("风险等级", SINGLE_SELECT),
            FieldSpec("消息摘要", TEXT),
            FieldSpec("通知对象", USER, property={"multiple": True}),
            FieldSpec("发送时间", DATE_TIME),
            FieldSpec("发送结果", SINGLE_SELECT),
            FieldSpec("错误信息", TEXT),
            FieldSpec("是否使用 LLM", CHECKBOX),
            FieldSpec("LLM 降级原因", TEXT),
        ),
    ),
)


DEFAULT_PROCESS_NODES = (
    "需求撰写",
    "内部评审",
    "产品需求评审",
    "设计稿输出",
    "设计宣讲",
    "需求宣讲",
    "工作量评估排期",
    "各端开发",
    "联调",
    "提测",
    "AT 测试第一轮",
    "AT 测试第二轮",
    "PV 测试第一轮",
    "PV 测试第二轮",
    "服务端上线",
    "线上回归",
    "多语言翻译",
    "版本合入",
)
DELIVERY_DOMAINS = (
    "平台",
    "客户端",
    "服务端",
    "车辆",
    "中枢平台",
    "嵌入式",
    "插件",
    "助手",
    "其他",
)
WORK_TYPES = ("研发", "测试", "联调", "发布", "设计")
TEST_ROLES = ("客户端测试", "服务端测试", "车辆测试", "专项测试", "其他测试")


def _seed_records(config_type: str, names: Sequence[str]) -> List[Dict[str, Any]]:
    return [
        {
            "配置名称": name,
            "配置类型": config_type,
            "排序": index,
            "是否启用": True,
        }
        for index, name in enumerate(names, start=1)
    ]


BASIC_CONFIG_SEEDS = tuple(
    _seed_records("环节", DEFAULT_PROCESS_NODES)
    + _seed_records("交付域", DELIVERY_DOMAINS)
    + _seed_records("工作类型", WORK_TYPES)
    + _seed_records("测试角色", TEST_ROLES)
)


def build_schema_plan(
    meta: Mapping[str, Any],
    fields_by_table: Mapping[str, Any],
) -> List[Operation]:
    tables = _table_items(meta)
    _require_unique_names(tables, _table_name, "table")
    tables_by_name = {_table_name(table): table for table in tables}
    _preflight_existing_schema(tables_by_name, fields_by_table)
    operations: List[Operation] = []
    renamed_fields = set()

    if "需求主表" not in tables_by_name and "数据表" in tables_by_name:
        main_table = tables_by_name.pop("数据表")
        operations.append(
            Operation(
                "rename_table",
                {"table_id": _table_id(main_table), "name": "需求主表"},
            )
        )
        tables_by_name["需求主表"] = main_table

    main_table = tables_by_name.get("需求主表")
    if main_table is not None:
        main_table_id = _table_id(main_table)
        main_fields = _field_items(fields_by_table.get(main_table_id, []))
        fields_by_name = {_field_name(field): field for field in main_fields}
        if "OKR目标" not in fields_by_name and "项目名称" in fields_by_name:
            legacy_field = fields_by_name["项目名称"]
            if legacy_field.get("type", legacy_field.get("field_type")) != TEXT:
                raise SchemaError("Legacy field 需求主表.项目名称 must have type 1")
            field_id = _field_id(legacy_field)
            if field_id:
                operations.append(
                    Operation(
                        "rename_field",
                        {
                            "table_id": main_table_id,
                            "field_id": field_id,
                            "name": "OKR目标",
                        },
                    )
                )
                renamed_fields.add((main_table_id, "OKR目标"))
        if "需求编号" not in fields_by_name and "文本" in fields_by_name:
            primary_field = fields_by_name["文本"]
            if primary_field.get("type") != TEXT:
                raise SchemaError("Primary field 文本 must have type 1")
            field_id = _field_id(primary_field)
            if field_id:
                operations.append(
                    Operation(
                        "rename_field",
                        {
                            "table_id": main_table_id,
                            "field_id": field_id,
                            "name": "需求编号",
                        },
                    )
                )
                renamed_fields.add((main_table_id, "需求编号"))
    table_ids = {
        name: _table_id(table) for name, table in tables_by_name.items()
    }

    for table_spec in SCHEMA:
        if table_spec.name in tables_by_name:
            continue
        operations.append(
            Operation(
                "create_table",
                {
                    "name": table_spec.name,
                    "fields": [
                        {
                            "field_name": field.name,
                            "type": field.field_type,
                            **({"property": dict(field.property)} if field.property else {}),
                        }
                        for field in table_spec.fields
                        if field.field_type != SINGLE_LINK
                    ],
                },
            )
        )

    field_operations: List[Operation] = []
    link_operations: List[Operation] = []
    for table_spec in SCHEMA:
        table_id = table_ids.get(table_spec.name)
        if table_id is None:
            continue
        existing_fields = _field_items(fields_by_table.get(table_id, []))
        existing_by_name = {
            _field_name(field): field for field in existing_fields
        }
        for field_spec in table_spec.fields:
            if (table_id, field_spec.name) in renamed_fields:
                continue
            existing_field = existing_by_name.get(field_spec.name)
            if existing_field is not None:
                _validate_existing_field(
                    table_spec, field_spec, existing_field, table_ids
                )
                continue
            payload: Dict[str, Any] = {
                "table_id": table_id,
                "name": field_spec.name,
                "field_type": field_spec.field_type,
            }
            if field_spec.property:
                payload["property"] = dict(field_spec.property)
            if field_spec.field_type == SINGLE_LINK:
                target_id = table_ids.get(field_spec.target_table or "")
                if target_id is None:
                    continue
                payload["property"] = {
                    "table_id": target_id,
                    "multiple": False,
                }
                link_operations.append(Operation("create_field", payload))
            else:
                if field_spec.property is not None:
                    payload["property"] = dict(field_spec.property)
                field_operations.append(Operation("create_field", payload))

    operations.extend(field_operations)
    operations.extend(link_operations)
    return operations


def initialize_schema(
    bitable_url: str,
    *,
    apply: bool,
    client: Optional[FeishuCLI] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> List[Operation]:
    schema_client = client or FeishuCLI()
    meta, fields_by_table = _inspect_schema(schema_client, bitable_url)
    plan = build_schema_plan(meta, fields_by_table)

    if not apply:
        seed_operation = _build_seed_operation(
            schema_client, meta, summary=True
        )
        if seed_operation is not None:
            plan.append(seed_operation)
        return plan

    executor = _OperationExecutor(schema_client, sleep_fn)
    applied_operations: List[Operation] = []
    for _ in range(3):
        plan = build_schema_plan(meta, fields_by_table)
        if not plan:
            break
        for operation in plan:
            executor.execute(meta, operation)
            applied_operations.append(operation)
        meta, fields_by_table = _inspect_schema(schema_client, bitable_url)
    else:
        raise SchemaError("Schema installation did not converge")

    remaining_plan = build_schema_plan(meta, fields_by_table)
    if remaining_plan:
        raise SchemaError("Schema installation left unapplied operations")

    seed_operation = _build_seed_operation(schema_client, meta, summary=False)
    if seed_operation is not None:
        executor.execute(meta, seed_operation)
        applied_operations.append(seed_operation)
    return applied_operations


def _inspect_schema(
    client: FeishuCLI, bitable_url: str
) -> Tuple[Mapping[str, Any], Dict[str, Any]]:
    meta = client.meta(bitable_url)
    app_token = _app_token(meta)
    meta_data = _data_object(meta)
    if meta_data.get("table_id") and not _table_items(meta):
        meta = client.meta(app_token)
    fields_by_table = {
        _table_id(table): client.fields(app_token, _table_id(table))
        for table in _table_items(meta)
    }
    return meta, fields_by_table


class _OperationExecutor:
    def __init__(
        self,
        client: FeishuCLI,
        sleep_fn: Callable[[float], None],
        write_delay: float = 0.6,
    ) -> None:
        self.client = client
        self.sleep_fn = sleep_fn
        self.write_delay = write_delay
        self.has_written = False

    def execute(
        self, meta: Mapping[str, Any], operation: Operation
    ) -> None:
        retries = 0
        while True:
            if self.has_written:
                self.sleep_fn(self.write_delay)
            self.has_written = True
            try:
                _apply_operation(self.client, meta, operation)
                return
            except Exception as exc:
                if (
                    isinstance(exc, FeishuCLIError)
                    and "1254291" in str(exc)
                    and retries < 3
                ):
                    retries += 1
                    continue
                raise SchemaError(
                    "Schema operation {} failed: {}".format(operation, exc)
                ) from exc


def _apply_operation(
    client: FeishuCLI, meta: Mapping[str, Any], operation: Operation
) -> None:
    app_token = _app_token(meta)
    payload = operation.payload
    if operation.kind == "rename_table":
        client.rename_table(app_token, payload["table_id"], payload["name"])
        return
    if operation.kind == "rename_field":
        client.update_field(
            app_token,
            payload["table_id"],
            payload["field_id"],
            name=payload["name"],
        )
        return
    if operation.kind == "create_table":
        client.create_table(app_token, payload["name"], payload["fields"])
        return
    if operation.kind == "create_field":
        client.create_field(
            app_token,
            payload["table_id"],
            payload["name"],
            payload["field_type"],
            property=payload.get("property"),
        )
        return
    if operation.kind == "seed_records":
        client.batch_create(app_token, payload["table_id"], payload["records"])
        return
    raise SchemaError("Unsupported schema operation: {}".format(operation.kind))


def _build_seed_operation(
    client: FeishuCLI,
    meta: Mapping[str, Any],
    *,
    summary: bool,
) -> Optional[Operation]:
    basic_table = next(
        (
            table
            for table in _table_items(meta)
            if _table_name(table) == "基础配置表"
        ),
        None,
    )
    if basic_table is None:
        if summary:
            return Operation(
                "seed_records",
                {
                    "table_id": "<基础配置表>",
                    "record_count": len(BASIC_CONFIG_SEEDS),
                },
            )
        return None

    app_token = _app_token(meta)
    table_id = _table_id(basic_table)
    existing_records = _all_records(client, app_token, table_id)
    existing_keys = set()
    for record in existing_records:
        record_fields = record.get("fields")
        if not isinstance(record_fields, Mapping):
            raise SchemaError(
                "Bitable record did not include a fields object"
            )
        existing_keys.add(
            (record_fields.get("配置类型"), record_fields.get("配置名称"))
        )
    missing_records = [
        record
        for record in BASIC_CONFIG_SEEDS
        if (
            record["配置类型"],
            record["配置名称"],
        )
        not in existing_keys
    ]
    if not missing_records:
        return None
    if summary:
        return Operation(
            "seed_records",
            {"table_id": table_id, "record_count": len(missing_records)},
        )
    return Operation(
        "seed_records", {"table_id": table_id, "records": missing_records}
    )


def _all_records(
    client: FeishuCLI, app_token: str, table_id: str
) -> List[Mapping[str, Any]]:
    records: List[Mapping[str, Any]] = []
    page_token: Optional[str] = None
    while True:
        response = client.records(
            app_token, table_id, page_size=500, page_token=page_token
        )
        data = _data_object(response)
        if "records" in data:
            page_records = data["records"]
        elif "items" in data:
            page_records = data["items"]
        else:
            raise SchemaError(
                "Bitable records response did not include a record list"
            )
        if not isinstance(page_records, list):
            raise SchemaError(
                "Bitable records response did not include a record list"
            )
        if any(not isinstance(record, Mapping) for record in page_records):
            raise SchemaError("Bitable record list contained an invalid record")
        records.extend(page_records)
        if not data.get("has_more"):
            return records
        next_page_token = data.get("page_token")
        if not isinstance(next_page_token, str) or not next_page_token:
            raise SchemaError("Record pagination omitted the next page token")
        page_token = next_page_token


def _validate_existing_field(
    table_spec: TableSpec,
    field_spec: FieldSpec,
    existing_field: Mapping[str, Any],
    table_ids: Mapping[str, str],
) -> None:
    existing_type = existing_field.get("type", existing_field.get("field_type"))
    if not field_spec.accepts_type(existing_type):
        raise SchemaError(
            "Field {}.{} has type {}, expected {}".format(
                table_spec.name,
                field_spec.name,
                existing_type,
                field_spec.field_type,
            )
        )
    if field_spec.property:
        actual_property = existing_field.get("property")
        if not isinstance(actual_property, Mapping):
            raise SchemaError(
                "Field {}.{} is missing required property settings".format(
                    table_spec.name, field_spec.name
                )
            )
        for key, expected_value in field_spec.property.items():
            if actual_property.get(key) != expected_value:
                raise SchemaError(
                    "Field {}.{} property {} is {}, expected {}".format(
                        table_spec.name,
                        field_spec.name,
                        key,
                        actual_property.get(key),
                        expected_value,
                    )
                )
    elif field_spec.field_type == USER:
        actual_property = existing_field.get("property")
        if isinstance(actual_property, Mapping) and actual_property.get(
            "multiple", False
        ):
            raise SchemaError(
                "Field {}.{} property multiple is True, expected False".format(
                    table_spec.name, field_spec.name
                )
            )
    if field_spec.field_type != SINGLE_LINK:
        return
    target_id = table_ids.get(field_spec.target_table or "")
    if target_id is None:
        raise SchemaError(
            "Field {}.{} target table is missing: {}".format(
                table_spec.name,
                field_spec.name,
                field_spec.target_table,
            )
        )
    property_data = existing_field.get("property")
    if isinstance(property_data, Mapping):
        actual_target = property_data.get("table_id")
        actual_multiple = property_data.get("multiple", False)
        if actual_target == target_id and actual_multiple is False:
            return
    raise SchemaError(
        "Field {}.{} links to the wrong table".format(
            table_spec.name, field_spec.name
        )
    )


def _preflight_existing_schema(
    tables_by_name: Mapping[str, Mapping[str, Any]],
    fields_by_table: Mapping[str, Any],
) -> None:
    effective_tables = dict(tables_by_name)
    if "需求主表" not in effective_tables and "数据表" in effective_tables:
        effective_tables["需求主表"] = effective_tables["数据表"]
    table_ids = {
        name: _table_id(table) for name, table in effective_tables.items()
    }

    for table_spec in SCHEMA:
        table = effective_tables.get(table_spec.name)
        if table is None:
            continue
        table_id = _table_id(table)
        if table_id not in fields_by_table:
            raise SchemaError(
                "Missing field response for table {}".format(table_spec.name)
            )
        fields = _field_items(fields_by_table[table_id])
        _require_unique_names(fields, _field_name, "field")
        fields_by_name = {_field_name(field): field for field in fields}

        expected_primary_name = table_spec.primary_field_name
        if (
            table_spec.name == "需求主表"
            and "需求编号" not in fields_by_name
            and "文本" in fields_by_name
        ):
            expected_primary_name = "文本"
        _validate_primary_field(
            table_spec.name, expected_primary_name, fields
        )

        for field_spec in table_spec.fields:
            existing_field = fields_by_name.get(field_spec.name)
            if existing_field is not None:
                _validate_existing_field(
                    table_spec, field_spec, existing_field, table_ids
                )


def _validate_primary_field(
    table_name: str,
    expected_name: str,
    fields: Sequence[Mapping[str, Any]],
) -> None:
    primary_fields = [field for field in fields if field.get("is_primary") is True]
    if len(primary_fields) != 1:
        raise SchemaError(
            "Table {} must have exactly one primary field".format(table_name)
        )
    actual_name = _field_name(primary_fields[0])
    if actual_name != expected_name:
        raise SchemaError(
            "Field {}.{} must be the primary field; found {}".format(
                table_name, expected_name, actual_name
            )
        )


def _require_unique_names(
    items: Sequence[Mapping[str, Any]],
    name_getter: Callable[[Mapping[str, Any]], str],
    item_kind: str,
) -> None:
    names = [name_getter(item) for item in items]
    duplicates = sorted(
        name for name, count in Counter(names).items() if count > 1
    )
    if duplicates:
        label = "table" if item_kind == "table" else "field"
        raise SchemaError(
            "Duplicate {} name is ambiguous: {}".format(
                label, ", ".join(duplicates)
            )
        )


def _data_object(value: Mapping[str, Any]) -> Mapping[str, Any]:
    data = value.get("data")
    if isinstance(data, Mapping):
        return data
    return value


def _table_items(meta: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    data = _data_object(meta)
    tables = data.get("tables", data.get("items", []))
    if not isinstance(tables, list):
        raise SchemaError("Bitable metadata did not include a table list")
    return [table for table in tables if isinstance(table, Mapping)]


def _field_items(value: Any) -> List[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        data = _data_object(value)
        fields = data.get("items", data.get("fields", []))
    else:
        fields = value
    if not isinstance(fields, list):
        raise SchemaError("Bitable field response did not include a field list")
    return [field for field in fields if isinstance(field, Mapping)]


def _app_token(meta: Mapping[str, Any]) -> str:
    data = _data_object(meta)
    app_token = data.get("app_token")
    if not isinstance(app_token, str) or not app_token:
        raise SchemaError("Bitable metadata did not include app_token")
    return app_token


def _table_id(table: Mapping[str, Any]) -> str:
    table_id = table.get("table_id")
    if not isinstance(table_id, str) or not table_id:
        raise SchemaError("Bitable table did not include table_id")
    return table_id


def _table_name(table: Mapping[str, Any]) -> str:
    name = table.get("name", table.get("table_name"))
    if not isinstance(name, str) or not name:
        raise SchemaError("Bitable table did not include a name")
    return name


def _field_id(field: Mapping[str, Any]) -> Optional[str]:
    field_id = field.get("field_id")
    return field_id if isinstance(field_id, str) and field_id else None


def _field_name(field: Mapping[str, Any]) -> str:
    name = field.get("field_name", field.get("name"))
    if not isinstance(name, str) or not name:
        raise SchemaError("Bitable field did not include a name")
    return name
