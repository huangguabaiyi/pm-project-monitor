from copy import deepcopy
from datetime import datetime
import json
import os
from zoneinfo import ZoneInfo

import pytest

from requirement_monitor.models import BaseConfig, NodeRisk, RequirementRisk, RiskLevel
from requirement_monitor.repository import (
    BitableRepository,
    RepositoryDataError,
    RepositorySchemaError,
    parse_snapshot,
)
from requirement_monitor.schema import BASIC_CONFIG_SEEDS, SCHEMA, SINGLE_LINK


SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def raw_tables():
    merge_at = datetime(2026, 7, 28, 10, 0, tzinfo=SHANGHAI)
    planned_start = datetime(2026, 7, 25, 9, 30, tzinfo=SHANGHAI)
    return {
        "需求主表": [
            {
                "record_id": "rec-req-1",
                "fields": {
                    "需求编号": "REQ-1",
                    "需求名称": "登录优化",
                    "项目名称": "米家",
                    "当前环节": {"name": "开发"},
                    "项目负责人": [
                        {"open_id": "ou-project", "name": "项目负责人"}
                    ],
                    "产品负责人": [{"id": "ou-product", "name": "产品负责人"}],
                    "目标版本": "10.2",
                    "合板时间": int(merge_at.timestamp() * 1000),
                    "计划上线时间": "2026-07-30T18:00:00+08:00",
                    "需求宣讲是否完成": True,
                    "是否允许通知": True,
                    "是否归档": False,
                    "项目配置关联": [{"record_id": "rec-config-1"}],
                    "需求补充说明": "重点关注登录链路",
                },
            }
        ],
        "进展节点表": [
            {
                "record_id": "rec-node-1",
                "fields": {
                    "关联需求": ["rec-req-1"],
                    "交付域": {"text": "客户端"},
                    "工作类型": {"value": "研发"},
                    "节点名称": "客户端开发",
                    "负责人": [{"user_id": "ou-node", "name": "节点负责人"}],
                    "计划开始时间": int(planned_start.timestamp() * 1000),
                    "计划完成时间": "2026-07-28T02:00:00Z",
                    "实际完成时间": None,
                    "当前状态": {"name": "进行中"},
                    "进展说明": "联调中",
                    "最后更新时间": "2026-07-24T17:20:00",
                },
            }
        ],
        "阻塞项表": [
            {
                "record_id": "rec-blocker-1",
                "fields": {
                    "关联需求": [{"record_id": "rec-req-1"}],
                    "关联节点": {"link_record_ids": ["rec-node-1"]},
                    "阻塞事项": "等待接口",
                    "责任人": [{"open_id": "ou-blocker", "name": "阻塞负责人"}],
                    "发现时间": "2026-07-24T09:00:00+08:00",
                    "计划解决时间": "2026-07-25T12:00:00+08:00",
                    "实际解决时间": None,
                    "当前状态": {"name": "处理中"},
                    "是否影响合板": True,
                    "处理说明": "服务端处理中",
                },
            }
        ],
        "项目配置表": [
            {
                "record_id": "rec-config-1",
                "fields": {
                    "项目名称": "米家",
                    "周期计算方式": {"name": "工作日"},
                    "AT 最少测试天数": 3,
                    "PV 最少测试天数": 2,
                    "Bug 修复预留天数": 1,
                    "线上回归最少天数": 1,
                    "服务端专项测试天数": 2,
                    "客户端专项测试天数": 2,
                    "车辆专项测试天数": 0,
                    "可上线日期": "周一, 周三, 周五",
                    "上线截止时间": "18:30",
                    "是否启用 LLM": True,
                    "项目补充说明": "核心项目",
                },
            }
        ],
        "基础配置表": [
            {
                "record_id": "rec-base-{}".format(index),
                "fields": dict(fields),
            }
            for index, fields in enumerate(BASIC_CONFIG_SEEDS, start=1)
        ],
        "通知记录表": [],
    }


