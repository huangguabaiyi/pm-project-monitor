"""macOS LaunchAgent helpers.

launchd evaluates ``StartCalendarInterval`` in the host system timezone.
The plist therefore stores intervals converted from the configured timezone
using the current system zone.  ``scheduled-run`` remains the final guard in
the configured timezone, and ``start`` should be rerun after a system,
configured-business timezone, or DST transition so the static calendar
mapping is refreshed.
"""

import os
import plistlib
import re
import subprocess
import tempfile
from datetime import (
    date,
    datetime,
    timedelta,
    time as datetime_time,
    timezone as utc_timezone,
    tzinfo,
)
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LAUNCH_AGENT_LABEL = "com.mi.requirement-monitor"
TimezoneValue = Union[str, tzinfo]
DEFAULT_PLIST_PATH = (
    Path.home()
    / "Library"
    / "LaunchAgents"
    / f"{LAUNCH_AGENT_LABEL}.plist"
)


class LaunchdError(RuntimeError):
    """Raised when launchctl cannot manage the monitor LaunchAgent."""

    def __init__(
        self,
        message: str,
        *,
        returncode: Optional[int] = None,
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


def in_scheduled_window(
    now: datetime,
    hour: int,
    minute: int,
    window_minutes: int = 5,
) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("scheduled time is invalid")
    if window_minutes <= 0:
        raise ValueError("window_minutes must be positive")
    if now.weekday() >= 5:
        return False
    scheduled_at = now.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    return scheduled_at <= now < scheduled_at + timedelta(minutes=window_minutes)


def system_timezone_provider() -> tzinfo:
    candidates = []
    environment_timezone = os.getenv("TZ", "").strip()
    if environment_timezone:
        candidates.append(environment_timezone.lstrip(":"))

    try:
        localtime = Path("/etc/localtime").resolve()
        marker = f"{os.sep}zoneinfo{os.sep}"
        resolved = str(localtime)
        if marker in resolved:
            candidates.append(resolved.split(marker, 1)[1])
    except OSError:
        pass

    for candidate in candidates:
        try:
            return ZoneInfo(candidate)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            continue

    current = datetime.now().astimezone()
    return current.tzinfo or utc_timezone.utc


current_system_timezone = system_timezone_provider


def _coerce_timezone(value: TimezoneValue) -> tzinfo:
    if isinstance(value, str):
        try:
            return ZoneInfo(value)
        except (TypeError, ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
    if value is None or not isinstance(value, tzinfo):
        raise ValueError("timezone must be a valid timezone")
    return value


def _resolve_local_datetime(value: datetime, zone: tzinfo) -> datetime:
    candidate = value.replace(tzinfo=zone)
    round_trip = candidate.astimezone(utc_timezone.utc).astimezone(zone)
    if round_trip.replace(tzinfo=None) != value.replace(tzinfo=None):
        return round_trip
    return candidate


def scheduled_intervals_in_system_timezone(
    *,
    hour: int,
    minute: int,
    configured_timezone: TimezoneValue,
    system_timezone: Optional[TimezoneValue] = None,
    reference_date: Optional[date] = None,
    now: Optional[datetime] = None,
    system_timezone_provider_fn: Optional[Callable[[], TimezoneValue]] = None,
) -> List[Dict[str, int]]:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("scheduled time is invalid")
    configured_zone = _coerce_timezone(configured_timezone)
    system_zone = _coerce_timezone(
        system_timezone
        if system_timezone is not None
        else (system_timezone_provider_fn or system_timezone_provider)()
    )
    if now is not None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        start_date = now.astimezone(configured_zone).date()
    elif reference_date is not None:
        start_date = reference_date
    else:
        start_date = datetime.now(configured_zone).date()
    intervals: List[Dict[str, int]] = []
    local_date = start_date
    while len(intervals) < 5:
        if local_date.weekday() >= 5:
            local_date += timedelta(days=1)
            continue
        local_datetime = _resolve_local_datetime(
            datetime.combine(
                local_date,
                datetime_time(hour=hour, minute=minute),
            ),
            configured_zone,
        )
        system_datetime = local_datetime.astimezone(system_zone)
        intervals.append(
            {
                "Weekday": system_datetime.isoweekday(),
                "Hour": system_datetime.hour,
                "Minute": system_datetime.minute,
            }
        )
        local_date += timedelta(days=1)
    return intervals


def render_plist(
    *,
    python_path: str,
    config_path: Path,
    hour: int,
    minute: int,
    timezone: str = "Asia/Shanghai",
    system_timezone: Optional[TimezoneValue] = None,
    reference_date: Optional[date] = None,
    now: Optional[datetime] = None,
    system_timezone_provider_fn: Optional[Callable[[], TimezoneValue]] = None,
    working_directory: Optional[Path] = None,
    label: str = LAUNCH_AGENT_LABEL,
) -> str:
    configured_zone = _coerce_timezone(timezone)
    host_zone = (
        _coerce_timezone(system_timezone)
        if system_timezone is not None
        else (system_timezone_provider_fn or system_timezone_provider)()
    )
    intervals = scheduled_intervals_in_system_timezone(
        hour=hour,
        minute=minute,
        configured_timezone=configured_zone,
        system_timezone=host_zone,
        reference_date=reference_date,
        now=now,
    )
    config = Path(config_path).expanduser().resolve()
    python = Path(python_path).expanduser()
    if not python.is_absolute():
        python = python.resolve()
    program_arguments = [
        str(python),
        "-m",
        "requirement_monitor.cli",
        "scheduled-run",
        "--config",
        str(config),
    ]
    payload: Mapping[str, Any] = {
        "Label": label,
        "ProgramArguments": program_arguments,
        "StartCalendarInterval": intervals,
        "EnvironmentVariables": {"TZ": timezone},
        "WorkingDirectory": str(
            Path(working_directory or config.parent).expanduser().resolve()
        ),
        "RunAtLoad": False,
    }
    return plistlib.dumps(dict(payload), fmt=plistlib.FMT_XML).decode("utf-8")


def write_plist(path: Path, content: str) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{target.name}.",
            dir=str(target.parent),
        )
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
        os.chmod(target, 0o600)
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
    return target


def default_plist_path() -> Path:
    return DEFAULT_PLIST_PATH


def _run_launchctl(
    arguments: Sequence[str],
    *,
    command_runner: Optional[Callable[..., Any]] = None,
) -> Any:
    runner = command_runner or subprocess.run
    command = ["launchctl", *arguments]
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            shell=False,
            check=False,
        )
    except OSError as error:
        raise LaunchdError(
            "launchctl could not be executed",
            stderr=str(error),
        ) from error
    return_code = getattr(result, "returncode", 0)
    if return_code != 0:
        raise LaunchdError(
            "launchctl command failed",
            returncode=return_code,
            stderr=str(getattr(result, "stderr", "") or ""),
        )
    return result


