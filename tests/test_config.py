import json
from pathlib import Path

import pytest

from requirement_monitor.config import ConfigError, load_settings


ENVIRONMENT_KEYS = (
    "REQUIREMENT_MONITOR_CONFIG",
    "REQUIREMENT_MONITOR_WEBHOOK_URL",
    "REQUIREMENT_MONITOR_LLM_API_KEY",
    "REQUIREMENT_MONITOR_LLM_BASE_URL",
    "REQUIREMENT_MONITOR_LLM_MODEL",
)


def write_config(path: Path, **overrides) -> None:
    config = {
        "bitable_url": "https://mi.feishu.cn/wiki/base",
        "fixed_rules_path": "固定业务规则",
        "state_dir": ".state",
        "log_dir": "logs",
        "llm": {"enabled": False},
    }
    config.update(overrides)
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")


def clear_environment(monkeypatch) -> None:
    for key in ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_environment_overrides_secret_values(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(
        config_path,
        webhook_url="https://file.example/hook",
        llm={
            "enabled": True,
            "base_url": "https://file.example/v1",
            "api_key": "file-key",
            "model": "file-model",
        },
    )
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_WEBHOOK_URL", "https://env.example/hook"
    )
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_API_KEY", "env-key")
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_LLM_BASE_URL", "https://env.example/v1"
    )
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_MODEL", "env-model")

    settings = load_settings(config_path)

    assert (
        settings.webhook_url.get_secret_value() == "https://env.example/hook"
    )
    assert settings.llm.api_key.get_secret_value() == "env-key"
    assert settings.llm.base_url == "https://env.example/v1"
    assert settings.llm.model == "env-model"
    assert settings.llm.timeout_seconds == 20
    assert settings.fixed_rules_path == Path("固定业务规则")
    assert settings.timezone == "Asia/Shanghai"
    assert settings.send_hour == 20
    assert settings.send_minute == 0


def test_config_path_comes_from_environment(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "monitor.json"
    write_config(config_path, webhook_url="https://example.invalid/hook")
    monkeypatch.setenv("REQUIREMENT_MONITOR_CONFIG", str(config_path))

    settings = load_settings()

    assert settings.bitable_url == "https://mi.feishu.cn/wiki/base"


def test_missing_webhook_has_clear_configuration_error(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path)

    with pytest.raises(ConfigError, match="Webhook"):
        load_settings(config_path)


def test_blank_webhook_has_clear_configuration_error(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url="   ")

    with pytest.raises(ConfigError, match="Webhook"):
        load_settings(config_path)
