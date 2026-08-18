import json
import stat
import traceback
from pathlib import Path

import pytest

import requirement_monitor.config as config_module
from requirement_monitor.config import ConfigError, load_settings


ENVIRONMENT_KEYS = (
    "REQUIREMENT_MONITOR_CONFIG",
    "REQUIREMENT_MONITOR_ENV",
    "REQUIREMENT_MONITOR_TEST_WEBHOOK_URL",
    "REQUIREMENT_MONITOR_PROD_WEBHOOK_URL",
    "REQUIREMENT_MONITOR_WEBHOOK_URL",
    "REQUIREMENT_MONITOR_BOT_KEYWORD",
    "REQUIREMENT_MONITOR_LLM_API_KEY",
    "REQUIREMENT_MONITOR_LLM_BASE_URL",
    "REQUIREMENT_MONITOR_LLM_MODEL",
)
VALID_WEBHOOK_URL = (
    "https://open.feishu.cn/open-apis/bot/v2/hook/test-config-token"
)
OVERRIDE_WEBHOOK_URL = (
    "https://open.larksuite.com/open-apis/bot/v2/hook/test-env-token"
)
PROD_WEBHOOK_URL = (
    "https://open.feishu.cn/open-apis/bot/v2/hook/prod-env-token"
)
LOCAL_PROD_WEBHOOK_URL = (
    "https://open.larksuite.com/open-apis/bot/v2/hook/prod-config-token"
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


@pytest.fixture(autouse=True)
def isolate_runtime_environment(monkeypatch):
    clear_environment(monkeypatch)


def test_snapshot_loading_ignores_all_runtime_environment_overrides(
    tmp_path, monkeypatch
):
    clear_environment(monkeypatch)
    snapshot_webhook = (
        "https://open.feishu.cn/open-apis/bot/v2/hook/snapshot-token"
    )
    config_path = tmp_path / "runtime-config.json"
    write_config(
        config_path,
        runtime_environment="test",
        webhook_url=snapshot_webhook,
        bot_keyword="snapshot-keyword",
        llm={
            "enabled": True,
            "api_key": "snapshot-api-key",
            "base_url": "https://snapshot-llm.example/v1",
            "model": "snapshot-model",
        },
    )
    monkeypatch.setenv("REQUIREMENT_MONITOR_ENV", "prod")
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_TEST_WEBHOOK_URL", OVERRIDE_WEBHOOK_URL
    )
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_PROD_WEBHOOK_URL", PROD_WEBHOOK_URL
    )
    monkeypatch.setenv("REQUIREMENT_MONITOR_WEBHOOK_URL", VALID_WEBHOOK_URL)
    monkeypatch.setenv("REQUIREMENT_MONITOR_BOT_KEYWORD", "ambient-keyword")
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_API_KEY", "ambient-api-key")
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_LLM_BASE_URL", "https://ambient-llm.example/v1"
    )
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_MODEL", "ambient-model")

    settings = load_settings(
        config_path,
        use_environment_overrides=False,
    )

    assert settings.runtime_environment == "test"
    assert settings.webhook_url.get_secret_value() == snapshot_webhook
    assert settings.bot_keyword == "snapshot-keyword"
    assert settings.llm.api_key.get_secret_value() == "snapshot-api-key"
    assert settings.llm.base_url == "https://snapshot-llm.example/v1"
    assert settings.llm.model == "snapshot-model"


