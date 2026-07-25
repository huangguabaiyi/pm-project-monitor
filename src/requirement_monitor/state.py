import json
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Literal, Optional, Set, Union

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
    result: Literal["attempted", "success", "failed"]
    error_code: Optional[str] = None

    @field_validator("error_code", mode="before")
    @classmethod
    def normalize_error_code(cls, value):
        return normalize_send_error_code(value)


class _ScheduledAttemptEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    kind: Literal["scheduled_attempt"] = "scheduled_attempt"
    key: str = Field(min_length=1)
    result: ScheduledDailyResult


class _SevereConfirmationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    kind: Literal["severe_confirmation"] = "severe_confirmation"
    fingerprint: str = Field(min_length=1)
    requirement_id: str = Field(min_length=1)
    confirmed_at: AwareDatetime


_RecoveryEvent = Union[_ScheduledAttemptEvent, _SevereConfirmationEvent]


class _RecoveryJournal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    next_sequence: int = Field(default=1, ge=1)
    events: List[_RecoveryEvent] = Field(default_factory=list)


class MonitorState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    last_successful_run: Optional[AwareDatetime] = None
    last_scheduled_date: Optional[date] = None
    active_fingerprints: Set[str] = Field(default_factory=set)
    active_fingerprint_requirements: Dict[str, str] = Field(
        default_factory=dict
    )
    scheduled_daily_results: Dict[str, ScheduledDailyResult] = Field(
        default_factory=dict
    )
    recent_sends: List[RecentSend] = Field(default_factory=list)
    recovery_cursor: int = Field(default=0, ge=0)


class StateStore:
    def __init__(self, path: Path, *, now=None) -> None:
        self.path = Path(path)
        self.recovery_path = self.path.with_name(
            "{}.recovery".format(self.path.name)
        )
        self._now = now or datetime.now

    def load(self) -> MonitorState:
        state = self._load_main_state()
        journal = self._load_recovery_journal()
        scheduled_daily_results = dict(state.scheduled_daily_results)
        active_fingerprints = set(state.active_fingerprints)
        active_requirements = dict(state.active_fingerprint_requirements)
        recovery_cursor = state.recovery_cursor
        for event in journal.events:
            recovery_cursor = max(recovery_cursor, event.sequence)
            if event.sequence <= state.recovery_cursor:
                continue
            if isinstance(event, _ScheduledAttemptEvent):
                scheduled_daily_results[event.key] = event.result
            else:
                active_fingerprints.add(event.fingerprint)
                active_requirements[event.fingerprint] = event.requirement_id
        return state.model_copy(
            update={
                "scheduled_daily_results": scheduled_daily_results,
                "active_fingerprints": active_fingerprints,
                "active_fingerprint_requirements": active_requirements,
                "recovery_cursor": recovery_cursor,
            }
        )

    def _load_main_state(self) -> MonitorState:
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
        try:
            validated = MonitorState.model_validate(state)
        except ValidationError:
            raise StatePersistenceError("STATE_WRITE_FAILED") from None
        payload = validated.model_dump(mode="json")
        payload["active_fingerprints"] = sorted(
            validated.active_fingerprints
        )
        self._atomic_write(self.path, payload, "STATE_WRITE_FAILED")
        self._prune_recovery_journal(validated.recovery_cursor)

    def record_scheduled_attempt(
        self, key: str, result: ScheduledDailyResult
    ) -> int:
        journal = self._load_recovery_journal()
        sequence = journal.next_sequence
        event = _ScheduledAttemptEvent(
            sequence=sequence,
            key=key,
            result=result,
        )
        self._write_recovery_journal(
            journal.model_copy(
                update={
                    "next_sequence": sequence + 1,
                    "events": journal.events + [event],
                }
            )
        )
        return sequence

    def record_severe_confirmation(
        self,
        fingerprint: str,
        requirement_id: str,
        confirmed_at: datetime,
    ) -> int:
        journal = self._load_recovery_journal()
        sequence = journal.next_sequence
        event = _SevereConfirmationEvent(
            sequence=sequence,
            fingerprint=fingerprint,
            requirement_id=requirement_id,
            confirmed_at=confirmed_at,
        )
        self._write_recovery_journal(
            journal.model_copy(
                update={
                    "next_sequence": sequence + 1,
                    "events": journal.events + [event],
                }
            )
        )
        return sequence

    def _load_recovery_journal(self) -> _RecoveryJournal:
        if not self.recovery_path.exists():
            return _RecoveryJournal()
        try:
            contents = self.recovery_path.read_text(encoding="utf-8")
        except OSError:
            raise StatePersistenceError("STATE_RECOVERY_READ_FAILED") from None
        try:
            journal = _RecoveryJournal.model_validate_json(contents)
        except (UnicodeError, ValidationError):
            self._backup_corrupt_file(self.recovery_path)
            raise StateCorruptionError(
                "STATE_RECOVERY_CORRUPT_BACKED_UP"
            ) from None
        try:
            os.chmod(self.recovery_path, 0o600)
        except OSError:
            raise StatePersistenceError(
                "STATE_RECOVERY_PERMISSION_FAILED"
            ) from None
        return journal

    def _write_recovery_journal(self, journal: _RecoveryJournal) -> None:
        payload = journal.model_dump(mode="json")
        self._atomic_write(
            self.recovery_path,
            payload,
            "STATE_RECOVERY_WRITE_FAILED",
        )

    def _prune_recovery_journal(self, recovery_cursor: int) -> None:
        if not self.recovery_path.exists():
            return
        journal = self._load_recovery_journal()
        remaining = [
            event
            for event in journal.events
            if event.sequence > recovery_cursor
        ]
        if len(remaining) == len(journal.events):
            return
        self._write_recovery_journal(
            journal.model_copy(update={"events": remaining})
        )

    def _atomic_write(
        self,
        target_path: Path,
        payload: Dict[str, object],
        error_code: str,
    ) -> None:
        temporary_path: Optional[Path] = None
        file_descriptor: Optional[int] = None
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".{}.".format(target_path.name),
                suffix=".tmp",
                dir=str(target_path.parent),
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
            os.replace(temporary_path, target_path)
            os.chmod(target_path, 0o600)
            self._sync_parent_directory()
        except MemoryError:
            raise
        except Exception:
            raise StatePersistenceError(error_code) from None
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
        return self._backup_corrupt_file(self.path)

    def _backup_corrupt_file(self, file_path: Path) -> Path:
        timestamp = self._now().strftime("%Y%m%dT%H%M%S%z")
        base_name = "{}.corrupt-{}.bak".format(file_path.name, timestamp)
        backup = file_path.with_name(base_name)
        suffix = 1
        while backup.exists():
            backup = file_path.with_name(
                "{}.{}.bak".format(base_name[:-4], suffix)
            )
            suffix += 1
        try:
            os.replace(file_path, backup)
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
