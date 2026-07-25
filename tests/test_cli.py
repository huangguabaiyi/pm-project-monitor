import json
import plistlib
import stat
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from requirement_monitor import cli
from requirement_monitor.launchd import LaunchdError


WEBHOOK_URL = (
    "https://open.feishu.cn/open-apis/bot/v2/hook/runtime-webhook-secret"
)


def write_operational_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "bitable_url": "https://mi.feishu.cn/wiki/base",
                "fixed_rules_path": "固定业务规则",
                "timezone": "Asia/Shanghai",
                "send_hour": 20,
                "send_minute": 0,
                "state_dir": ".state",
                "log_dir": "logs",
                "llm": {
                    "enabled": True,
                    "base_url": "https://llm.example/v1",
                    "model": "test-model",
                },
            }
        ),
        encoding="utf-8",
    )


def start_settings(
    *,
    state_dir: Path = Path(".state"),
    log_dir: Path = Path("logs"),
    fixed_rules_path: Path = Path("固定业务规则"),
):
    return SimpleNamespace(
        bitable_url="https://mi.feishu.cn/wiki/base",
        webhook_url=WEBHOOK_URL,
        bot_keyword=None,
        timezone="Asia/Shanghai",
        send_hour=20,
        send_minute=0,
        state_dir=state_dir,
        log_dir=log_dir,
        fixed_rules_path=fixed_rules_path,
        llm=SimpleNamespace(
            enabled=False,
            base_url=None,
            api_key=None,
            model=None,
            timeout_seconds=20,
        ),
    )


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


@pytest.mark.parametrize(
    ("flag", "expected_apply"),
    (("--dry-run", False), ("--apply", True)),
)
def test_init_table_accepts_secret_free_config_example(
    monkeypatch, flag, expected_apply
):
    monkeypatch.delenv("REQUIREMENT_MONITOR_WEBHOOK_URL", raising=False)
    config_path = Path(__file__).parents[1] / "config.example.json"
    calls = []

    def fake_initializer(bitable_url, *, apply):
        calls.append((bitable_url, apply))
        return []

    exit_code = cli.main(
        ["init-table", flag, "--config", str(config_path)],
        initialize_schema_fn=fake_initializer,
    )

    assert exit_code == 0
    expected_url = (
        "https://mi.feishu.cn/wiki/TA6nwzFi0i4fdOkIamzcxj34nRd?"
        "fromScene=spaceOverview&table=tblQlOtlW0xmcKBE&view=vewCeVIyDY"
    )
    assert calls == [(expected_url, expected_apply)]


@pytest.mark.parametrize(
    "command_args",
    (
        ["start"],
        ["status"],
        ["scheduled-run"],
        ["run-once", "--dry-run"],
    ),
)
def test_operational_commands_still_require_webhook(
    tmp_path, monkeypatch, command_args
):
    monkeypatch.delenv("REQUIREMENT_MONITOR_WEBHOOK_URL", raising=False)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "bitable_url": "https://mi.feishu.cn/wiki/base",
                "fixed_rules_path": "固定业务规则",
                "state_dir": ".state",
                "log_dir": "logs",
            }
        ),
        encoding="utf-8",
    )

    exit_code = cli.main(
        [*command_args, "--config", str(config_path)]
    )

    assert exit_code == cli.EXIT_CONFIG


def test_invalid_webhook_url_is_configuration_exit_two(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("REQUIREMENT_MONITOR_WEBHOOK_URL", raising=False)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "bitable_url": "https://mi.feishu.cn/wiki/base",
                "webhook_url": (
                    "https://example.com/open-apis/bot/v2/hook/token"
                ),
                "fixed_rules_path": "固定业务规则",
                "state_dir": ".state",
                "log_dir": "logs",
            }
        ),
        encoding="utf-8",
    )

    exit_code = cli.main(
        ["run-once", "--dry-run", "--config", str(config_path)]
    )

    assert exit_code == cli.EXIT_CONFIG


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
    settings = start_settings()
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


