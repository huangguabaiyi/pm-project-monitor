import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import List, Literal, Optional, Set

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError


class StateCorruptionError(RuntimeError):
    """Raised after an unreadable state file has been quarantined."""


class RecentSend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_type: str = Field(min_length=1)
    fingerprint: Optional[str] = None
    success: bool
    sent_at: AwareDatetime
    project: Optional[str] = None
    requirement_id: Optional[str] = None
    error: Optional[str] = None


class MonitorState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    last_successful_run: Optional[AwareDatetime] = None
    last_scheduled_date: Optional[date] = None
    active_fingerprints: Set[str] = Field(default_factory=set)
    recent_sends: List[RecentSend] = Field(default_factory=list)


class StateStore:
    def __init__(self, path: Path, *, now=None) -> None:
        self.path = Path(path)
        self._now = now or datetime.now

    def load(self) -> MonitorState:
        if not self.path.exists():
            return MonitorState()
        try:
            raw_state = json.loads(self.path.read_text(encoding="utf-8"))
            state = MonitorState.model_validate(raw_state)
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as error:
            backup = self._backup_corrupt_state()
            raise StateCorruptionError(
                "State file {} is corrupt and was backed up to {}: {}".format(
                    self.path, backup, error
                )
            ) from error
        os.chmod(self.path, 0o600)
        return state

    def save(self, state: MonitorState) -> None:
        validated = MonitorState.model_validate(state)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = validated.model_dump(mode="json")
        payload["active_fingerprints"] = sorted(validated.active_fingerprints)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".{}.".format(self.path.name),
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        temporary_path = Path(temporary_name)
        try:
            os.chmod(temporary_path, 0o600)
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as output:
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
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
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
        except OSError as error:
            raise StateCorruptionError(
                "State file {} is corrupt and could not be backed up: {}".format(
                    self.path, error
                )
            ) from error
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
