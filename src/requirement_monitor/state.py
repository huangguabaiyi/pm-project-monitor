import json
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Literal, Optional, Set

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)


class StateCorruptionError(RuntimeError):
    """Raised after an unreadable state file has been quarantined."""


class StatePersistenceError(RuntimeError):
    """Raised with a fixed code when state I/O cannot complete safely."""


_SAFE_SEND_ERROR_CODES = {
    "WEBHOOK_ERROR",
    "SEND_ERROR",
    "request_error",
    "timeout",
    "network_error",
    "card_format_rejected",
    "invalid_response",
    "invalid_payload",
    "invalid_interactive_payload",
    "invalid_text_payload",
    "payload_too_large",
    "service_error",
}


def normalize_send_error_code(
    value: Optional[str], fallback: str = "SEND_ERROR"
) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized in _SAFE_SEND_ERROR_CODES:
        return normalized
    if re.fullmatch(r"(?:http|feishu_error)_-?\d{1,9}", normalized):
        return normalized
    return fallback


class RecentSend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_type: str = Field(min_length=1)
    fingerprint: Optional[str] = None
    success: bool
    sent_at: AwareDatetime
    project: Optional[str] = None
    requirement_id: Optional[str] = None
    error: Optional[str] = None

    @field_validator("error", mode="before")
    @classmethod
    def normalize_error(cls, value):
        return normalize_send_error_code(value)


class ScheduledDailyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduled_date: date
    project: str = Field(min_length=1)
    attempted_at: AwareDatetime
    result: Literal["success", "failed"]
    error_code: Optional[str] = None

    @field_validator("error_code", mode="before")
    @classmethod
    def normalize_error_code(cls, value):
        return normalize_send_error_code(value)


class MonitorState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    last_successful_run: Optional[AwareDatetime] = None
    last_scheduled_date: Optional[date] = None
    active_fingerprints: Set[str] = Field(default_factory=set)
    scheduled_daily_results: Dict[str, ScheduledDailyResult] = Field(
        default_factory=dict
    )
    recent_sends: List[RecentSend] = Field(default_factory=list)


class StateStore:
    def __init__(self, path: Path, *, now=None) -> None:
        self.path = Path(path)
        self._now = now or datetime.now

    def load(self) -> MonitorState:
        if not self.path.exists():
            return MonitorState()
        try:
            contents = self.path.read_text(encoding="utf-8")
        except OSError:
            raise StatePersistenceError("STATE_READ_FAILED") from None
        try:
            raw_state = json.loads(contents)
            state = MonitorState.model_validate(raw_state)
        except (UnicodeError, json.JSONDecodeError, ValidationError):
            self._backup_corrupt_state()
            raise StateCorruptionError("STATE_CORRUPT_BACKED_UP") from None
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            raise StatePersistenceError("STATE_PERMISSION_FAILED") from None
        return state

    def save(self, state: MonitorState) -> None:
        temporary_path: Optional[Path] = None
        file_descriptor: Optional[int] = None
        try:
            validated = MonitorState.model_validate(state)
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            payload = validated.model_dump(mode="json")
            payload["active_fingerprints"] = sorted(
                validated.active_fingerprints
            )
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".{}.".format(self.path.name),
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            temporary_path = Path(temporary_name)
            os.chmod(temporary_path, 0o600)
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as output:
                file_descriptor = None
                json.dump(
                    payload,
                    output,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, self.path)
            os.chmod(self.path, 0o600)
            self._sync_parent_directory()
        except MemoryError:
            raise
        except Exception:
            raise StatePersistenceError("STATE_WRITE_FAILED") from None
        finally:
            if file_descriptor is not None:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    def _backup_corrupt_state(self) -> Path:
        timestamp = self._now().strftime("%Y%m%dT%H%M%S%z")
        base_name = "{}.corrupt-{}.bak".format(self.path.name, timestamp)
        backup = self.path.with_name(base_name)
        suffix = 1
        while backup.exists():
            backup = self.path.with_name("{}.{}.bak".format(base_name[:-4], suffix))
            suffix += 1
        try:
            os.replace(self.path, backup)
            os.chmod(backup, 0o600)
        except OSError:
            raise StateCorruptionError(
                "STATE_CORRUPT_BACKUP_FAILED"
            ) from None
        return backup

    def _sync_parent_directory(self) -> None:
        try:
            directory_descriptor = os.open(str(self.path.parent), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_descriptor)
        except OSError:
            pass
        finally:
            os.close(directory_descriptor)
