from datetime import datetime
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from requirement_monitor.database import (
    DatabaseRepository,
    SnapshotImporter,
    initialize_database,
    session_scope,
    PersonRow,
)
from requirement_monitor.models import (
    BaseConfig,
    Blocker,
    DataSnapshot,
    DeliveryNode,
    NodeStatus,
    Person,
    ProjectConfig,
    Requirement,
)
from sqlalchemy import select


SHANGHAI = ZoneInfo("Asia/Shanghai")


def sample_snapshot() -> DataSnapshot:
    merge_at = datetime(2026, 8, 20, 18, 0, tzinfo=SHANGHAI)
    return DataSnapshot(
        requirements=[
            Requirement(
                record_id="rec-req-1",
                requirement_id="REQ-1",
                name="登录优化",
                okr_target="项目 A",
                current_stage="研发",
                project_owner_id="ou-project",
                project_owner_name="项目负责人",
                product_owner_id="ou-product",
                product_owner_name="产品负责人",
                target_version="1.0",
                merge_at=merge_at,
                briefing_completed=True,
                notification_enabled=True,
                archived=False,
            )
        ],
        nodes=[
            DeliveryNode(
                record_id="rec-node-1",
                requirement_id="REQ-1",
                domain="客户端",
                work_type="研发",
                name="客户端开发",
                owners=[
                    Person(open_id="ou-node-1", name="研发一"),
                    Person(open_id="ou-node-2", name="研发二"),
                ],
                status=NodeStatus.IN_PROGRESS,
            )
        ],
        blockers=[
            Blocker(
                record_id="rec-blocker-1",
                requirement_id="REQ-1",
                title="等待接口确认",
                owner_id="ou-blocker",
                owner_name="阻塞负责人",
                found_at=datetime(2026, 8, 18, 9, 0, tzinfo=SHANGHAI),
                planned_resolution_at=datetime(2026, 8, 19, 18, 0, tzinfo=SHANGHAI),
                status="处理中",
                affects_merge=True,
            )
        ],
        project_configs=[
            ProjectConfig(
                record_id="rec-config-1",
                project="项目 A",
                duration_mode="workday",
                at1_days=4,
            )
        ],
        base_configs=[
            BaseConfig(
                record_id="rec-base-1",
                name="客户端",
                config_type="交付域",
                sort_order=1,
                enabled=True,
            )
        ],
    )


def test_snapshot_import_is_idempotent_and_preserves_people():
    snapshot = sample_snapshot()
    with NamedTemporaryFile(suffix=".db") as database_file:
        database_url = "sqlite+pysqlite:///" + database_file.name
        initialize_database(database_url)
        importer = SnapshotImporter(database_url)

        first = importer.import_snapshot(snapshot)
        second = importer.import_snapshot(snapshot)

        assert first == second
        loaded, issues = DatabaseRepository(database_url).load_snapshot()
        assert issues == []
        assert len(loaded.requirements) == 1
        assert len(loaded.nodes) == 1
        assert len(loaded.nodes[0].owners) == 2
        assert loaded.requirements[0].project_owner_id == "ou-project"

        with session_scope(database_url) as session:
            people = list(session.scalars(select(PersonRow)))
            assert {person.feishu_open_id for person in people} == {
                "ou-project",
                "ou-product",
                "ou-node-1",
                "ou-node-2",
                "ou-blocker",
            }


def test_database_repository_persists_system_risk_fields():
    snapshot = sample_snapshot()
    with NamedTemporaryFile(suffix=".db") as database_file:
        database_url = "sqlite+pysqlite:///" + database_file.name
        initialize_database(database_url)
        SnapshotImporter(database_url).import_snapshot(snapshot)
        loaded, _ = DatabaseRepository(database_url).load_snapshot()
        assert loaded.requirements[0].requirement_id == "REQ-1"
