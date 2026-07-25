import os
import plistlib
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LAUNCH_AGENT_LABEL = "com.mi.requirement-monitor"
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


def render_plist(
    *,
    python_path: str,
    config_path: Path,
    hour: int,
    minute: int,
    timezone: str = "Asia/Shanghai",
    working_directory: Optional[Path] = None,
    label: str = LAUNCH_AGENT_LABEL,
) -> str:
    try:
        ZoneInfo(timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("timezone must be a valid IANA timezone") from error
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
        "StartCalendarInterval": [
            {"Weekday": weekday, "Hour": hour, "Minute": minute}
            for weekday in range(1, 6)
        ],
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
    target.write_text(content, encoding="utf-8")
    os.chmod(target, 0o600)
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
        raise LaunchdError("launchctl could not be executed") from error
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
    except LaunchdError:
        return False
    return True


launchctl_bootstrap = bootstrap
launchctl_bootout = bootout
launchctl_status = status
launchctl_enable = enable
launchctl_disable = disable
