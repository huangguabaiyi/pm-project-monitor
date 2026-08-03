import json
import os
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    ValidationError,
    field_validator,
)

from .webhook_url import is_allowed_webhook_url


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ConfigError(ValueError):
    """Raised when monitor configuration cannot be loaded or validated."""


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base_url: Optional[NonEmptyStr] = None
    api_key: Optional[SecretStr] = None
    model: Optional[NonEmptyStr] = None
    timeout_seconds: int = Field(default=20, gt=0)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            _validate_http_url(value, "base_url")
        return value

    @field_validator("api_key", mode="before")
    @classmethod
    def strip_api_key(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("api_key must not be empty")
            return stripped
        return value


class WebhookSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test: Optional[SecretStr] = None
    prod: Optional[SecretStr] = None

    @field_validator("test", "prod", mode="before")
    @classmethod
    def validate_webhook_url(cls, value):
        return _validated_webhook_url(value)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bitable_url: NonEmptyStr
    runtime_environment: Literal["test", "prod"] = "test"
    webhook_url: Optional[SecretStr] = None
    webhooks: WebhookSettings = Field(default_factory=WebhookSettings)
    bot_keyword: Optional[NonEmptyStr] = None
    fixed_rules_path: Path
    timezone: NonEmptyStr = "Asia/Shanghai"
    send_hour: int = Field(default=19, ge=0, le=23)
    send_minute: int = Field(default=30, ge=0, le=59)
    state_dir: Path
    log_dir: Path
    llm: LLMSettings = Field(default_factory=LLMSettings)

    @field_validator("bitable_url")
    @classmethod
    def validate_bitable_url(cls, value: str) -> str:
        parsed = _validate_http_url(value, "bitable_url")
        hostname = parsed.hostname or ""
        if hostname != "feishu.cn" and not hostname.endswith(".feishu.cn"):
            raise ValueError("bitable_url must be a Feishu URL")
        return value

    @field_validator("webhook_url", mode="before")
    @classmethod
    def validate_webhook_url(cls, value):
        return _validated_webhook_url(value)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


def load_settings(
    path: Optional[Path] = None,
    *,
    require_webhook: bool = True,
    runtime_environment: Optional[str] = None,
    use_environment_overrides: bool = True,
) -> Settings:
    config_path = _resolve_config_path(path)
    _tighten_local_config_permissions(config_path)
    config_data = _read_config(config_path)
    if use_environment_overrides:
        resolved_environment = _resolve_runtime_environment(
            runtime_environment,
            config_data.get("runtime_environment"),
        )
        configured_webhooks = config_data.get("webhooks")
        legacy_webhook_url = config_data.pop("webhook_url", None)
        resolved_webhook_url = _resolve_webhook_url(
            resolved_environment,
            configured_webhooks,
            legacy_webhook_url,
        )

        config_data["runtime_environment"] = resolved_environment
        if resolved_webhook_url is not None:
            config_data["webhook_url"] = resolved_webhook_url
        _apply_environment_overrides(config_data)
    else:
        resolved_environment = _validate_runtime_environment_value(
            config_data.get("runtime_environment", "test")
        )
        config_data["runtime_environment"] = resolved_environment

    try:
        settings = Settings.model_validate(config_data)
    except ValidationError as exc:
        errors = exc.errors(include_input=False)
        raise ConfigError(
            "Invalid requirement monitor configuration in {}: {}".format(
                config_path, _format_validation_errors(errors)
            )
        ) from None

    if require_webhook and settings.webhook_url is None:
        if resolved_environment == "prod":
            raise ConfigError(
                "Webhook URL is missing for prod runtime environment; set "
                "webhooks.prod or REQUIREMENT_MONITOR_PROD_WEBHOOK_URL."
            )
        raise ConfigError(
            "Webhook URL is missing for test runtime environment; set "
            "webhooks.test, REQUIREMENT_MONITOR_TEST_WEBHOOK_URL, "
            "REQUIREMENT_MONITOR_WEBHOOK_URL, or legacy webhook_url."
        )
    return settings


def _resolve_config_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path)

    environment_path = os.getenv("REQUIREMENT_MONITOR_CONFIG")
    if environment_path:
        return Path(environment_path)
    return Path("config.local.json")


