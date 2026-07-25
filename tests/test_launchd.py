import stat
import plistlib
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from requirement_monitor.launchd import (
    LAUNCH_AGENT_LABEL,
    bootout,
    disable,
    enable,
    in_scheduled_window,
    render_plist,
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
