import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from zoneinfo import ZoneInfo

from requirement_monitor import __version__
from requirement_monitor.config import ConfigError
from requirement_monitor.launchd import (
    bootstrap,
    bootout,
    default_plist_path,
    in_scheduled_window,
    render_plist,
    status as launchd_status,
    write_plist,
)


EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_FEISHU = 3
EXIT_WEBHOOK = 4
EXIT_UNEXPECTED = 5


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="requirement-monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("start", "stop", "restart", "status", "logs"):
        command_parser = subparsers.add_parser(command)
        _add_config_argument(command_parser)

    run_once_parser = subparsers.add_parser("run-once")
    _add_config_argument(run_once_parser)
    run_once_parser.add_argument("--dry-run", action="store_true")

    scheduled_parser = subparsers.add_parser("scheduled-run")
    _add_config_argument(scheduled_parser)

    subparsers.add_parser("version")

    init_table_parser = subparsers.add_parser("init-table")
    _add_config_argument(init_table_parser)
    mode = init_table_parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_false", dest="apply")
    mode.add_argument("--apply", action="store_true", dest="apply")
    return parser


def _resolve_config_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    environment_path = os.getenv("REQUIREMENT_MONITOR_CONFIG")
    if environment_path:
        return Path(environment_path).expanduser().resolve()
    local_path = Path("config.local.json")
    if local_path.exists():
        return local_path.resolve()
    return Path("config.example.json").resolve()


def _load_settings(
    config_path: Path,
    load_settings_fn: Optional[Callable[[Optional[Path]], Any]],
) -> Any:
    if load_settings_fn is None:
        from requirement_monitor.config import load_settings

        load_settings_fn = load_settings
    return load_settings_fn(config_path)


