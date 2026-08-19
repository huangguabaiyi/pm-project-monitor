from pathlib import Path

from sqlalchemy import inspect

from requirement_monitor.database import create_database_engine, initialize_database
from requirement_monitor.service import get_requirement, seed_demo


def test_schema_contains_new_domain_graph_and_requirement_tables_only(tmp_path: Path):
    url = f"sqlite+pysqlite:///{tmp_path / 'schema.db'}"
    initialize_database(url)
    tables = set(inspect(create_database_engine(url)).get_table_names())
    assert {"people", "delivery_domains", "node_definitions", "node_definition_domains", "workflow_templates", "workflow_template_nodes", "workflow_template_edges", "requirements", "requirement_nodes", "requirement_edges", "webhook_settings", "ai_settings"} <= tables
    assert "projects" not in tables
    assert "blockers" not in tables
    requirement_columns = {column["name"] for column in inspect(create_database_engine(url)).get_columns("requirements")}
    assert {"sequence_id", "meego_url", "requirement_url", "figma_url", "ai_analysis", "ai_analyzed_at", "ai_input_hash", "ai_error"} <= requirement_columns


def test_demo_seed_creates_parallel_workflow_and_snapshot(tmp_path: Path):
    url = f"sqlite+pysqlite:///{tmp_path / 'demo.db'}"
    initialize_database(url)
    counts = seed_demo(url)
    assert counts["requirements"] == 1
    from requirement_monitor.service import list_requirements
    requirement = list_requirements(url)[0]
    detail = get_requirement(url, requirement["id"])
    assert detail is not None
    assert len(detail["nodes"]) == 5
    assert len(detail["edges"]) == 5
    review = next(node for node in detail["nodes"] if node["name"] == "需求评审")
    assert sum(edge["source"] == review["id"] for edge in detail["edges"]) == 2


def test_existing_people_table_gets_domain_column_without_data_loss(tmp_path: Path):
    from sqlalchemy import create_engine, text
    path = tmp_path / "old.db"
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE people (id VARCHAR(32) PRIMARY KEY, display_name VARCHAR(255), feishu_open_id VARCHAR(128), email VARCHAR(320), role_name VARCHAR(255), description TEXT, active BOOLEAN, created_at DATETIME, updated_at DATETIME)"))
        connection.execute(text("INSERT INTO people (id, display_name, active) VALUES ('old-person', '旧成员', 1)"))
    url = f"sqlite+pysqlite:///{path}"
    initialize_database(url)
    columns = {column["name"] for column in inspect(create_database_engine(url)).get_columns("people")}
    assert "domain_id" in columns
    with create_database_engine(url).connect() as connection:
        assert connection.execute(text("SELECT display_name FROM people WHERE id='old-person'" )).scalar_one() == "旧成员"