def test_parses_people_dates_single_selects_and_link_record_ids(raw_tables):
    snapshot, issues = parse_snapshot(raw_tables)

    assert issues == []
    requirement = snapshot.requirements[0]
    assert requirement.project_owner_id == "ou-project"
    assert requirement.project_owner_name == "项目负责人"
    assert requirement.product_owner_id == "ou-product"
    assert requirement.current_stage == "开发"
    assert requirement.project_config_record_id == "rec-config-1"
    assert requirement.merge_at == datetime(2026, 7, 28, 10, 0, tzinfo=SHANGHAI)
    assert requirement.launch_at == datetime(2026, 7, 30, 18, 0, tzinfo=SHANGHAI)

    node = snapshot.nodes[0]
    assert node.requirement_id == "REQ-1"
    assert node.owner_id == "ou-node"
    assert node.domain == "客户端"
    assert node.work_type == "研发"
    assert node.planned_end == datetime(2026, 7, 28, 10, 0, tzinfo=SHANGHAI)
    assert node.updated_at == datetime(2026, 7, 24, 17, 20, tzinfo=SHANGHAI)

    blocker = snapshot.blockers[0]
    assert blocker.requirement_id == "REQ-1"
    assert blocker.node_record_id == "rec-node-1"
    assert blocker.owner_id == "ou-blocker"

    project_config = snapshot.project_configs[0]
    assert project_config.duration_mode == "workday"
    assert project_config.launch_weekdays == {0, 2, 4}


def test_invalid_node_date_isolated_from_valid_sibling(raw_tables):
    valid_node = deepcopy(raw_tables["进展节点表"][0])
    valid_node["record_id"] = "rec-node-2"
    valid_node["fields"]["节点名称"] = "服务端开发"
    raw_tables["进展节点表"].append(valid_node)
    raw_tables["进展节点表"][0]["fields"]["计划完成时间"] = "not-a-date"

    snapshot, issues = parse_snapshot(raw_tables)

    assert [item.requirement_id for item in snapshot.requirements] == ["REQ-1"]
    assert [item.record_id for item in snapshot.nodes] == ["rec-node-2"]
    assert len(issues) == 1
    assert issues[0].table_name == "进展节点表"
    assert issues[0].record_id == "rec-node-1"
    assert issues[0].requirement_id == "REQ-1"
    assert issues[0].field_name == "计划完成时间"
    assert issues[0].current_value == "not-a-date"
    assert issues[0].expected_format
    assert issues[0].fix_suggestion
    assert issues[0].skip_scope == "record"


def test_requirement_missing_fields_isolated_while_valid_records_still_parse(
    raw_tables,
):
    raw_tables["需求主表"].append({"record_id": "rec-missing-fields"})

    snapshot, issues = parse_snapshot(raw_tables)

    assert [item.requirement_id for item in snapshot.requirements] == ["REQ-1"]
    assert [item.record_id for item in snapshot.nodes] == ["rec-node-1"]
    assert [item.record_id for item in snapshot.blockers] == ["rec-blocker-1"]
    assert len(issues) == 1
    assert issues[0].record_id == "rec-missing-fields"
    assert issues[0].field_name == "fields"
    assert issues[0].current_value is None
    assert issues[0].skip_scope == "requirement"


def test_blank_invalid_requirement_id_is_reported_as_none(raw_tables):
    raw_tables["需求主表"][0]["fields"]["需求编号"] = "   "

    snapshot, issues = parse_snapshot(raw_tables)

    assert snapshot.requirements == []
    assert len(issues) == 1
    assert issues[0].field_name == "需求编号"
    assert issues[0].requirement_id is None


def test_second_based_numeric_datetime_is_rejected_as_record_issue(raw_tables):
    raw_tables["需求主表"][0]["fields"]["合板时间"] = 1785204000

    snapshot, issues = parse_snapshot(raw_tables)

    assert snapshot.requirements == []
    assert len(issues) == 1
    assert issues[0].field_name == "合板时间"


@pytest.mark.parametrize(
    "people",
    [
        [],
        [
            {"open_id": "ou-one", "name": "一号负责人"},
            {"open_id": "ou-two", "name": "二号负责人"},
        ],
    ],
)
def test_single_owner_field_requires_exactly_one_person(raw_tables, people):
    raw_tables["需求主表"][0]["fields"]["项目负责人"] = people

    snapshot, issues = parse_snapshot(raw_tables)

    assert snapshot.requirements == []
    assert len(issues) == 1
    assert issues[0].field_name == "项目负责人"


def test_child_single_owner_field_rejects_multiple_people(raw_tables):
    raw_tables["进展节点表"][0]["fields"]["负责人"] = [
        {"open_id": "ou-one", "name": "一号负责人"},
        {"open_id": "ou-two", "name": "二号负责人"},
    ]

    snapshot, issues = parse_snapshot(raw_tables)

    assert [item.requirement_id for item in snapshot.requirements] == ["REQ-1"]
    assert snapshot.nodes == []
    assert len(issues) == 1
    assert issues[0].field_name == "负责人"


