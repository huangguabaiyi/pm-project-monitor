import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

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


def _configured_url_type(bitable_url: str) -> str:
    parts = [part for part in urlparse(bitable_url).path.split("/") if part]
    return parts[0] if parts else ""


def test_live_bitable_metadata_is_readable():
    settings = load_settings(require_webhook=False)
    metadata = FeishuCLI().meta(settings.bitable_url)
    data = _metadata_data(metadata)
    url_type = _configured_url_type(settings.bitable_url)

    assert isinstance(data.get("app_token"), str) and data["app_token"]
    if url_type == "wiki":
        assert isinstance(data.get("table_id"), str) and data["table_id"]
        assert isinstance(data.get("name"), str) and data["name"]
        assert data.get("url_type") == "wiki"
    else:
        assert url_type in {"base", "app"}
        metadata_type = data.get(
            "type", metadata.get("type", data.get("url_type"))
        )
        assert metadata_type in {"bitable", "base", "app"}
