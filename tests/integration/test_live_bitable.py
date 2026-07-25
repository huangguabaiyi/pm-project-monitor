import os
from collections.abc import Mapping
from typing import Any

import pytest

from requirement_monitor.config import load_settings
from requirement_monitor.feishu_cli import FeishuCLI


pytestmark = pytest.mark.skipif(
    os.getenv("REQUIREMENT_MONITOR_LIVE_TEST") != "1",
    reason="live Feishu tests are opt-in",
)


def _metadata_data(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    data = metadata.get("data")
    return data if isinstance(data, Mapping) else metadata


def _has_table(metadata: Mapping[str, Any], data: Mapping[str, Any]) -> bool:
    if isinstance(data.get("table_id"), str) and data["table_id"]:
        return True

    tables = data.get("tables", data.get("items", []))
    if not isinstance(tables, list):
        return False
    return any(
        isinstance(table, Mapping)
        and isinstance(table.get("table_id"), str)
        and bool(table["table_id"])
        for table in tables
    )


def test_live_bitable_metadata_is_readable():
    settings = load_settings()
    metadata = FeishuCLI().meta(settings.bitable_url)
    data = _metadata_data(metadata)

    assert metadata.get("type", data.get("type")) == "bitable"
    assert isinstance(data.get("app_token"), str) and data["app_token"]
    assert _has_table(metadata, data)