def test_invalid_requirement_excludes_only_it_and_its_children(raw_tables):
    second_requirement = deepcopy(raw_tables["需求主表"][0])
    second_requirement["record_id"] = "rec-req-2"
    second_requirement["fields"]["需求编号"] = "REQ-2"
    second_requirement["fields"]["需求名称"] = "设备页优化"
    raw_tables["需求主表"].append(second_requirement)

    second_node = deepcopy(raw_tables["进展节点表"][0])
    second_node["record_id"] = "rec-node-2"
    second_node["fields"]["关联需求"] = ["rec-req-2"]
    raw_tables["进展节点表"].append(second_node)

    second_blocker = deepcopy(raw_tables["阻塞项表"][0])
    second_blocker["record_id"] = "rec-blocker-2"
    second_blocker["fields"]["关联需求"] = ["rec-req-2"]
    second_blocker["fields"]["关联节点"] = ["rec-node-2"]
    raw_tables["阻塞项表"].append(second_blocker)

    raw_tables["需求主表"][0]["fields"]["合板时间"] = "bad-date"

    snapshot, issues = parse_snapshot(raw_tables)

    assert [item.requirement_id for item in snapshot.requirements] == ["REQ-2"]
    assert [item.record_id for item in snapshot.nodes] == ["rec-node-2"]
    assert [item.record_id for item in snapshot.blockers] == ["rec-blocker-2"]
    assert len(issues) == 1
    assert issues[0].table_name == "需求主表"
    assert issues[0].field_name == "合板时间"
    assert issues[0].current_value == "bad-date"
    assert issues[0].skip_scope == "requirement"


def test_invalid_blocker_excludes_only_that_blocker(raw_tables):
    valid_blocker = deepcopy(raw_tables["阻塞项表"][0])
    valid_blocker["record_id"] = "rec-blocker-2"
    raw_tables["阻塞项表"].append(valid_blocker)
    raw_tables["阻塞项表"][0]["fields"]["发现时间"] = "invalid"

    snapshot, issues = parse_snapshot(raw_tables)

    assert [item.record_id for item in snapshot.blockers] == ["rec-blocker-2"]
    assert [item.record_id for item in snapshot.nodes] == ["rec-node-1"]
    assert len(issues) == 1
    assert issues[0].field_name == "发现时间"
    assert issues[0].skip_scope == "record"


def test_invalid_project_config_does_not_block_valid_config(raw_tables):
    invalid_config = deepcopy(raw_tables["项目配置表"][0])
    invalid_config["record_id"] = "rec-config-bad"
    invalid_config["fields"]["上线截止时间"] = "25:00"
    raw_tables["项目配置表"].append(invalid_config)

    snapshot, issues = parse_snapshot(raw_tables)

    assert [item.record_id for item in snapshot.project_configs] == ["rec-config-1"]
    assert len(issues) == 1
    assert issues[0].table_name == "项目配置表"
    assert issues[0].record_id == "rec-config-bad"
    assert issues[0].field_name == "上线截止时间"
    assert issues[0].skip_scope == "record"


def test_ineligible_requirements_are_filtered_after_parsing(raw_tables):
    raw_tables["需求主表"][0]["fields"]["需求宣讲是否完成"] = False

    snapshot, issues = parse_snapshot(raw_tables)

    assert issues == []
    assert snapshot.eligible_requirements() == []


