from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from requirement_monitor import cli
from requirement_monitor.launchd import LaunchdError


def test_version_command(capsys):
    exit_code = cli.main(["version"])
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "requirement-monitor 0.1.0"


def test_build_parser_exposes_public_commands():
    parser = cli.build_parser()

    commands = {
        parser.parse_args([command]).command
        for command in (
            "start",
            "stop",
            "restart",
            "status",
            "run-once",
            "logs",
            "scheduled-run",
            "version",
        )
    }
    assert commands == {
        "start",
        "stop",
        "restart",
        "status",
        "run-once",
        "logs",
        "scheduled-run",
        "version",
    }
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["unknown"])
    assert exc_info.value.code == 2
    assert "scheduled-run" not in parser.format_help()


@pytest.mark.parametrize(
    ("flag", "apply"), (("--dry-run", False), ("--apply", True))
)
def test_init_table_parser_requires_an_explicit_mode(flag, apply):
    args = cli.build_parser().parse_args(["init-table", flag])

    assert args.command == "init-table"
    assert args.apply is apply


def test_init_table_parser_rejects_missing_or_conflicting_modes():
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["init-table"])
    with pytest.raises(SystemExit):
        parser.parse_args(["init-table", "--dry-run", "--apply"])


def test_init_table_dry_run_prints_operations_without_applying(capsys):
    calls = []

    def fake_initializer(bitable_url, *, apply):
        calls.append((bitable_url, apply))
        return [
            type(
                "FakeOperation",
                (),
                {"kind": "rename_table", "payload": {"name": "需求主表"}},
            )(),
            type(
                "FakeOperation",
                (),
                {
                    "kind": "seed_records",
                    "payload": {
                        "table_id": "<基础配置表>",
                        "record_count": 35,
                    },
                },
            )(),
        ]

    exit_code = cli.main(
        ["init-table", "--dry-run"],
        initialize_schema_fn=fake_initializer,
        load_settings_fn=lambda path: type(
            "Settings", (), {"bitable_url": "https://example.feishu.cn/base/app"}
        )(),
    )

    assert exit_code == 0
    assert calls == [("https://example.feishu.cn/base/app", False)]
    output = capsys.readouterr().out
    assert "rename_table" in output
    assert 'seed_records {"record_count": 35' in output


def test_run_once_dry_run_renders_payload_without_sending(capsys, tmp_path):
    calls = []

    class FakeRunner:
        def run(self, *, trigger, dry_run=False):
            calls.append((trigger, dry_run))
            return SimpleNamespace(
                payloads=[{"msg_type": "text", "content": {"text": "preview"}}],
                errors=[],
                failed_sends=0,
                sent_cards=0,
            )

    settings = SimpleNamespace(
        timezone="Asia/Shanghai",
        send_hour=20,
        send_minute=0,
        state_dir=Path(".state"),
        log_dir=Path("logs"),
        fixed_rules_path=Path("固定业务规则"),
    )
    exit_code = cli.main(
        ["run-once", "--dry-run", "--config", str(tmp_path / "config.json")],
        load_settings_fn=lambda path: settings,
        runner_factory_fn=lambda loaded, config_path: FakeRunner(),
    )

    assert exit_code == 0
    assert calls == [("manual", True)]
    assert '"preview"' in capsys.readouterr().out


