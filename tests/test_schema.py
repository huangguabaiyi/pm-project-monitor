from copy import deepcopy

from requirement_monitor.schema import (
    BASIC_CONFIG_SEEDS,
    SCHEMA,
    Operation,
    build_schema_plan,
    initialize_schema,
)


def test_existing_data_table_is_renamed_and_missing_tables_are_created():
    meta = {
        "app_token": "app",
        "tables": [{"table_id": "tbl-main", "name": "数据表"}],
    }
    fields = {
        "tbl-main": [
            {"field_id": "fld-primary", "field_name": "文本", "type": 1}
        ]
    }

    plan = build_schema_plan(meta, fields)

    assert plan[0] == Operation(
        "rename_table", {"table_id": "tbl-main", "name": "需求主表"}
    )
    assert plan[1] == Operation(
        "rename_field",
        {
            "table_id": "tbl-main",
            "field_id": "fld-primary",
            "name": "需求编号",
        },
    )
    assert not any(
        item.kind == "create_field"
        and item.payload.get("table_id") == "tbl-main"
        and item.payload.get("name") == "需求编号"
        for item in plan
    )
    assert {
        item.payload["name"]
        for item in plan
        if item.kind == "create_table"
    } == {
        "进展节点表",
        "阻塞项表",
        "项目配置表",
        "基础配置表",
        "通知记录表",
    }


def test_schema_manifest_has_only_the_approved_fields_and_types():
    assert {table.name for table in SCHEMA} == {
        "需求主表",
        "进展节点表",
        "阻塞项表",
        "项目配置表",
        "基础配置表",
        "通知记录表",
    }
    assert {
        field.name: field.field_type
        for table in SCHEMA
        if table.name == "需求主表"
        for field in table.fields
    } == {
        "需求编号": 1,
        "需求名称": 1,
        "项目名称": 1,
        "当前环节": 3,
        "项目负责人": 11,
        "产品负责人": 11,
        "目标版本": 1,
        "合板时间": 5,
        "计划上线时间": 5,
        "需求宣讲是否完成": 7,
        "是否允许通知": 7,
        "是否归档": 7,
        "项目配置关联": 18,
        "需求补充说明": 1,
        "备注": 1,
        "当前风险等级": 3,
        "风险原因": 1,
        "预计完成时间": 5,
        "剩余缓冲天数": 2,
        "最近检查时间": 5,
        "最近通知时间": 5,
    }
    assert {
        field.name: field.field_type
        for table in SCHEMA
        if table.name == "进展节点表"
        for field in table.fields
    } == {
        "关联需求": 18,
        "交付域": 3,
        "工作类型": 3,
        "节点名称": 1,
        "负责人": 11,
        "计划开始时间": 5,
        "计划完成时间": 5,
        "实际完成时间": 5,
        "当前状态": 3,
        "进展说明": 1,
        "最后更新时间": 5,
        "系统风险等级": 3,
        "系统风险原因": 1,
        "最晚安全DDL": 5,
    }
    assert {
        field.name: field.field_type
        for table in SCHEMA
        if table.name == "项目配置表"
        for field in table.fields
    } == {
        "项目名称": 1,
        "周期计算方式": 3,
        "AT 最少测试天数": 2,
        "PV 最少测试天数": 2,
        "Bug 修复预留天数": 2,
        "线上回归最少天数": 2,
        "服务端专项测试天数": 2,
        "客户端专项测试天数": 2,
        "车辆专项测试天数": 2,
        "可上线日期": 1,
        "上线截止时间": 1,
        "是否启用 LLM": 7,
        "项目补充说明": 1,
    }
    assert {
        field.name: field.field_type
        for table in SCHEMA
        if table.name == "通知记录表"
        for field in table.fields
    } == {
        "需求": 18,
        "通知类型": 3,
        "风险等级": 3,
        "消息摘要": 1,
        "通知对象": 11,
        "发送时间": 5,
        "发送结果": 3,
        "错误信息": 1,
        "是否使用 LLM": 7,
        "LLM 降级原因": 1,
        "通知指纹": 1,
    }


def test_basic_config_seed_names_match_the_approved_defaults():
    process_names = [
        record["配置名称"]
        for record in BASIC_CONFIG_SEEDS
        if record["配置类型"] == "环节"
    ]

    assert process_names[10:14] == [
        "AT 测试第一轮",
        "AT 测试第二轮",
        "PV 测试第一轮",
        "PV 测试第二轮",
    ]
    assert set(BASIC_CONFIG_SEEDS[0]) == {
        "配置名称",
        "配置类型",
        "排序",
        "是否启用",
    }


def test_complete_schema_has_empty_plan_and_links_use_target_table_ids():
    meta, fields = complete_schema_state()

    assert build_schema_plan(meta, fields) == []

    fields["tbl-progress"] = [
        field
        for field in fields["tbl-progress"]
        if field["field_name"] != "关联需求"
    ]
    plan = build_schema_plan(meta, fields)

    assert plan == [
        Operation(
            "create_field",
            {
                "table_id": "tbl-progress",
                "name": "关联需求",
                "field_type": 18,
                "property": {"table_id": "tbl-main", "multiple": False},
            },
        )
    ]