class FakeCLI:
    def __init__(self, meta, pages=None, field_responses=None):
        self.meta_payload = meta
        self.pages = pages or {}
        self.field_responses = field_responses or {}
        self.record_calls = []
        self.field_calls = []
        self.batch_updates = []
        self.batch_creates = []
        self.temp_files = []

    def meta(self, url_or_token):
        assert url_or_token == "base-token"
        return self.meta_payload

    def records(
        self,
        app_token,
        table_id,
        *,
        page_size=None,
        page_token=None,
        automatic_fields=False,
    ):
        self.record_calls.append(
            {
                "app_token": app_token,
                "table_id": table_id,
                "page_size": page_size,
                "page_token": page_token,
                "automatic_fields": automatic_fields,
            }
        )
        return self.pages[(table_id, page_token)]

    def fields(self, app_token, table_id):
        self.field_calls.append((app_token, table_id))
        return self.field_responses[table_id]

    def batch_update(self, app_token, table_id, records=None, *, file_path=None):
        loaded_records = self._load_batch(records, file_path)
        self.batch_updates.append((app_token, table_id, loaded_records))
        return {"data": {"records": loaded_records}}

    def batch_create(self, app_token, table_id, records=None, *, file_path=None):
        loaded_records = self._load_batch(records, file_path)
        self.batch_creates.append((app_token, table_id, loaded_records))
        return {"data": {"records": loaded_records}}

    def _load_batch(self, records, file_path):
        if file_path is None:
            return list(records)
        self.temp_files.append(
            {
                "path": file_path,
                "mode": os.stat(file_path).st_mode & 0o777,
                "exists_during_call": os.path.exists(file_path),
            }
        )
        with open(file_path, "r", encoding="utf-8") as input_file:
            return json.load(input_file)


def repository_meta(*table_names):
    return {
        "data": {
            "app_token": "app-token",
            "tables": [
                {"table_id": "tbl-{}".format(index), "name": name}
                for index, name in enumerate(table_names)
            ],
        }
    }


def repository_fields(meta):
    table_ids = {
        table["name"]: table["table_id"] for table in meta["data"]["tables"]
    }
    responses = {}
    for table_spec in SCHEMA:
        table_id = table_ids[table_spec.name]
        items = []
        for index, field_spec in enumerate(table_spec.fields):
            field = {
                "field_id": "fld-{}-{}".format(table_id, index),
                "field_name": field_spec.name,
                "type": field_spec.field_type,
                "is_primary": index == 0,
            }
            if field_spec.field_type == SINGLE_LINK:
                field["property"] = {
                    "table_id": table_ids[field_spec.target_table],
                    "multiple": False,
                }
            items.append(field)
        responses[table_id] = {"data": {"items": items}}
    return responses


def test_load_snapshot_discovers_exact_names_and_reads_all_pages(raw_tables):
    table_names = (
        "需求主表备份",
        "需求主表",
        "进展节点表",
        "阻塞项表",
        "项目配置表",
        "基础配置表",
        "通知记录表",
    )
    meta = repository_meta(*table_names)
    table_ids = {
        table["name"]: table["table_id"] for table in meta["data"]["tables"]
    }
    pages = {
        (table_ids["需求主表"], None): {
            "data": {
                "items": raw_tables["需求主表"],
                "has_more": True,
                "page_token": "next-page",
            }
        },
        (table_ids["需求主表"], "next-page"): {
            "data": {"records": [], "has_more": False}
        },
        (table_ids["进展节点表"], None): {
            "data": {"records": raw_tables["进展节点表"], "has_more": False}
        },
        (table_ids["阻塞项表"], None): {
            "data": {"items": raw_tables["阻塞项表"], "has_more": False}
        },
        (table_ids["项目配置表"], None): {
            "data": {"records": raw_tables["项目配置表"], "has_more": False}
        },
        (table_ids["基础配置表"], None): {
            "data": {"items": raw_tables["基础配置表"], "has_more": False}
        },
    }
    client = FakeCLI(meta, pages, repository_fields(meta))

    snapshot, issues = BitableRepository("base-token", client=client).load_snapshot()

    assert issues == []
    assert [item.requirement_id for item in snapshot.requirements] == ["REQ-1"]
    assert len(client.record_calls) == 6
    assert len(client.field_calls) == 6
    assert all(call["page_size"] == 500 for call in client.record_calls)
    assert all(call["automatic_fields"] is True for call in client.record_calls)
    assert [
        call["page_token"]
        for call in client.record_calls
        if call["table_id"] == table_ids["需求主表"]
    ] == [None, "next-page"]
    assert not any(
        call["table_id"] == table_ids["需求主表备份"]
        for call in client.record_calls
    )
    assert not any(
        call["table_id"] == table_ids["通知记录表"]
        for call in client.record_calls
    )
    assert ("app-token", table_ids["通知记录表"]) in client.field_calls


def test_load_snapshot_raises_when_a_key_table_is_missing():
    client = FakeCLI(repository_meta("需求主表", "进展节点表", "项目配置表"))

    with pytest.raises(RepositorySchemaError, match="阻塞项表"):
        BitableRepository("base-token", client=client).load_snapshot()


