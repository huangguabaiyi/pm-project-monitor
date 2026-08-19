import json
import stat
from pathlib import Path

import pytest

from requirement_monitor.config import ConfigError, load_settings


VALID = "https://open.feishu.cn/open-apis/bot/v2/hook/test-token"


def test_config_selects_environment_webhook_and_database(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.local.json"
    path.write_text(json.dumps({"database_url": "sqlite+pysqlite:///./pulse.db", "runtime_environment": "test", "webhooks": {"test": VALID}}), encoding="utf-8")
    monkeypatch.delenv("REQUIREMENT_MONITOR_ENV", raising=False)
    settings = load_settings(path)
    assert settings.database_url.endswith("pulse.db")
    assert settings.webhook_url is not None
    assert settings.webhook_url.get_secret_value() == VALID
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_config_can_run_without_webhook_for_database_only(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    assert load_settings(path, require_webhook=False).webhook_url is None
    with pytest.raises(ConfigError, match="Webhook URL is missing"):
        load_settings(path)


def test_config_rejects_old_complex_fields(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"fixed_rules_path": "rules", "llm": {"enabled": True}}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(path, require_webhook=False)