def test_initialize_schema_applies_two_phases_and_seed_is_idempotent():
    client = FakeSchemaClient()

    first_operations = initialize_schema(
        "https://example.feishu.cn/base/app", apply=True, client=client
    )
    second_operations = initialize_schema(
        "https://example.feishu.cn/base/app", apply=True, client=client
    )

    first_link_call = next(
        index
        for index, call in enumerate(client.calls)
        if call[0] == "create_field" and call[4] == 18
    )
    last_create_table_call = max(
        index for index, call in enumerate(client.calls) if call[0] == "create_table"
    )
    assert first_link_call > last_create_table_call
    assert any(operation.kind == "seed_records" for operation in first_operations)
    assert second_operations == []
    assert len(client.records_by_table["tbl-basic"]) == len(BASIC_CONFIG_SEEDS)
    assert len(BASIC_CONFIG_SEEDS) == 35


def test_initialize_schema_rediscovers_tables_for_a_table_scoped_url():
    client = TableScopedMetaClient()

    operations = initialize_schema(
        "https://example.feishu.cn/base/app?table=tbl-main",
        apply=False,
        client=client,
    )

    assert operations[0] == Operation(
        "rename_table", {"table_id": "tbl-main", "name": "需求主表"}
    )
    assert [call for call in client.calls if call[0] == "meta"][:2] == [
        ("meta", "https://example.feishu.cn/base/app?table=tbl-main"),
        ("meta", "app"),
    ]


def complete_schema_state():
    table_ids = {
        "需求主表": "tbl-main",
        "进展节点表": "tbl-progress",
        "阻塞项表": "tbl-blocker",
        "项目配置表": "tbl-project",
        "基础配置表": "tbl-basic",
        "通知记录表": "tbl-notification",
    }
    meta = {
        "app_token": "app",
        "tables": [
            {"table_id": table_id, "name": name}
            for name, table_id in table_ids.items()
        ],
    }
    fields = {}
    for table in SCHEMA:
        table_id = table_ids[table.name]
        fields[table_id] = []
        for index, field in enumerate(table.fields):
            item = {
                "field_id": f"fld-{table_id}-{index}",
                "field_name": field.name,
                "type": field.field_type,
            }
            if field.target_table:
                item["property"] = {
                    "table_id": table_ids[field.target_table],
                    "multiple": False,
                }
            fields[table_id].append(item)
    return meta, fields


class FakeSchemaClient:
    def __init__(self):
        self.app_token = "app"
        self.tables = [{"table_id": "tbl-main", "name": "数据表"}]
        self.fields_by_table = {
            "tbl-main": [
                {
                    "field_id": "fld-main-primary",
                    "field_name": "文本",
                    "type": 1,
                }
            ]
        }
        self.records_by_table = {}
        self.calls = []

    def meta(self, url_or_token):
        self.calls.append(("meta", url_or_token))
        return {"app_token": self.app_token, "tables": deepcopy(self.tables)}

    def fields(self, app_token, table_id):
        self.calls.append(("fields", app_token, table_id))
        return {"items": deepcopy(self.fields_by_table[table_id])}

    def rename_table(self, app_token, table_id, name):
        self.calls.append(("rename_table", app_token, table_id, name))
        next(item for item in self.tables if item["table_id"] == table_id)[
            "name"
        ] = name
        return {}

    def update_field(self, app_token, table_id, field_id, *, name=None, property=None):
        self.calls.append(
            ("update_field", app_token, table_id, field_id, name, property)
        )
        field = next(
            item
            for item in self.fields_by_table[table_id]
            if item["field_id"] == field_id
        )
        if name is not None:
            field["field_name"] = name
        if property is not None:
            field["property"] = property
        return {}

    def create_table(self, app_token, name, fields, *, default_view_name=None):
        self.calls.append(("create_table", app_token, name, deepcopy(fields)))
        table_id = {
            "进展节点表": "tbl-progress",
            "阻塞项表": "tbl-blocker",
            "项目配置表": "tbl-project",
            "基础配置表": "tbl-basic",
            "通知记录表": "tbl-notification",
        }[name]
        self.tables.append({"table_id": table_id, "name": name})
        self.fields_by_table[table_id] = [
            {
                "field_id": f"fld-{table_id}-{index}",
                "field_name": field["field_name"],
                "type": field["type"],
            }
            for index, field in enumerate(fields)
        ]
        self.records_by_table[table_id] = []
        return {"table_id": table_id}

    def create_field(
        self, app_token, table_id, name, field_type, *, property=None, ui_type=None
    ):
        self.calls.append(
            ("create_field", app_token, table_id, name, field_type, property)
        )
        self.fields_by_table[table_id].append(
            {
                "field_id": f"fld-{table_id}-{len(self.fields_by_table[table_id])}",
                "field_name": name,
                "type": field_type,
                "property": deepcopy(property),
            }
        )
        return {}

    def records(self, app_token, table_id, **kwargs):
        self.calls.append(("records", app_token, table_id, kwargs))
        return {"items": deepcopy(self.records_by_table.get(table_id, []))}

    def batch_create(self, app_token, table_id, records):
        self.calls.append(("batch_create", app_token, table_id, deepcopy(records)))
        self.records_by_table.setdefault(table_id, []).extend(
            {"fields": deepcopy(record)} for record in records
        )
        return {}


class TableScopedMetaClient(FakeSchemaClient):
    def meta(self, url_or_token):
        self.calls.append(("meta", url_or_token))
        if url_or_token != self.app_token:
            return {"app_token": self.app_token, "table_id": "tbl-main"}
        return {"app_token": self.app_token, "tables": deepcopy(self.tables)}