@pytest.mark.parametrize(
    (
        "command_environment",
        "environment_override",
        "file_environment",
        "expected_environment",
        "expected_webhook",
    ),
    (
        (None, None, "prod", "prod", LOCAL_PROD_WEBHOOK_URL),
        (None, "test", "prod", "test", VALID_WEBHOOK_URL),
        ("test", "prod", "prod", "test", VALID_WEBHOOK_URL),
        (None, None, None, "test", VALID_WEBHOOK_URL),
    ),
)
def test_runtime_environment_precedence_uses_command_env_file_then_test(
    tmp_path,
    monkeypatch,
    command_environment,
    environment_override,
    file_environment,
    expected_environment,
    expected_webhook,
):
    config_path = tmp_path / "config.json"
    overrides = {
        "webhooks": {
            "test": VALID_WEBHOOK_URL,
            "prod": LOCAL_PROD_WEBHOOK_URL,
        }
    }
    if file_environment is not None:
        overrides["runtime_environment"] = file_environment
    write_config(config_path, **overrides)
    if environment_override is not None:
        monkeypatch.setenv("REQUIREMENT_MONITOR_ENV", environment_override)

    settings = load_settings(
        config_path,
        runtime_environment=command_environment,
    )

    assert settings.runtime_environment == expected_environment
    assert settings.webhook_url.get_secret_value() == expected_webhook


def test_selected_webhook_environment_override_wins_local_webhook(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.json"
    write_config(
        config_path,
        runtime_environment="prod",
        webhooks={
            "test": VALID_WEBHOOK_URL,
            "prod": LOCAL_PROD_WEBHOOK_URL,
        },
    )
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_PROD_WEBHOOK_URL", PROD_WEBHOOK_URL
    )

    settings = load_settings(config_path)

    assert settings.runtime_environment == "prod"
    assert settings.webhook_url.get_secret_value() == PROD_WEBHOOK_URL


def test_legacy_test_environment_override_still_wins_local_webhook(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.json"
    write_config(
        config_path,
        runtime_environment="test",
        webhooks={"test": VALID_WEBHOOK_URL},
    )
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_WEBHOOK_URL", OVERRIDE_WEBHOOK_URL
    )

    settings = load_settings(config_path)

    assert settings.webhook_url.get_secret_value() == OVERRIDE_WEBHOOK_URL


def test_unified_local_config_loads_bot_keyword_and_llm_api_key(tmp_path):
    config_path = tmp_path / "config.json"
    write_config(
        config_path,
        runtime_environment="test",
        webhooks={"test": VALID_WEBHOOK_URL, "prod": LOCAL_PROD_WEBHOOK_URL},
        bot_keyword="本地机器人关键词",
        llm={
            "enabled": True,
            "api_key": "local-api-key",
            "base_url": "https://local-llm.example/v1",
            "model": "local-model",
        },
    )

    settings = load_settings(config_path)

    assert settings.bot_keyword == "本地机器人关键词"
    assert settings.llm.api_key.get_secret_value() == "local-api-key"
    assert settings.llm.base_url == "https://local-llm.example/v1"
    assert settings.llm.model == "local-model"


def test_loading_config_local_tightens_permissions_to_owner_only(tmp_path):
    config_path = tmp_path / "config.local.json"
    write_config(
        config_path,
        runtime_environment="test",
        webhooks={"test": VALID_WEBHOOK_URL},
    )
    config_path.chmod(0o644)

    load_settings(config_path)

    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_config_permission_failure_includes_exact_repair_command(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.local.json"
    write_config(
        config_path,
        runtime_environment="test",
        webhooks={"test": VALID_WEBHOOK_URL},
    )

    def deny_chmod(path, mode):
        raise OSError("permission denied")

    monkeypatch.setattr(config_module.os, "chmod", deny_chmod)

    with pytest.raises(
        ConfigError,
        match=r"chmod 600 .*config\.local\.json",
    ):
        load_settings(config_path)


def test_default_runtime_environment_uses_test_webhook(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url=VALID_WEBHOOK_URL)
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_TEST_WEBHOOK_URL", OVERRIDE_WEBHOOK_URL
    )
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_PROD_WEBHOOK_URL", PROD_WEBHOOK_URL
    )

    settings = load_settings(config_path)

    assert settings.runtime_environment == "test"
    assert settings.webhook_url.get_secret_value() == OVERRIDE_WEBHOOK_URL