def test_start_snapshots_environment_secrets_for_launchagent_scheduled_run(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.json"
    write_operational_config(config_path)
    llm_api_key = "runtime-llm-secret"
    monkeypatch.setenv("REQUIREMENT_MONITOR_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_API_KEY", llm_api_key)
    monkeypatch.setenv("REQUIREMENT_MONITOR_BOT_KEYWORD", "需求机器人")
    plist_path = tmp_path / "monitor.plist"

    exit_code = cli.main(
        ["start", "--config", str(config_path)],
        plist_path=plist_path,
        status_fn=lambda: False,
        enable_fn=lambda: None,
        bootstrap_fn=lambda path: None,
        system_timezone_fn=lambda: ZoneInfo("Asia/Shanghai"),
    )

    runtime_path = tmp_path / ".state" / "runtime-config.json"
    runtime_data = json.loads(runtime_path.read_text(encoding="utf-8"))
    plist = plistlib.loads(plist_path.read_bytes())
    assert exit_code == 0
    assert stat.S_IMODE(runtime_path.stat().st_mode) == 0o600
    assert runtime_data["webhook_url"] == WEBHOOK_URL
    assert runtime_data["bot_keyword"] == "需求机器人"
    assert runtime_data["llm"]["api_key"] == llm_api_key
    assert runtime_data["fixed_rules_path"] == str(
        (tmp_path / "固定业务规则").resolve()
    )
    assert runtime_data["state_dir"] == str((tmp_path / ".state").resolve())
    assert runtime_data["log_dir"] == str((tmp_path / "logs").resolve())
    assert plist["ProgramArguments"][-1] == str(runtime_path)
    assert b"runtime-webhook-secret" not in plist_path.read_bytes()
    assert llm_api_key.encode("utf-8") not in plist_path.read_bytes()

    monkeypatch.delenv("REQUIREMENT_MONITOR_WEBHOOK_URL")
    monkeypatch.delenv("REQUIREMENT_MONITOR_LLM_API_KEY")
    monkeypatch.delenv("REQUIREMENT_MONITOR_BOT_KEYWORD")
    loaded = {}

    class FakeRunner:
        webhook = None

        def run(self, *, trigger, dry_run=False):
            return SimpleNamespace(
                finished_at="2026-07-27T20:02:00+08:00",
                trigger=trigger,
                payloads=[],
                errors=[],
                failed_sends=0,
                sent_cards=1,
                llm_degraded=False,
                llm_failure_reasons=[],
            )

    def runner_factory(settings, loaded_config_path):
        loaded["webhook_url"] = settings.webhook_url.get_secret_value()
        loaded["bot_keyword"] = settings.bot_keyword
        loaded["llm_api_key"] = settings.llm.api_key.get_secret_value()
        loaded["config_path"] = loaded_config_path
        return FakeRunner()

    scheduled_exit = cli.main(
        ["scheduled-run", "--config", str(runtime_path)],
        runner_factory_fn=runner_factory,
        now_fn=lambda: datetime(
            2026, 7, 27, 20, 2, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
    )

    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    assert scheduled_exit == 0
    assert loaded == {
        "webhook_url": WEBHOOK_URL,
        "bot_keyword": "需求机器人",
        "llm_api_key": llm_api_key,
        "config_path": runtime_path,
    }
    assert "runtime-webhook-secret" not in rendered
    assert llm_api_key not in rendered


def test_runtime_config_commands_do_not_echo_secrets(tmp_path, capsys):
    runtime_path = tmp_path / "runtime-config.json"
    runtime_path.write_text(
        json.dumps(
            {
                "bitable_url": "https://mi.feishu.cn/wiki/base",
                "webhook_url": WEBHOOK_URL,
                "bot_keyword": "需求机器人",
                "fixed_rules_path": str(tmp_path / "固定业务规则"),
                "timezone": "Asia/Shanghai",
                "send_hour": 20,
                "send_minute": 0,
                "state_dir": str(tmp_path / ".state"),
                "log_dir": str(tmp_path / "logs"),
                "llm": {
                    "enabled": True,
                    "base_url": "https://llm.example/v1",
                    "api_key": "runtime-llm-secret",
                    "model": "test-model",
                    "timeout_seconds": 20,
                },
            }
        ),
        encoding="utf-8",
    )

    class FakeRunner:
        webhook = None

        def run(self, *, trigger, dry_run=False):
            return SimpleNamespace(
                finished_at="2026-07-25T12:00:00+08:00",
                trigger=trigger,
                payloads=[{"msg_type": "text", "content": {"text": "preview"}}],
                errors=[],
                failed_sends=0,
                sent_cards=0,
                llm_degraded=False,
                llm_failure_reasons=[],
            )

    assert (
        cli.main(
            ["status", "--config", str(runtime_path)],
            status_fn=lambda: False,
        )
        == 0
    )
    assert cli.main(["logs", "--config", str(runtime_path)]) == 0
    assert (
        cli.main(
            ["run-once", "--dry-run", "--config", str(runtime_path)],
            runner_factory_fn=lambda settings, path: FakeRunner(),
        )
        == 0
    )

    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    assert "runtime-webhook-secret" not in rendered
    assert "runtime-llm-secret" not in rendered


def test_restart_rewrites_runtime_snapshot_with_current_environment(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.json"
    write_operational_config(config_path)
    plist_path = tmp_path / "monitor.plist"
    monkeypatch.setenv("REQUIREMENT_MONITOR_WEBHOOK_URL", WEBHOOK_URL)

    first_exit = cli.main(
        ["start", "--config", str(config_path)],
        plist_path=plist_path,
        status_fn=lambda: False,
        enable_fn=lambda: None,
        bootstrap_fn=lambda path: None,
        system_timezone_fn=lambda: ZoneInfo("Asia/Shanghai"),
    )

    replacement_url = (
        "https://open.feishu.cn/open-apis/bot/v2/hook/replacement-secret"
    )
    monkeypatch.setenv("REQUIREMENT_MONITOR_WEBHOOK_URL", replacement_url)
    restart_calls = []
    restart_exit = cli.main(
        ["restart", "--config", str(config_path)],
        plist_path=plist_path,
        status_fn=lambda: False,
        disabled_status_fn=lambda: True,
        bootout_fn=lambda: restart_calls.append("bootout"),
        disable_fn=lambda: restart_calls.append("disable"),
        enable_fn=lambda: restart_calls.append("enable"),
        bootstrap_fn=lambda path: restart_calls.append("bootstrap"),
        system_timezone_fn=lambda: ZoneInfo("Asia/Shanghai"),
    )

    runtime_path = tmp_path / ".state" / "runtime-config.json"
    runtime_data = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert first_exit == 0
    assert restart_exit == 0
    assert runtime_data["webhook_url"] == replacement_url
    assert restart_calls == ["bootout", "disable", "enable", "bootstrap"]


def test_restart_failure_restores_loaded_service_and_old_files(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.json"
    write_operational_config(config_path)
    monkeypatch.setenv("REQUIREMENT_MONITOR_WEBHOOK_URL", WEBHOOK_URL)
    state = {"loaded": True, "disabled": False}
    plist_path = tmp_path / "monitor.plist"
    old_plist = "old plist"
    plist_path.write_text(old_plist, encoding="utf-8")
    runtime_path = tmp_path / ".state" / "runtime-config.json"
    runtime_path.parent.mkdir()
    old_runtime = b'{"old":"runtime"}'
    runtime_path.write_bytes(old_runtime)
    calls = []

    def bootout_agent():
        calls.append("bootout")
        state["loaded"] = False

    def loaded_status():
        calls.append("loaded-status")
        return state["loaded"]

    def disabled_status():
        calls.append("disabled-status")
        return state["disabled"]

    def disable_agent():
        calls.append("disable")
        state["disabled"] = True

    def enable_agent():
        calls.append("enable")
        state["disabled"] = False

    def bootstrap_agent(path):
        content = Path(path).read_text(encoding="utf-8")
        calls.append(("bootstrap", content))
        if content != old_plist:
            state["loaded"] = True
            raise LaunchdError("new bootstrap failed", stderr="new failure")
        state["loaded"] = True

    exit_code = cli.main(
        ["restart", "--config", str(config_path)],
        plist_path=plist_path,
        status_fn=loaded_status,
        disabled_status_fn=disabled_status,
        bootout_fn=bootout_agent,
        disable_fn=disable_agent,
        enable_fn=enable_agent,
        bootstrap_fn=bootstrap_agent,
        system_timezone_fn=lambda: ZoneInfo("Asia/Shanghai"),
    )

    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_UNEXPECTED
    assert plist_path.read_text(encoding="utf-8") == old_plist
    assert runtime_path.read_bytes() == old_runtime
    assert state == {"loaded": True, "disabled": False}
    assert calls[:3] == ["loaded-status", "disabled-status", "bootout"]
    assert calls[-3:] == ["bootout", "enable", ("bootstrap", old_plist)]
    assert "runtime-webhook-secret" not in captured.out + captured.err


def test_restart_failure_keeps_previously_stopped_disabled_service_stopped(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.json"
    write_operational_config(config_path)
    monkeypatch.setenv("REQUIREMENT_MONITOR_WEBHOOK_URL", WEBHOOK_URL)
    state = {"loaded": False, "disabled": True}
    plist_path = tmp_path / "monitor.plist"
    old_plist = "old stopped plist"
    plist_path.write_text(old_plist, encoding="utf-8")
    runtime_path = tmp_path / ".state" / "runtime-config.json"
    runtime_path.parent.mkdir()
    old_runtime = b'{"old":"stopped-runtime"}'
    runtime_path.write_bytes(old_runtime)
    bootstrapped_contents = []

    def bootstrap_agent(path):
        content = Path(path).read_text(encoding="utf-8")
        bootstrapped_contents.append(content)
        if content == old_plist:
            state["loaded"] = True
            return
        state["loaded"] = True
        raise LaunchdError("new bootstrap failed", stderr="new failure")

    exit_code = cli.main(
        ["restart", "--config", str(config_path)],
        plist_path=plist_path,
        status_fn=lambda: state["loaded"],
        disabled_status_fn=lambda: state["disabled"],
        bootout_fn=lambda: state.update(loaded=False),
        disable_fn=lambda: state.update(disabled=True),
        enable_fn=lambda: state.update(disabled=False),
        bootstrap_fn=bootstrap_agent,
        system_timezone_fn=lambda: ZoneInfo("Asia/Shanghai"),
    )

    assert exit_code == cli.EXIT_UNEXPECTED
    assert plist_path.read_text(encoding="utf-8") == old_plist
    assert runtime_path.read_bytes() == old_runtime
    assert state == {"loaded": False, "disabled": True}
    assert old_plist not in bootstrapped_contents


def test_restart_reports_original_and_restore_failures_without_secrets(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.json"
    write_operational_config(config_path)
    monkeypatch.setenv("REQUIREMENT_MONITOR_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_API_KEY", "runtime-llm-secret")
    plist_path = tmp_path / "monitor.plist"
    old_plist = "old plist"
    plist_path.write_text(old_plist, encoding="utf-8")
    runtime_path = tmp_path / ".state" / "runtime-config.json"
    runtime_path.parent.mkdir()
    runtime_path.write_text('{"old":"runtime"}', encoding="utf-8")

    def bootstrap_agent(path):
        content = Path(path).read_text(encoding="utf-8")
        if content == old_plist:
            raise LaunchdError(
                "rollback bootstrap failed",
                stderr="rollback failure runtime-llm-secret",
            )
        raise LaunchdError(
            "new bootstrap failed",
            stderr="new failure runtime-webhook-secret",
        )

    exit_code = cli.main(
        ["restart", "--config", str(config_path)],
        plist_path=plist_path,
        status_fn=lambda: True,
        disabled_status_fn=lambda: False,
        bootout_fn=lambda: None,
        disable_fn=lambda: None,
        enable_fn=lambda: None,
        bootstrap_fn=bootstrap_agent,
        system_timezone_fn=lambda: ZoneInfo("Asia/Shanghai"),
    )

    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    assert exit_code == cli.EXIT_UNEXPECTED
    assert "restart error" in rendered
    assert "rollback error" in rendered
    assert "new failure" in rendered
    assert "rollback failure" in rendered
    assert "runtime-webhook-secret" not in rendered
    assert "runtime-llm-secret" not in rendered


def test_stop_retains_runtime_snapshot(tmp_path):
    runtime_path = tmp_path / ".state" / "runtime-config.json"
    runtime_path.parent.mkdir()
    runtime_path.write_text("private runtime config", encoding="utf-8")

    exit_code = cli.main(
        ["stop"],
        bootout_fn=lambda: None,
        disable_fn=lambda: None,
    )

    assert exit_code == 0
    assert runtime_path.read_text(encoding="utf-8") == "private runtime config"


def test_start_failure_removes_new_runtime_snapshot_without_leaking_secrets(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.json"
    write_operational_config(config_path)
    monkeypatch.setenv("REQUIREMENT_MONITOR_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_API_KEY", "new-llm-secret")
    plist_path = tmp_path / "monitor.plist"

    exit_code = cli.main(
        ["start", "--config", str(config_path)],
        plist_path=plist_path,
        status_fn=lambda: False,
        enable_fn=lambda: None,
        write_plist_fn=lambda path, content: (_ for _ in ()).throw(
            OSError("plist write failed")
        ),
        bootstrap_fn=lambda path: None,
        disable_fn=lambda: None,
        system_timezone_fn=lambda: ZoneInfo("Asia/Shanghai"),
    )

    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    assert exit_code == cli.EXIT_UNEXPECTED
    assert not (tmp_path / ".state" / "runtime-config.json").exists()
    assert "runtime-webhook-secret" not in rendered
    assert "new-llm-secret" not in rendered


def test_start_failure_restores_previous_runtime_snapshot(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.json"
    write_operational_config(config_path)
    monkeypatch.setenv("REQUIREMENT_MONITOR_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.setenv("REQUIREMENT_MONITOR_LLM_API_KEY", "new-llm-secret")
    runtime_path = tmp_path / ".state" / "runtime-config.json"
    runtime_path.parent.mkdir()
    previous_content = b'{"webhook_url":"old-secret"}'
    runtime_path.write_bytes(previous_content)
    runtime_path.chmod(0o600)
    plist_path = tmp_path / "monitor.plist"
    plist_path.write_text("old plist", encoding="utf-8")

    def failing_bootstrap(path):
        raise LaunchdError("bootstrap failed", stderr="safe failure")

    exit_code = cli.main(
        ["start", "--config", str(config_path)],
        plist_path=plist_path,
        status_fn=lambda: False,
        enable_fn=lambda: None,
        bootstrap_fn=failing_bootstrap,
        bootout_fn=lambda: None,
        disable_fn=lambda: None,
        system_timezone_fn=lambda: ZoneInfo("Asia/Shanghai"),
    )

    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    assert exit_code == cli.EXIT_UNEXPECTED
    assert runtime_path.read_bytes() == previous_content
    assert stat.S_IMODE(runtime_path.stat().st_mode) == 0o600
    assert "runtime-webhook-secret" not in rendered
    assert "new-llm-secret" not in rendered


def test_start_write_failure_restores_plist_and_disables_agent(tmp_path):
    calls = []
    settings = start_settings(
        state_dir=tmp_path / ".state",
        log_dir=tmp_path / "logs",
        fixed_rules_path=tmp_path / "固定业务规则",
    )
    plist_path = tmp_path / "monitor.plist"
    plist_path.write_text("old plist", encoding="utf-8")

    write_calls = []

    def failing_write(path, content):
        write_calls.append("failed")
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


def test_start_uses_default_status_query_for_loaded_rollback(
    tmp_path, monkeypatch
):
    calls = []
    settings = start_settings(
        state_dir=tmp_path / ".state",
        log_dir=tmp_path / "logs",
        fixed_rules_path=tmp_path / "固定业务规则",
    )
    plist_path = tmp_path / "monitor.plist"
    plist_path.write_text("old plist", encoding="utf-8")
    monkeypatch.setattr(cli, "launchd_status", lambda: True)

    def failing_bootstrap(path):
        raise LaunchdError("bootstrap failed", stderr="bootstrap stderr")

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
    assert calls == ["enable", "bootout", "enable", "disable"]


def test_start_rollback_reuses_atomic_private_plist_writer(tmp_path, monkeypatch):
    settings = start_settings(
        state_dir=tmp_path / ".state",
        log_dir=tmp_path / "logs",
        fixed_rules_path=tmp_path / "固定业务规则",
    )
    plist_path = tmp_path / "monitor.plist"
    old_content = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<plist><string>a &amp; b</string></plist>"
    )
    plist_path.write_text(old_content, encoding="utf-8")
    calls = []
    real_write_plist = cli.write_plist

    def fail_once(path, content):
        calls.append(content)
        if len(calls) == 1:
            Path(path).write_text("broken", encoding="utf-8")
            raise OSError("permission denied")
        return real_write_plist(path, content)

    monkeypatch.setattr(cli, "write_plist", fail_once)

    exit_code = cli.main(
        ["start", "--config", str(tmp_path / "config.json")],
        load_settings_fn=lambda path: settings,
        plist_path=plist_path,
        status_fn=lambda: False,
        enable_fn=lambda: None,
        bootstrap_fn=lambda path: (_ for _ in ()).throw(
            LaunchdError("bootstrap failed", stderr="bootstrap stderr")
        ),
        bootout_fn=lambda: None,
        disable_fn=lambda: None,
    )

    assert exit_code == 5
    assert calls[-1] == old_content
    assert plist_path.read_text(encoding="utf-8") == old_content
    assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600


def test_start_bootstrap_retry_failure_cleans_up_and_preserves_stderr(tmp_path, capsys):
    calls = []
    settings = start_settings(
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
    settings = start_settings(
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
