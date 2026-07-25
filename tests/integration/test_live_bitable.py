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


def test_live_bitable_metadata_is_readable():
    settings = load_settings(require_webhook=False)
    metadata = FeishuCLI().meta(settings.bitable_url)
    data = _metadata_data(metadata)

    assert isinstance(data.get("app_token"), str) and data["app_token"]
    assert isinstance(data.get("table_id"), str) and data["table_id"]
    assert isinstance(data.get("name"), str) and data["name"]
    assert data.get("url_type") == "wiki"