def test_environment_runtime_environment_overrides_default(
    tmp_path, monkeypatch
):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url=VALID_WEBHOOK_URL)
    monkeypatch.setenv("REQUIREMENT_MONITOR_ENV", "prod")
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_PROD_WEBHOOK_URL", PROD_WEBHOOK_URL
    )

    settings = load_settings(config_path)

    assert settings.runtime_environment == "prod"
    assert settings.webhook_url.get_secret_value() == PROD_WEBHOOK_URL


@pytest.mark.parametrize(
    ("runtime_environment", "expected_webhook_url"),
    (("test", OVERRIDE_WEBHOOK_URL), ("prod", PROD_WEBHOOK_URL)),
)
def test_explicit_runtime_environment_selects_matching_webhook(
    tmp_path,
    monkeypatch,
    runtime_environment,
    expected_webhook_url,
):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url=VALID_WEBHOOK_URL)
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_TEST_WEBHOOK_URL", OVERRIDE_WEBHOOK_URL
    )
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_PROD_WEBHOOK_URL", PROD_WEBHOOK_URL
    )

    settings = load_settings(
        config_path, runtime_environment=runtime_environment
    )

    assert settings.runtime_environment == runtime_environment
    assert settings.webhook_url.get_secret_value() == expected_webhook_url


def test_command_runtime_environment_overrides_environment_variable(
    tmp_path, monkeypatch
):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url=VALID_WEBHOOK_URL)
    monkeypatch.setenv("REQUIREMENT_MONITOR_ENV", "prod")
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_TEST_WEBHOOK_URL", OVERRIDE_WEBHOOK_URL
    )
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_PROD_WEBHOOK_URL", PROD_WEBHOOK_URL
    )

    settings = load_settings(config_path, runtime_environment="test")

    assert settings.runtime_environment == "test"
    assert settings.webhook_url.get_secret_value() == OVERRIDE_WEBHOOK_URL


@pytest.mark.parametrize("runtime_environment", ("staging", "", "PROD"))
def test_invalid_runtime_environment_is_rejected(
    tmp_path, monkeypatch, runtime_environment
):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url=VALID_WEBHOOK_URL)

    with pytest.raises(ConfigError, match="runtime environment.*test.*prod"):
        load_settings(
            config_path,
            runtime_environment=runtime_environment,
            require_webhook=False,
        )


def test_invalid_environment_variable_is_rejected_without_webhook(
    tmp_path, monkeypatch
):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path)
    monkeypatch.setenv("REQUIREMENT_MONITOR_ENV", "staging")

    with pytest.raises(ConfigError, match="runtime environment.*test.*prod"):
        load_settings(config_path, require_webhook=False)


def test_prod_requires_prod_webhook_even_when_legacy_values_exist(
    tmp_path, monkeypatch
):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url=VALID_WEBHOOK_URL)
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_WEBHOOK_URL", OVERRIDE_WEBHOOK_URL
    )

    with pytest.raises(
        ConfigError, match="REQUIREMENT_MONITOR_PROD_WEBHOOK_URL"
    ):
        load_settings(config_path, runtime_environment="prod")


def test_test_webhook_falls_back_to_legacy_environment_variable(
    tmp_path, monkeypatch
):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url=VALID_WEBHOOK_URL)
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_WEBHOOK_URL", OVERRIDE_WEBHOOK_URL
    )

    settings = load_settings(config_path, runtime_environment="test")

    assert settings.webhook_url.get_secret_value() == OVERRIDE_WEBHOOK_URL


def test_test_webhook_falls_back_to_config_value(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url=VALID_WEBHOOK_URL)

    settings = load_settings(config_path, runtime_environment="test")

    assert settings.webhook_url.get_secret_value() == VALID_WEBHOOK_URL


