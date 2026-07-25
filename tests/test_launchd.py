import stat
import plistlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from requirement_monitor.launchd import (
    LAUNCH_AGENT_LABEL,
    LaunchdError,
    bootout,
    bootstrap,
    disable,
    enable,
    in_scheduled_window,
    render_plist,
    scheduled_intervals_in_system_timezone,
    status,
    write_plist,
)


TZ = ZoneInfo("Asia/Shanghai")


def test_scheduled_window_requires_a_local_weekday_and_is_five_minutes_wide():
    assert in_scheduled_window(
        datetime(2026, 7, 24, 20, 0, tzinfo=TZ), 20, 0
    ) is True
    assert in_scheduled_window(
        datetime(2026, 7, 24, 20, 4, 59, tzinfo=TZ), 20, 0
    ) is True
    assert in_scheduled_window(
        datetime(2026, 7, 24, 20, 5, tzinfo=TZ), 20, 0
    ) is False
    assert in_scheduled_window(
        datetime(2026, 7, 25, 20, 3, tzinfo=TZ), 20, 0
    ) is False


def test_scheduled_window_rejects_naive_datetime():
    try:
        in_scheduled_window(datetime(2026, 7, 24, 20, 3), 20, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("naive datetimes must be rejected")


def test_plist_runs_only_weekdays_at_configured_time(tmp_path):
    content = render_plist(
        python_path="/tmp/venv/bin/python",
        config_path=tmp_path / "config.local.json",
        hour=20,
        minute=0,
        timezone="Asia/Shanghai",
        system_timezone=TZ,
        reference_date=date(2026, 7, 20),
        working_directory=tmp_path,
    )
    payload = plistlib.loads(content.encode("utf-8"))
    assert content.count("<key>Weekday</key>") == 5
    assert "<integer>20</integer>" in content
    assert "scheduled-run" in content
    assert "--config" in content
    assert str(tmp_path / "config.local.json") in content
    assert str(tmp_path) in content
    assert payload["EnvironmentVariables"]["TZ"] == "Asia/Shanghai"


def test_schedule_conversion_handles_date_rollover_with_fixed_offsets():
    intervals = scheduled_intervals_in_system_timezone(
        hour=20,
        minute=0,
        configured_timezone=timezone(timedelta(hours=-5)),
        system_timezone=timezone(timedelta(hours=14)),
        reference_date=date(2026, 7, 20),
    )

    friday = intervals[4]
    assert friday == {"Weekday": 6, "Hour": 15, "Minute": 0}


def test_schedule_conversion_uses_injected_zoneinfo_dst_offset():
    intervals = scheduled_intervals_in_system_timezone(
        hour=20,
        minute=0,
        configured_timezone="America/New_York",
        system_timezone=ZoneInfo("UTC"),
        reference_date=date(2026, 3, 9),
    )

    assert intervals[0] == {"Weekday": 2, "Hour": 0, "Minute": 0}


def test_sunday_start_uses_next_weekday_after_dst_transition():
    intervals = scheduled_intervals_in_system_timezone(
        hour=20,
        minute=0,
        configured_timezone="America/New_York",
        system_timezone=ZoneInfo("UTC"),
        now=datetime(2026, 3, 8, 12, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    assert intervals[0] == {"Weekday": 2, "Hour": 0, "Minute": 0}


def test_system_timezone_provider_accepts_injected_tz_environment(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")

    from requirement_monitor.launchd import system_timezone_provider

    assert getattr(system_timezone_provider(), "key", None) == "America/New_York"


def test_launchctl_status_distinguishes_missing_service_from_execution_error():
    missing = lambda command, **kwargs: SimpleNamespace(
        returncode=1,
        stderr="Could not find service",
    )
    denied = lambda command, **kwargs: SimpleNamespace(
        returncode=1,
        stderr="Operation not permitted",
    )

    assert status(uid=501, command_runner=missing) is False
    try:
        status(uid=501, command_runner=denied)
    except LaunchdError as error:
        assert error.stderr == "Operation not permitted"
    else:
        raise AssertionError("launchctl execution errors must not look stopped")


def test_launchctl_oserror_preserves_detail():
    def raise_oserror(command, **kwargs):
        raise OSError("launchctl permission detail")

    try:
        bootstrap(Path("/tmp/monitor.plist"), command_runner=raise_oserror)
    except LaunchdError as error:
        assert "launchctl permission detail" in error.stderr
    else:
        raise AssertionError("launchctl OSError must be wrapped with detail")


def test_write_plist_uses_private_permissions(tmp_path):
    path = tmp_path / "LaunchAgents" / "monitor.plist"

    write_plist(path, "plist content")

    assert path.read_text(encoding="utf-8") == "plist content"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_launchctl_disable_enable_and_idempotent_bootout_use_gui_label():
    commands = []

    def fake_runner(command, **kwargs):
        commands.append(command)
        if command[1] == "bootout":
            return SimpleNamespace(
                returncode=1,
                stderr="Could not find service",
            )
        return SimpleNamespace(returncode=0, stderr="")

    bootout(uid=501, command_runner=fake_runner)
    disable(uid=501, command_runner=fake_runner)
    enable(uid=501, command_runner=fake_runner)

    assert commands == [
        ["launchctl", "bootout", f"gui/501/{LAUNCH_AGENT_LABEL}"],
        ["launchctl", "disable", f"gui/501/{LAUNCH_AGENT_LABEL}"],
        ["launchctl", "enable", f"gui/501/{LAUNCH_AGENT_LABEL}"],
    ]
