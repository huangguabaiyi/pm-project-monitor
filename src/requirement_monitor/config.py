import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, SecretStr, ValidationError


class ConfigError(ValueError):
    """Raised when monitor configuration cannot be loaded or validated."""


class LLMSettings(BaseModel):
    enabled: bool = False
    base_url: Optional[str] = None
    api_key: Optional[SecretStr] = None
    model: Optional[str] = None
    timeout_seconds: int = Field(default=20, gt=0)


class Settings(BaseModel):
    bitable_url: str
    webhook_url: SecretStr
    fixed_rules_path: Path
    timezone: str = "Asia/Shanghai"
    send_hour: int = Field(default=20, ge=0, le=23)
    send_minute: int = Field(default=0, ge=0, le=59)
    state_dir: Path
    log_dir: Path
    llm: LLMSettings = Field(default_factory=LLMSettings)


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
        raise ConfigError(
            "Invalid requirement monitor configuration in {}: {}".format(
                config_path, exc
            )
        ) from exc


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
        raise ConfigError("Unable to read configuration file {}: {}".format(path, exc)) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError("Configuration file {} is not valid JSON: {}".format(path, exc)) from exc

    if not isinstance(raw_data, dict):
        raise ConfigError("Configuration file {} must contain a JSON object.".format(path))
    return raw_data


def _apply_environment_overrides(config_data: Dict[str, Any]) -> None:
    webhook_url = os.getenv("REQUIREMENT_MONITOR_WEBHOOK_URL")
    if webhook_url:
        config_data["webhook_url"] = webhook_url

    llm_data = config_data.get("llm")
    if not isinstance(llm_data, dict):
        llm_data = {}
        config_data["llm"] = llm_data

    llm_overrides = {
        "api_key": os.getenv("REQUIREMENT_MONITOR_LLM_API_KEY"),
        "base_url": os.getenv("REQUIREMENT_MONITOR_LLM_BASE_URL"),
        "model": os.getenv("REQUIREMENT_MONITOR_LLM_MODEL"),
    }
    for field_name, value in llm_overrides.items():
        if value:
            llm_data[field_name] = value