def test_scheduled_run_does_not_run_outside_local_window(tmp_path):
    calls = []
    settings = SimpleNamespace(
        timezone="Asia/Shanghai",
        send_hour=20,
        send_minute=0,
        state_dir=Path(".state"),
        log_dir=Path("logs"),
        fixed_rules_path=Path("固定业务规则"),
    )

    exit_code = cli.main(
        ["scheduled-run", "--config", str(tmp_path / "config.json")],
        load_settings_fn=lambda path: settings,
        runner_factory_fn=lambda loaded, config_path: calls.append("runner"),
        now_fn=lambda: datetime(2026, 7, 25, 20, 2, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert exit_code == 0
    assert calls == []


def test_scheduled_run_runs_inside_local_window(tmp_path):
    calls = []

    class FakeRunner:
        def run(self, *, trigger, dry_run=False):
            calls.append((trigger, dry_run))
            return SimpleNamespace(
                payloads=[],
                errors=[],
                failed_sends=0,
                sent_cards=1,
            )

    settings = SimpleNamespace(
        timezone="Asia/Shanghai",
        send_hour=20,
        send_minute=0,
        state_dir=Path(".state"),
        log_dir=Path("logs"),
        fixed_rules_path=Path("固定业务规则"),
    )
    exit_code = cli.main(
        ["scheduled-run", "--config", str(tmp_path / "config.json")],
        load_settings_fn=lambda path: settings,
        runner_factory_fn=lambda loaded, config_path: FakeRunner(),
        now_fn=lambda: datetime(2026, 7, 24, 20, 2, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert exit_code == 0
    assert calls == [("scheduled", False)]


def test_scheduled_run_rejects_naive_clock_without_running(tmp_path, capsys):
    calls = []
    settings = SimpleNamespace(
        timezone="Asia/Shanghai",
        send_hour=20,
        send_minute=0,
        state_dir=Path(".state"),
        log_dir=Path("logs"),
        fixed_rules_path=Path("固定业务规则"),
    )

    exit_code = cli.main(
        ["scheduled-run", "--config", str(tmp_path / "config.json")],
        load_settings_fn=lambda path: settings,
        runner_factory_fn=lambda loaded, config_path: calls.append("runner"),
        now_fn=lambda: datetime(2026, 7, 24, 20, 2),
    )

    assert exit_code == 2
    assert calls == []
    assert "clock" in capsys.readouterr().err.lower()


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (SimpleNamespace(errors=["AUTH_ERROR"], failed_sends=0, sent_cards=0), 3),
        (SimpleNamespace(errors=[], failed_sends=1, sent_cards=0), 4),
        (SimpleNamespace(errors=["STATE_ERROR"], failed_sends=0, sent_cards=0), 5),
        (SimpleNamespace(errors=[], failed_sends=0, sent_cards=1), 0),
    ],
)
def test_report_exit_codes_are_explicit(report, expected):
    assert cli._report_exit_code(report) == expected


def test_mixed_webhook_failure_returns_four_and_reports_partial(capsys, tmp_path):
    class FakeRunner:
        def run(self, *, trigger, dry_run=False):
            return SimpleNamespace(
                payloads=[],
                errors=[],
                failed_sends=1,
                sent_cards=1,
            )

    settings = SimpleNamespace(
        timezone="Asia/Shanghai",
        send_hour=20,
        send_minute=0,
        state_dir=Path(".state"),
        log_dir=Path("logs"),
        fixed_rules_path=Path("固定业务规则"),
    )

    exit_code = cli.main(
        ["run-once", "--config", str(tmp_path / "config.json")],
        load_settings_fn=lambda path: settings,
        runner_factory_fn=lambda loaded, config_path: FakeRunner(),
    )

    assert exit_code == 4
    assert "partial" in capsys.readouterr().err.lower()


def test_status_missing_service_is_stopped_but_launchctl_error_is_five(
    capsys, tmp_path
):
    settings = SimpleNamespace(
        timezone="Asia/Shanghai",
        send_hour=20,
        send_minute=0,
        state_dir=tmp_path / ".state",
        log_dir=tmp_path / "logs",
        fixed_rules_path=tmp_path / "固定业务规则",
    )

    stopped = cli.main(
        ["status", "--config", str(tmp_path / "config.json")],
        load_settings_fn=lambda path: settings,
        status_fn=lambda: False,
    )
    assert stopped == 0
    assert "loaded: no" in capsys.readouterr().out

    denied = cli.main(
        ["status", "--config", str(tmp_path / "config.json")],
        load_settings_fn=lambda path: settings,
        status_fn=lambda: (_ for _ in ()).throw(
            LaunchdError("launchctl command failed", stderr="Operation not permitted")
        ),
    )
    assert denied == 5
    assert "Operation not permitted" in capsys.readouterr().err


def test_start_writes_private_plist_and_bootstraps(tmp_path):
    calls = []
    settings = SimpleNamespace(
        timezone="Asia/Shanghai",
        send_hour=20,
        send_minute=0,
        state_dir=Path(".state"),
        log_dir=Path("logs"),
        fixed_rules_path=Path("固定业务规则"),
    )
    plist_path = tmp_path / "monitor.plist"

    exit_code = cli.main(
        ["start", "--config", str(tmp_path / "config.json")],
        load_settings_fn=lambda path: settings,
        plist_path=plist_path,
        write_plist_fn=lambda path, content: Path(path).write_text(content),
        enable_fn=lambda: calls.append("enable"),
        bootstrap_fn=lambda path: calls.append(Path(path)),
    )

    assert exit_code == 0
    assert calls == ["enable", plist_path]
    assert "scheduled-run" in plist_path.read_text()
    assert "Asia/Shanghai" in plist_path.read_text()


def test_start_write_failure_restores_plist_and_disables_agent(tmp_path):
    calls = []
    settings = SimpleNamespace(
        timezone="Asia/Shanghai",
        send_hour=20,
        send_minute=0,
        state_dir=tmp_path / ".state",
        log_dir=tmp_path / "logs",
        fixed_rules_path=tmp_path / "固定业务规则",
    )
    plist_path = tmp_path / "monitor.plist"
    plist_path.write_text("old plist", encoding="utf-8")

    def failing_write(path, content):
        Path(path).write_text("new plist", encoding="utf-8")
        raise OSError("write failed")

    exit_code = cli.main(
        ["start", "--config", str(tmp_path / "config.json")],
        load_settings_fn=lambda path: settings,
        plist_path=plist_path,
        enable_fn=lambda: calls.append("enable"),
        write_plist_fn=failing_write,
        disable_fn=lambda: calls.append("disable"),
        bootstrap_fn=lambda path: calls.append("bootstrap"),
    )

    assert exit_code == 5
    assert plist_path.read_text(encoding="utf-8") == "old plist"
    assert calls == ["enable", "disable"]


def test_start_bootstrap_retry_failure_cleans_up_and_preserves_stderr(tmp_path, capsys):
    calls = []
    settings = SimpleNamespace(
        timezone="Asia/Shanghai",
        send_hour=20,
        send_minute=0,
        state_dir=tmp_path / ".state",
        log_dir=tmp_path / "logs",
        fixed_rules_path=tmp_path / "固定业务规则",
    )
    plist_path = tmp_path / "monitor.plist"
    plist_path.write_text("old plist", encoding="utf-8")
    bootstrap_calls = []

    def failing_bootstrap(path):
        bootstrap_calls.append(path)
        if len(bootstrap_calls) == 1:
            raise LaunchdError("first bootstrap", stderr="first stderr")
        raise LaunchdError("retry bootstrap", stderr="retry stderr")

    exit_code = cli.main(
        ["start", "--config", str(tmp_path / "config.json")],
        load_settings_fn=lambda path: settings,
        plist_path=plist_path,
        enable_fn=lambda: calls.append("enable"),
        write_plist_fn=lambda path, content: Path(path).write_text(content),
        bootstrap_fn=failing_bootstrap,
        bootout_fn=lambda: calls.append("bootout"),
        disable_fn=lambda: calls.append("disable"),
    )

    assert exit_code == 5
    assert len(bootstrap_calls) == 2
    assert calls == ["enable", "bootout", "disable"]
    assert plist_path.read_text(encoding="utf-8") == "old plist"
    error_output = capsys.readouterr().err
    assert "first stderr" in error_output
    assert "retry stderr" in error_output


def test_start_failure_restores_previously_loaded_service(tmp_path):
    calls = []
    settings = SimpleNamespace(
        timezone="Asia/Shanghai",
        send_hour=20,
        send_minute=0,
        state_dir=tmp_path / ".state",
        log_dir=tmp_path / "logs",
        fixed_rules_path=tmp_path / "固定业务规则",
    )
    plist_path = tmp_path / "monitor.plist"
    plist_path.write_text("old plist", encoding="utf-8")

    def failing_bootstrap(path):
        raise LaunchdError("bootstrap failed", stderr="bootstrap stderr")

    exit_code = cli.main(
        ["start", "--config", str(tmp_path / "config.json")],
        load_settings_fn=lambda path: settings,
        plist_path=plist_path,
        status_fn=lambda: True,
        enable_fn=lambda: calls.append("enable"),
        write_plist_fn=lambda path, content: Path(path).write_text(content),
        bootstrap_fn=failing_bootstrap,
        bootout_fn=lambda: calls.append("bootout"),
        disable_fn=lambda: calls.append("disable"),
    )

    assert exit_code == 5
    assert plist_path.read_text(encoding="utf-8") == "old plist"
    assert calls == ["enable", "bootout", "enable", "disable"]


def test_stop_boots_out_then_persistently_disables_agent():
    calls = []

    exit_code = cli.main(
        ["stop"],
        bootout_fn=lambda: calls.append("bootout"),
        disable_fn=lambda: calls.append("disable"),
    )

    assert exit_code == 0
    assert calls == ["bootout", "disable"]