def test_load_snapshot_validates_schema_before_reading_empty_tables():
    table_names = tuple(table.name for table in SCHEMA)
    meta = repository_meta(*table_names)
    fields = repository_fields(meta)
    requirement_table = next(
        table for table in meta["data"]["tables"] if table["name"] == "需求主表"
    )
    fields[requirement_table["table_id"]]["data"]["items"] = [
        field
        for field in fields[requirement_table["table_id"]]["data"]["items"]
        if field["field_name"] != "项目负责人"
    ]
    client = FakeCLI(meta, field_responses=fields)

    with pytest.raises(RepositorySchemaError, match="项目负责人"):
        BitableRepository("base-token", client=client).load_snapshot()

    assert client.record_calls == []


def test_load_snapshot_rejects_wrong_single_link_target():
    table_names = tuple(table.name for table in SCHEMA)
    meta = repository_meta(*table_names)
    fields = repository_fields(meta)
    node_table = next(
        table for table in meta["data"]["tables"] if table["name"] == "进展节点表"
    )
    link = next(
        field
        for field in fields[node_table["table_id"]]["data"]["items"]
        if field["field_name"] == "关联需求"
    )
    link["property"] = {"table_id": "tbl-wrong", "multiple": True}

    with pytest.raises(RepositorySchemaError, match="关联需求"):
        BitableRepository(
            "base-token", client=FakeCLI(meta, field_responses=fields)
        ).load_snapshot()


def test_parse_snapshot_loads_base_configuration(raw_tables):
    snapshot, issues = parse_snapshot(raw_tables)

    assert issues == []
    assert isinstance(snapshot.base_configs[0], BaseConfig)
    assert snapshot.enabled_config_names("环节")[:2] == ["需求撰写", "内部评审"]


def test_dynamic_domains_work_types_and_test_roles_are_configuration_driven(
    raw_tables,
):
    raw_tables["基础配置表"].extend(
        [
            {
                "record_id": "rec-custom-domain",
                "fields": {
                    "配置名称": "合作伙伴",
                    "配置类型": "交付域",
                    "排序": 99,
                    "是否启用": True,
                    "备注": "动态参与方",
                },
            },
            {
                "record_id": "rec-custom-work",
                "fields": {
                    "配置名称": "验收",
                    "配置类型": "工作类型",
                    "排序": 99,
                    "是否启用": True,
                    "备注": "动态工作类型",
                },
            },
            {
                "record_id": "rec-custom-role",
                "fields": {
                    "配置名称": "安全测试",
                    "配置类型": "测试角色",
                    "排序": 99,
                    "是否启用": True,
                    "备注": "动态测试角色",
                },
            },
        ]
    )
    raw_tables["进展节点表"][0]["fields"]["交付域"] = {"name": "合作伙伴"}
    raw_tables["进展节点表"][0]["fields"]["工作类型"] = {"name": "验收"}

    snapshot, issues = parse_snapshot(raw_tables)

    assert issues == []
    assert snapshot.nodes[0].domain == "合作伙伴"
    assert snapshot.nodes[0].work_type == "验收"
    assert "安全测试" in snapshot.enabled_config_names("测试角色")


def test_checklist_planned_end_is_derived_from_launch_time(raw_tables):
    node_fields = raw_tables["进展节点表"][0]["fields"]
    node_fields.update(
        {
            "交付域": {"name": "服务端"},
            "工作类型": {"name": "发布"},
            "节点名称": "上线 Checklist",
        }
    )
    node_fields.pop("计划完成时间")

    snapshot, issues = parse_snapshot(raw_tables)

    assert issues == []
    assert snapshot.nodes[0].planned_end == datetime(
        2026, 7, 29, 18, 0, tzinfo=SHANGHAI
    )


def test_checklist_without_launch_time_is_requirement_data_error(raw_tables):
    raw_tables["需求主表"][0]["fields"]["计划上线时间"] = None
    node_fields = raw_tables["进展节点表"][0]["fields"]
    node_fields.update(
        {
            "交付域": {"name": "服务端"},
            "工作类型": {"name": "发布"},
            "节点名称": "上线 Checklist",
        }
    )
    node_fields.pop("计划完成时间")

    snapshot, issues = parse_snapshot(raw_tables)

    assert snapshot.nodes == []
    assert issues[0].field_name == "计划上线时间"


