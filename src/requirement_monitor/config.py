import json
import os
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional
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


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bitable_url: NonEmptyStr
    webhook_url: SecretStr
    fixed_rules_path: Path
    timezone: NonEmptyStr = "Asia/Shanghai"
    send_hour: int = Field(default=20, ge=0, le=23)
    send_minute: int = Field(default=0, ge=0, le=59)
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
        if isinstance(value, SecretStr):
            raw_value = value.get_secret_value()
        elif isinstance(value, str):
            raw_value = value.strip()
        else:
            return value
        if not raw_value:
            raise ValueError("webhook_url must not be empty")
        _validate_http_url(raw_value, "webhook_url")
        return raw_value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


def load_settings(path: Optional[Path] = None) -> Settings:
    config_path = _resolve_config_path(path)
    config_data = _read_config(config_path)
    _apply_environment_overrides(config_data)

    webhook_url = config_data.get("webhook_url")
    if not isinstance(webhook_url, str) or not webhook_url.strip():
        raise ConfigError(
            "Webhook URL is missing; set webhook_url in the configuration file "
            "or REQUIREMENT_MONITOR_WEBHOOK_URL."
        )

    try:
        return Settings.model_validate(config_data)
    except ValidationError as exc:
        errors = exc.errors(include_input=False)
        raise ConfigError(
            "Invalid requirement monitor configuration in {}: {}".format(
                config_path, _format_validation_errors(errors)
            )
        ) from None


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


def _apply_environment_overrides(config_data: Dict[str, Any]) -> None:
    webhook_url = os.getenv("REQUIREMENT_MONITOR_WEBHOOK_URL")
    if webhook_url:
        config_data["webhook_url"] = webhook_url

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


def _format_validation_errors(errors: List[Dict[str, Any]]) -> str:
    messages = []
    for error in errors:
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = error.get("msg", "invalid value")
        messages.append("{}: {}".format(location or "configuration", message))
    return "; ".join(messages)
