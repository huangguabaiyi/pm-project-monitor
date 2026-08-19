from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, ValidationError, field_validator

from .webhook_url import is_allowed_webhook_url


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ConfigError(ValueError):
    pass


class WebhookSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    test: Optional[SecretStr] = None
    prod: Optional[SecretStr] = None

    @field_validator("test", "prod", mode="before")
    @classmethod
    def validate_url(cls, value):
        if value in (None, ""):
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value).strip()
        if not is_allowed_webhook_url(raw):
            raise ValueError("Webhook URL must use an official Feishu/Lark endpoint")
        return raw


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database_url: NonEmptyStr = "sqlite+pysqlite:///./.state/pulse.db"
    runtime_environment: Literal["test", "prod"] = "test"
    webhooks: WebhookSettings = Field(default_factory=WebhookSettings)
    webhook_url: Optional[SecretStr] = None
    bot_keyword: Optional[NonEmptyStr] = None
    timezone: NonEmptyStr = "Asia/Shanghai"
    state_dir: Path = Path(".state")
    log_dir: Path = Path("logs")

    @field_validator("webhook_url", mode="before")
    @classmethod
    def validate_selected_webhook(cls, value):
        if value in (None, ""):
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value).strip()
        if not is_allowed_webhook_url(raw):
            raise ValueError("Webhook URL must use an official Feishu/Lark endpoint")
        return raw


def load_settings(path: Optional[Path] = None, *, require_webhook: bool = True) -> Settings:
    config_path = Path(path or os.getenv("REQUIREMENT_MONITOR_CONFIG", "config.local.json"))
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"Unable to read configuration: {error}") from error
    environment = os.getenv("REQUIREMENT_MONITOR_ENV", data.get("runtime_environment", "test"))
    webhooks = data.get("webhooks") or {}
    webhook = (
        os.getenv("REQUIREMENT_MONITOR_PROD_WEBHOOK_URL") if environment == "prod"
        else os.getenv("REQUIREMENT_MONITOR_TEST_WEBHOOK_URL") or os.getenv("REQUIREMENT_MONITOR_WEBHOOK_URL")
    ) or webhooks.get(environment)
    data["runtime_environment"] = environment
    data["webhook_url"] = webhook
    if os.getenv("REQUIREMENT_MONITOR_DATABASE_URL"):
        data["database_url"] = os.environ["REQUIREMENT_MONITOR_DATABASE_URL"]
    if os.getenv("REQUIREMENT_MONITOR_BOT_KEYWORD"):
        data["bot_keyword"] = os.environ["REQUIREMENT_MONITOR_BOT_KEYWORD"]
    try:
        settings = Settings.model_validate(data)
    except ValidationError as error:
        raise ConfigError(str(error)) from None
    if require_webhook and settings.webhook_url is None:
        raise ConfigError(f"Webhook URL is missing for {environment}")
    if config_path.name == "config.local.json" and config_path.exists():
        os.chmod(config_path, 0o600)
    return settings