def _absolute_setting_path(value: Path, config_path: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def _settings_paths(settings: Any, config_path: Path):
    state_dir = _absolute_setting_path(settings.state_dir, config_path)
    log_dir = _absolute_setting_path(settings.log_dir, config_path)
    fixed_rules_path = _absolute_setting_path(
        settings.fixed_rules_path, config_path
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    return state_dir, log_dir, fixed_rules_path


def _secret_value(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return getter() if callable(getter) else str(value)


def _build_runner(settings: Any, config_path: Path):
    from requirement_monitor.feishu_cli import FeishuCLI
    from requirement_monitor.llm import LLMClient
    from requirement_monitor.repository import BitableRepository
    from requirement_monitor.runner import MonitorRunner
    from requirement_monitor.state import StateStore
    from requirement_monitor.webhook import WebhookSender

    state_dir, _, fixed_rules_path = _settings_paths(settings, config_path)
    feishu = FeishuCLI()
    return MonitorRunner(
        feishu=feishu,
        repository=BitableRepository(settings.bitable_url, client=feishu),
        webhook=WebhookSender(_secret_value(settings.webhook_url)),
        state_store=StateStore(state_dir / "monitor.json"),
        fixed_rules_path=fixed_rules_path,
        llm=LLMClient(settings.llm),
        timezone_name=settings.timezone,
    )


def _runner_from(
    settings: Any,
    config_path: Path,
    runner_factory_fn: Optional[Callable[..., Any]],
):
    factory = runner_factory_fn or _build_runner
    return factory(settings, config_path)


def _log_path(settings: Any, config_path: Path) -> Path:
    _, log_dir, _ = _settings_paths(settings, config_path)
    return log_dir / "requirement-monitor.log"


def _write_run_log(settings: Any, config_path: Path, report: Any) -> None:
    payload = {
        "finished_at": str(getattr(report, "finished_at", None)),
        "trigger": getattr(report, "trigger", ""),
        "errors": list(getattr(report, "errors", [])),
        "sent_cards": getattr(report, "sent_cards", 0),
        "failed_sends": getattr(report, "failed_sends", 0),
        "llm_degraded": getattr(report, "llm_degraded", False),
        "llm_failure_reasons": list(
            getattr(report, "llm_failure_reasons", [])
        ),
    }
    path = _log_path(settings, config_path)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(payload, ensure_ascii=False) + "\n")
    os.chmod(path, 0o600)


def _report_exit_code(report: Any) -> int:
    errors = set(getattr(report, "errors", []))
    if errors & {
        "AUTH_ERROR",
        "SNAPSHOT_ERROR",
        "SYSTEM_WRITE_ERROR",
    }:
        return EXIT_FEISHU
    failed_sends = int(getattr(report, "failed_sends", 0))
    sent_cards = int(getattr(report, "sent_cards", 0))
    if failed_sends > 0 and sent_cards == 0:
        return EXIT_WEBHOOK
    return EXIT_UNEXPECTED if errors else EXIT_OK


def _run_monitor(
    settings: Any,
    config_path: Path,
    *,
    trigger: str,
    dry_run: bool,
    runner_factory_fn: Optional[Callable[..., Any]],
) -> int:
    runner = _runner_from(settings, config_path, runner_factory_fn)
    try:
        report = runner.run(trigger=trigger, dry_run=dry_run)
        if dry_run:
            for payload in getattr(report, "payloads", []):
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        _write_run_log(settings, config_path, report)
        return _report_exit_code(report)
    finally:
        close = getattr(getattr(runner, "webhook", None), "close", None)
        if callable(close):
            close()


def _tail(path: Path, count: int = 100) -> Sequence[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()[-count:]
    except (OSError, UnicodeError):
        return ()


def _status(
    settings: Any,
    config_path: Path,
    *,
    status_fn: Optional[Callable[[], bool]],
) -> int:
    loaded = (status_fn or launchd_status)()
    print(f"launchd loaded: {'yes' if loaded else 'no'}")
    print(
        "schedule: weekdays at {:02d}:{:02d} {}".format(
            settings.send_hour, settings.send_minute, settings.timezone
        )
    )
    try:
        state_dir, _, _ = _settings_paths(settings, config_path)
        from requirement_monitor.state import StateStore

        state = StateStore(state_dir / "monitor.json").load()
        print(f"latest run: {state.last_successful_run or 'none'}")
        if state.recent_sends:
            latest_send = state.recent_sends[-1]
            print(
                "latest send: {} {}".format(
                    latest_send.sent_at,
                    "success" if latest_send.success else "failed",
                )
            )
        else:
            print("latest send: none")
    except Exception:
        print("latest run: unavailable")
        print("latest send: unavailable")

    log_lines = _tail(_log_path(settings, config_path), 1)
    if log_lines:
        try:
            latest_log = json.loads(log_lines[0])
            degradation = latest_log.get("llm_failure_reasons") or (
                "degraded" if latest_log.get("llm_degraded") else "none"
            )
        except (TypeError, ValueError):
            degradation = "unavailable"
    else:
        degradation = "none"
    print(f"latest LLM degradation: {degradation}")
    return EXIT_OK


def _start(
    settings: Any,
    config_path: Path,
    *,
    plist_path: Optional[Path],
    write_plist_fn: Optional[Callable[[Path, str], Any]],
    bootstrap_fn: Optional[Callable[[Path], Any]],
) -> int:
    target = Path(plist_path or default_plist_path()).expanduser()
    content = render_plist(
        python_path=sys.executable,
        config_path=config_path,
        hour=settings.send_hour,
        minute=settings.send_minute,
        working_directory=config_path.parent,
    )
    (write_plist_fn or write_plist)(target, content)
    (bootstrap_fn or bootstrap)(target)
    print(f"started: {target}")
    return EXIT_OK


def _stop(
    *,
    bootout_fn: Optional[Callable[[], Any]],
) -> int:
    (bootout_fn or bootout)()
    print("stopped")
    return EXIT_OK


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    initialize_schema_fn: Optional[Callable[..., Sequence[Any]]] = None,
    load_settings_fn: Optional[Callable[[Optional[Path]], Any]] = None,
    runner_factory_fn: Optional[Callable[..., Any]] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
    plist_path: Optional[Path] = None,
    write_plist_fn: Optional[Callable[[Path, str], Any]] = None,
    bootstrap_fn: Optional[Callable[[Path], Any]] = None,
    bootout_fn: Optional[Callable[[], Any]] = None,
    status_fn: Optional[Callable[[], bool]] = None,
) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "version":
        print(f"requirement-monitor {__version__}")
        return EXIT_OK

    try:
        if args.command == "stop":
            return _stop(bootout_fn=bootout_fn)

        config_path = _resolve_config_path(args.config)
        settings = _load_settings(config_path, load_settings_fn)

        if args.command == "init-table":
            if initialize_schema_fn is None:
                from requirement_monitor.schema import initialize_schema

                initialize_schema_fn = initialize_schema
            operations = initialize_schema_fn(
                settings.bitable_url, apply=args.apply
            )
            if not operations:
                print("Schema is up to date.")
                return EXIT_OK
            for operation in operations:
                print(
                    "{} {}".format(
                        operation.kind,
                        json.dumps(
                            operation.payload,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                )
            return EXIT_OK

        if args.command == "start":
            return _start(
                settings,
                config_path,
                plist_path=plist_path,
                write_plist_fn=write_plist_fn,
                bootstrap_fn=bootstrap_fn,
            )
        if args.command == "restart":
            _stop(bootout_fn=bootout_fn)
            return _start(
                settings,
                config_path,
                plist_path=plist_path,
                write_plist_fn=write_plist_fn,
                bootstrap_fn=bootstrap_fn,
            )
        if args.command == "status":
            return _status(settings, config_path, status_fn=status_fn)
        if args.command == "logs":
            path = _log_path(settings, config_path)
            print(f"log path: {path}")
            for line in _tail(path, 100):
                print(line)
            return EXIT_OK
        if args.command == "run-once":
            return _run_monitor(
                settings,
                config_path,
                trigger="manual",
                dry_run=args.dry_run,
                runner_factory_fn=runner_factory_fn,
            )
        if args.command == "scheduled-run":
            current_time = now_fn() if now_fn else datetime.now(
                ZoneInfo(settings.timezone)
            )
            current_time = current_time.astimezone(ZoneInfo(settings.timezone))
            if not in_scheduled_window(
                current_time, settings.send_hour, settings.send_minute
            ):
                return EXIT_OK
            return _run_monitor(
                settings,
                config_path,
                trigger="scheduled",
                dry_run=False,
                runner_factory_fn=runner_factory_fn,
            )
        return EXIT_CONFIG
    except ConfigError:
        print("Configuration error.", file=sys.stderr)
        return EXIT_CONFIG
    except ValueError:
        print("Unexpected internal error.", file=sys.stderr)
        return EXIT_UNEXPECTED
    except Exception as error:
        error_type = type(error).__name__
        if error_type == "ConfigError":
            print("Configuration error.", file=sys.stderr)
            return EXIT_CONFIG
        if error_type in {
            "FeishuCLIError",
            "RepositorySchemaError",
            "SchemaError",
        }:
            print("Feishu authentication or schema error.", file=sys.stderr)
            return EXIT_FEISHU
        print("Unexpected internal error.", file=sys.stderr)
        return EXIT_UNEXPECTED


def console_main() -> None:
    raise SystemExit(main())