def bootstrap(
    plist_path: Path,
    *,
    uid: Optional[int] = None,
    command_runner: Optional[Callable[..., Any]] = None,
) -> Any:
    user_id = os.getuid() if uid is None else uid
    return _run_launchctl(
        ["bootstrap", f"gui/{user_id}", str(Path(plist_path).expanduser())],
        command_runner=command_runner,
    )


def bootout(
    *,
    uid: Optional[int] = None,
    label: str = LAUNCH_AGENT_LABEL,
    command_runner: Optional[Callable[..., Any]] = None,
    ignore_missing: bool = True,
) -> Any:
    user_id = os.getuid() if uid is None else uid
    try:
        return _run_launchctl(
            ["bootout", f"gui/{user_id}/{label}"],
            command_runner=command_runner,
        )
    except LaunchdError as error:
        if ignore_missing and _is_missing_service(error.stderr):
            return None
        raise


def enable(
    *,
    uid: Optional[int] = None,
    label: str = LAUNCH_AGENT_LABEL,
    command_runner: Optional[Callable[..., Any]] = None,
) -> Any:
    user_id = os.getuid() if uid is None else uid
    return _run_launchctl(
        ["enable", f"gui/{user_id}/{label}"],
        command_runner=command_runner,
    )


def disable(
    *,
    uid: Optional[int] = None,
    label: str = LAUNCH_AGENT_LABEL,
    command_runner: Optional[Callable[..., Any]] = None,
) -> Any:
    user_id = os.getuid() if uid is None else uid
    return _run_launchctl(
        ["disable", f"gui/{user_id}/{label}"],
        command_runner=command_runner,
    )


def _is_missing_service(stderr: str) -> bool:
    normalized = stderr.lower()
    return any(
        phrase in normalized
        for phrase in (
            "could not find service",
            "service not found",
            "no such process",
            "not found",
        )
    )


def status(
    *,
    uid: Optional[int] = None,
    label: str = LAUNCH_AGENT_LABEL,
    command_runner: Optional[Callable[..., Any]] = None,
) -> bool:
    user_id = os.getuid() if uid is None else uid
    try:
        _run_launchctl(
            ["print", f"gui/{user_id}/{label}"],
            command_runner=command_runner,
        )
    except LaunchdError as error:
        if _is_missing_service(error.stderr):
            return False
        raise
    return True


def is_disabled(
    *,
    uid: Optional[int] = None,
    label: str = LAUNCH_AGENT_LABEL,
    command_runner: Optional[Callable[..., Any]] = None,
) -> bool:
    user_id = os.getuid() if uid is None else uid
    result = _run_launchctl(
        ["print-disabled", f"gui/{user_id}"],
        command_runner=command_runner,
    )
    output = str(getattr(result, "stdout", "") or "")
    match = re.search(
        r'"?{}"?\s*=>\s*(true|false)'.format(re.escape(label)),
        output,
        flags=re.IGNORECASE,
    )
    return bool(match and match.group(1).lower() == "true")


launchctl_bootstrap = bootstrap
launchctl_bootout = bootout
launchctl_status = status
launchctl_is_disabled = is_disabled
launchctl_enable = enable
launchctl_disable = disable