def _read_config(path: Path) -> Dict[str, Any]:
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(
            "Unable to read configuration file {}: {}".format(path, exc)
        ) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "Configuration file {} is not valid JSON: {}".format(path, exc)
        ) from exc

    if not isinstance(raw_data, dict):
        raise ConfigError(
            "Configuration file {} must contain a JSON object.".format(path)
        )
    return raw_data


def _tighten_local_config_permissions(path: Path) -> None:
    if path.name != "config.local.json" or not path.exists():
        return
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise ConfigError(
            "Unable to secure local configuration file {}: {}; run "
            "chmod 600 {} and retry.".format(path, exc, path)
        ) from exc


def _resolve_runtime_environment(
    command_override: Optional[str], configured_environment: Any = None
) -> str:
    if command_override is not None:
        resolved_environment = command_override
    else:
        environment_override = os.getenv("REQUIREMENT_MONITOR_ENV")
        if environment_override is not None:
            resolved_environment = environment_override
        elif configured_environment is not None:
            resolved_environment = configured_environment
        else:
            resolved_environment = "test"

    return _validate_runtime_environment_value(resolved_environment)


def _validate_runtime_environment_value(value: Any) -> str:
    if value not in ("test", "prod"):
        raise ConfigError(
            "Invalid runtime environment; expected one of: test, prod."
        )
    return value


def _resolve_webhook_url(
    runtime_environment: str,
    configured_webhooks: Any,
    legacy_webhook_url: Any,
) -> Any:
    configured_webhook_url = None
    if isinstance(configured_webhooks, dict):
        configured_webhook_url = configured_webhooks.get(runtime_environment)

    if runtime_environment == "prod":
        return (
            os.getenv("REQUIREMENT_MONITOR_PROD_WEBHOOK_URL")
            or configured_webhook_url
            or None
        )

    return (
        os.getenv("REQUIREMENT_MONITOR_TEST_WEBHOOK_URL")
        or os.getenv("REQUIREMENT_MONITOR_WEBHOOK_URL")
        or configured_webhook_url
        or legacy_webhook_url
    )


def _apply_environment_overrides(config_data: Dict[str, Any]) -> None:
    bot_keyword = os.getenv("REQUIREMENT_MONITOR_BOT_KEYWORD")
    if bot_keyword:
        config_data["bot_keyword"] = bot_keyword

    llm_overrides = {
        "api_key": os.getenv("REQUIREMENT_MONITOR_LLM_API_KEY"),
        "base_url": os.getenv("REQUIREMENT_MONITOR_LLM_BASE_URL"),
        "model": os.getenv("REQUIREMENT_MONITOR_LLM_MODEL"),
    }
    active_overrides = {
        field_name: value
        for field_name, value in llm_overrides.items()
        if value
    }
    if not active_overrides:
        return

    llm_data = config_data.get("llm")
    if llm_data is None:
        llm_data = {}
        config_data["llm"] = llm_data
    if isinstance(llm_data, dict):
        llm_data.update(active_overrides)


def _validate_http_url(value: str, field_name: str):
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("{} must be an HTTP(S) URL".format(field_name))
    return parsed


def _validated_webhook_url(value):
    if value is None:
        return None
    if isinstance(value, SecretStr):
        raw_value = value.get_secret_value()
    elif isinstance(value, str):
        raw_value = value.strip()
    else:
        return value
    if not raw_value:
        raise ValueError("Webhook URL must not be empty")
    if not is_allowed_webhook_url(raw_value):
        raise ValueError(
            "Webhook URL must use an official Feishu/Lark endpoint"
        )
    return raw_value


def _format_validation_errors(errors: List[Dict[str, Any]]) -> str:
    messages = []
    for error in errors:
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = error.get("msg", "invalid value")
        messages.append("{}: {}".format(location or "configuration", message))
    return "; ".join(messages)