def test_load_snapshot_rejects_empty_base_configuration(raw_tables):
    table_names = tuple(table.name for table in SCHEMA)
    meta = repository_meta(*table_names)
    table_ids = {
        table["name"]: table["table_id"] for table in meta["data"]["tables"]
    }
    pages = {
        (table_ids[name], None): {
            "data": {"records": raw_tables[name], "has_more": False}
        }
        for name in table_names
    }
    pages[(table_ids["基础配置表"], None)] = {
        "data": {"records": [], "has_more": False}
    }

    with pytest.raises(RepositoryDataError, match="基础配置表"):
        BitableRepository(
            "base-token",
            client=FakeCLI(meta, pages, repository_fields(meta)),
        ).load_snapshot()


def test_load_snapshot_rejects_missing_required_base_seed(raw_tables):
    table_names = tuple(table.name for table in SCHEMA)
    meta = repository_meta(*table_names)
    table_ids = {
        table["name"]: table["table_id"] for table in meta["data"]["tables"]
    }
    raw_tables["基础配置表"] = [
        record
        for record in raw_tables["基础配置表"]
        if record["fields"]["配置名称"] != "线上回归"
    ]
    pages = {
        (table_ids[name], None): {
            "data": {"records": raw_tables[name], "has_more": False}
        }
        for name in table_names
    }

    with pytest.raises(RepositoryDataError, match="线上回归"):
        BitableRepository(
            "base-token",
            client=FakeCLI(meta, pages, repository_fields(meta)),
        ).load_snapshot()


class SequenceRecordsCLI(FakeCLI):
    def __init__(self, meta, responses):
        super().__init__(meta)
        self.responses = iter(responses)

    def records(self, *args, **kwargs):
        self.record_calls.append({"args": args, **kwargs})
        try:
            return next(self.responses)
        except StopIteration:
            raise AssertionError("pagination loop was not detected") from None


def test_load_snapshot_rejects_repeated_pagination_token():
    meta = repository_meta(*(table.name for table in SCHEMA))
    client = SequenceRecordsCLI(
        meta,
        [
            {"data": {"records": [], "has_more": True, "page_token": "page-1"}},
            {"data": {"records": [], "has_more": True, "page_token": "page-1"}},
        ],
    )
    client.field_responses = repository_fields(meta)

    with pytest.raises(RepositorySchemaError, match="pagination"):
        BitableRepository("base-token", client=client).load_snapshot()


def test_load_snapshot_rejects_has_more_without_new_token():
    meta = repository_meta(*(table.name for table in SCHEMA))
    client = SequenceRecordsCLI(
        meta,
        [{"data": {"records": [], "has_more": True, "page_token": ""}}],
    )
    client.field_responses = repository_fields(meta)

    with pytest.raises(RepositorySchemaError, match="pagination"):
        BitableRepository("base-token", client=client).load_snapshot()


def make_requirement_risk(index):
    return RequirementRisk(
        requirement_record_id="rec-req-{}".format(index),
        requirement_id="REQ-{}".format(index),
        requirement_name="需求 {}".format(index),
        project="米家",
        target_version="8.0",
        merge_at=datetime(2026, 8, 3, 18, 0, tzinfo=SHANGHAI),
        launch_at=datetime(2026, 8, 5, 18, 0, tzinfo=SHANGHAI),
        project_owner_id="ou-project",
        project_owner_name="项目负责人",
        level=RiskLevel.SEVERE,
        predicted_completion=datetime(2026, 7, 29, 12, 0, tzinfo=SHANGHAI),
        buffer_days=-1.5,
        reasons=["节点延期", "阻塞超期"],
    )


def make_node_risk(index):
    return NodeRisk(
        node_record_id="rec-node-{}".format(index),
        requirement_id="REQ-{}".format(index),
        node_name="开发",
        domain="客户端",
        owner_id="ou-owner",
        owner_name="负责人",
        planned_end=datetime(2026, 7, 27, 18, 0, tzinfo=SHANGHAI),
        status="进行中",
        level=RiskLevel.WARNING,
        safe_deadline=datetime(2026, 7, 28, 18, 0, tzinfo=SHANGHAI),
        reasons=["缓冲不足"],
    )


def make_notification(index):
    return {
        "fingerprint": "fingerprint-{}".format(index),
        "requirement_record_id": "rec-req-{}".format(index),
        "notification_type": "严重风险",
        "risk_level": RiskLevel.SEVERE,
        "summary": "需求存在严重风险",
        "recipient_ids": ["ou-owner", "ou-product"],
        "sent_at": datetime(2026, 7, 24, 18, 0, tzinfo=SHANGHAI),
        "send_result": "成功",
        "error": "",
        "llm_used": False,
        "llm_degradation_reason": "未配置 API Key",
    }