def test_selected_webhook_validation_does_not_expose_secret(
    tmp_path, monkeypatch
):
    clear_environment(monkeypatch)
    secret = "prod-secret-that-must-not-leak"
    invalid_webhook_url = "https://example.com/open-apis/bot/v2/hook/{}".format(
        secret
    )
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url=VALID_WEBHOOK_URL)
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_PROD_WEBHOOK_URL", invalid_webhook_url
    )

    with pytest.raises(ConfigError, match="official Feishu/Lark") as exc_info:
        load_settings(config_path, runtime_environment="prod")

    rendered_exception = "".join(
        traceback.format_exception(
            exc_info.type, exc_info.value, exc_info.tb
        )
    )
    assert secret not in rendered_exception


def test_environment_overrides_secret_values(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(
        config_path,
        webhook_url=VALID_WEBHOOK_URL,
        llm={
            "enabled": True,
            "base_url": "https://file.example/v1",
            "api_key": "file-key",
            "model": "file-model",
        },
    )
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_WEBHOOK_URL", OVERRIDE_WEBHOOK_URL
    )
    monkeypatch.setenv("REQUIREMENT_MONITOR_BOT_KEYWORD", "需求提醒")
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_API_KEY", "env-key")
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_LLM_BASE_URL", "https://env.example/v1"
    )
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_MODEL", "env-model")

    settings = load_settings(config_path)

    assert (
        settings.webhook_url.get_secret_value() == OVERRIDE_WEBHOOK_URL
    )
    assert settings.bot_keyword == "需求提醒"
    assert settings.llm.api_key.get_secret_value() == "env-key"
    assert settings.llm.base_url == "https://env.example/v1"
    assert settings.llm.model == "env-model"
    assert settings.llm.timeout_seconds == 20
    assert settings.fixed_rules_path == Path("固定业务规则")
    assert settings.state_dir == Path(".state")
    assert settings.log_dir == Path("logs")
    assert settings.timezone == "Asia/Shanghai"
    assert settings.send_hour == 19
    assert settings.send_minute == 30


def test_config_path_comes_from_environment(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "monitor.json"
    write_config(config_path, webhook_url=VALID_WEBHOOK_URL)
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


def test_database_data_source_accepts_database_only_configuration(
    tmp_path, monkeypatch
):
    clear_environment(monkeypatch)
    config_path = tmp_path / "database-config.json"
    write_config(
        config_path,
        data_source="database",
        bitable_url=None,
        database_url="sqlite+pysqlite:///./database.db",
    )

    settings = load_settings(config_path, require_webhook=False)

    assert settings.data_source == "database"
    assert settings.bitable_url is None
    assert settings.database_url == "sqlite+pysqlite:///./database.db"


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
        webhook_url=VALID_WEBHOOK_URL,
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
        webhook_url=VALID_WEBHOOK_URL,
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
        webhook_url=VALID_WEBHOOK_URL,
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
        ("webhook_url", "https://example.com/open-apis/bot/v2/hook/token"),
        ("webhook_url", "http://localhost:8080/hook/token"),
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
    overrides = {"webhook_url": VALID_WEBHOOK_URL}
    overrides[field_name] = field_value
    write_config(config_path, **overrides)

    with pytest.raises(ConfigError, match=field_name):
        load_settings(config_path)


def test_invalid_llm_base_url_is_rejected(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(
        config_path,
        webhook_url=VALID_WEBHOOK_URL,
        llm={"enabled": True, "base_url": "file:///tmp/model", "model": "m"},
    )

    with pytest.raises(ConfigError, match="llm.base_url"):
        load_settings(config_path)


def test_llm_environment_override_creates_missing_llm_object(tmp_path, monkeypatch):
    clear_environment(monkeypatch)
    config_path = tmp_path / "config.json"
    write_config(config_path, webhook_url=VALID_WEBHOOK_URL)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.pop("llm")
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_MODEL", "environment-model")

    settings = load_settings(config_path)

    assert settings.llm.model == "environment-model"
