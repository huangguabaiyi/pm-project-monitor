import json
import traceback
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
    assert settings.state_dir == Path(".state")
    assert settings.log_dir == Path("logs")
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


def test_schema_configuration_can_skip_webhook_requirement(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path)

    settings = load_settings(config_path, require_webhook=False)

    assert settings.bitable_url == "https://mi.feishu.cn/wiki/base"
    assert settings.webhook_url is None


def test_blank_webhook_has_clear_configuration_error(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url="   ")

    with pytest.raises(ConfigError, match="Webhook"):
        load_settings(config_path)


def test_validation_error_does_not_expose_llm_secret(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    secret = "secret-that-must-not-leak"
    config_path = tmp_path / "config.json"
    write_config(
        config_path,
        webhook_url="https://example.invalid/hook",
        llm={"enabled": True, "api_key": {"raw": secret}},
    )

    with pytest.raises(ConfigError) as exc_info:
        load_settings(config_path)

    rendered_exception = "".join(
        traceback.format_exception(
            exc_info.type, exc_info.value, exc_info.tb
        )
    )
    assert secret not in rendered_exception
    assert "llm.api_key" in str(exc_info.value)


def test_non_object_llm_is_not_replaced_by_environment(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(
        config_path,
        webhook_url="https://example.invalid/hook",
        llm="invalid",
    )
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_API_KEY", "environment-key")

    with pytest.raises(ConfigError, match="llm"):
        load_settings(config_path)


@pytest.mark.parametrize(
    "overrides",
    (
        {"unexpected": True},
        {"llm": {"enabled": False, "unexpected": True}},
    ),
)
def test_unknown_configuration_fields_are_rejected(tmp_path, monkeypatch, overrides):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(
        config_path,
        webhook_url="https://example.invalid/hook",
        **overrides,
    )

    with pytest.raises(ConfigError, match="unexpected"):
        load_settings(config_path)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("timezone", "Not/A_Timezone"),
        ("bitable_url", "https://example.com/wiki/base"),
        ("bitable_url", "ftp://mi.feishu.cn/wiki/base"),
        ("webhook_url", "ftp://example.invalid/hook"),
        ("send_hour", 24),
        ("send_minute", 60),
    ),
)
def test_invalid_top_level_settings_are_rejected(
    tmp_path, monkeypatch, field_name, field_value
):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    overrides = {"webhook_url": "https://example.invalid/hook"}
    overrides[field_name] = field_value
    write_config(config_path, **overrides)

    with pytest.raises(ConfigError, match=field_name):
        load_settings(config_path)


def test_invalid_llm_base_url_is_rejected(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(
        config_path,
        webhook_url="https://example.invalid/hook",
        llm={"enabled": True, "base_url": "file:///tmp/model", "model": "m"},
    )

    with pytest.raises(ConfigError, match="llm.base_url"):
        load_settings(config_path)


def test_llm_environment_override_creates_missing_llm_object(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url="https://example.invalid/hook")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.pop("llm")
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_MODEL", "environment-model")

    settings = load_settings(config_path)

    assert settings.llm.model == "environment-model"