def test_batch_writers_chunk_at_500_and_use_schema_field_names():
    meta = repository_meta("需求主表", "进展节点表", "通知记录表")
    client = FakeCLI(meta)
    checked_at = datetime(2026, 7, 24, 18, 30, tzinfo=SHANGHAI)
    repository = BitableRepository(
        "base-token", client=client, now=lambda: checked_at
    )

    repository.write_requirement_risks(
        [make_requirement_risk(index) for index in range(501)]
    )
    repository.write_node_risks([make_node_risk(index) for index in range(501)])
    repository.append_notification_records(
        [make_notification(index) for index in range(501)]
    )

    assert [len(call[2]) for call in client.batch_updates] == [500, 1, 500, 1]
    assert [len(call[2]) for call in client.batch_creates] == [500, 1]

    requirement_record = client.batch_updates[0][2][0]
    assert requirement_record["id"] == "rec-req-0"
    assert requirement_record["fields"] == {
        "当前风险等级": "严重",
        "风险原因": "节点延期\n阻塞超期",
        "预计完成时间": 1785297600000,
        "剩余缓冲天数": -1.5,
        "最近检查时间": 1784889000000,
    }

    node_record = client.batch_updates[2][2][0]
    assert node_record["id"] == "rec-node-0"
    assert node_record["fields"] == {
        "系统风险等级": "预警",
        "系统风险原因": "缓冲不足",
        "最晚安全DDL": 1785232800000,
    }

    notification_fields = client.batch_creates[0][2][0]
    assert set(notification_fields) == {
        "通知指纹",
        "需求",
        "通知类型",
        "风险等级",
        "消息摘要",
        "通知对象",
        "发送时间",
        "发送结果",
        "错误信息",
        "是否使用 LLM",
        "LLM 降级原因",
    }
    assert notification_fields["需求"] == ["rec-req-0"]
    assert notification_fields["风险等级"] == "严重"
    assert notification_fields["通知对象"] == [
        {"id": "ou-owner"},
        {"id": "ou-product"},
    ]
    assert client.temp_files
    assert all(item["mode"] == 0o600 for item in client.temp_files)
    assert all(item["exists_during_call"] for item in client.temp_files)
    assert all(not os.path.exists(item["path"]) for item in client.temp_files)


def test_checklist_node_writer_updates_derived_plan_and_safe_deadline():
    meta = repository_meta("进展节点表")
    client = FakeCLI(meta)
    repository = BitableRepository("base-token", client=client)
    deadline = datetime(2026, 7, 29, 18, tzinfo=SHANGHAI)
    repository.write_node_risks(
        [
            NodeRisk(
                node_record_id="node-checklist",
                requirement_id="REQ-1",
                node_name="上线 Checklist",
                domain="服务端",
                owner_id="ou-node",
                owner_name="节点负责人",
                planned_end=deadline,
                status="进行中",
                safe_deadline=deadline,
                planned_end_is_system_managed=True,
            )
        ]
    )

    fields = client.batch_updates[0][2][0]["fields"]
    expected = int(deadline.timestamp() * 1000)
    assert fields["计划完成时间"] == expected
    assert fields["最晚安全DDL"] == expected


def test_batch_writers_do_not_send_empty_batches():
    client = FakeCLI(repository_meta("需求主表", "进展节点表", "通知记录表"))
    repository = BitableRepository("base-token", client=client)

    repository.write_requirement_risks([])
    repository.write_node_risks([])
    repository.append_notification_records([])

    assert client.batch_updates == []
    assert client.batch_creates == []
    assert client.record_calls == []


def test_batch_temp_file_is_deleted_when_cli_call_fails():
    class FailingCLI(FakeCLI):
        def batch_update(self, app_token, table_id, records=None, *, file_path=None):
            self._load_batch(records, file_path)
            raise RuntimeError("write failed")

    client = FailingCLI(repository_meta("需求主表"))
    repository = BitableRepository("base-token", client=client)

    with pytest.raises(RuntimeError, match="write failed"):
        repository.write_requirement_risks([make_requirement_risk(1)])

    assert client.temp_files
    assert all(not os.path.exists(item["path"]) for item in client.temp_files)
