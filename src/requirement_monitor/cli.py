import argparse
import fcntl
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from zoneinfo import ZoneInfo

from requirement_monitor import __version__
from requirement_monitor.config import ConfigError
from requirement_monitor.launchd import (
    LaunchdError,
    bootstrap,
    bootout,
    current_system_timezone,
    default_plist_path,
    disable,
    enable,
    in_scheduled_window,
    is_disabled as launchd_is_disabled,
    render_plist,
    status as launchd_status,
    write_plist,
)


EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_FEISHU = 3
EXIT_WEBHOOK = 4
EXIT_UNEXPECTED = 5
RUNTIME_CONFIG_FILENAME = "runtime-config.json"
LIFECYCLE_LOCK_FILENAME = "lifecycle.lock"


class LifecycleLockedError(RuntimeError):
    pass


class _RootParser(argparse.ArgumentParser):
    def __init__(self, *args, scheduled_parser=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._scheduled_parser = scheduled_parser

    def parse_args(self, args=None, namespace=None):
        values = list(sys.argv[1:] if args is None else args)
        if values and values[0] == "scheduled-run" and self._scheduled_parser:
            parsed = self._scheduled_parser.parse_args(values[1:])
            parsed.command = "scheduled-run"
            return parsed
        return super().parse_args(values, namespace)


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path)


def build_parser() -> argparse.ArgumentParser:
    scheduled_parser = argparse.ArgumentParser(
        prog="requirement-monitor scheduled-run"
    )
    _add_config_argument(scheduled_parser)

    parser = _RootParser(
        prog="requirement-monitor",
        scheduled_parser=scheduled_parser,
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{start,stop,restart,status,logs,run-once,version,init-table}",
    )

    for command in ("start", "stop", "restart", "status", "logs"):
        command_parser = subparsers.add_parser(command)
        _add_config_argument(command_parser)

    run_once_parser = subparsers.add_parser("run-once")
    _add_config_argument(run_once_parser)
    run_once_parser.add_argument("--dry-run", action="store_true")

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
    *,
    require_webhook: bool = True,
) -> Any:
    if load_settings_fn is None:
        from requirement_monitor.config import load_settings

        return load_settings(config_path, require_webhook=require_webhook)
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
        webhook=WebhookSender(
            _secret_value(settings.webhook_url),
            bot_keyword=getattr(settings, "bot_keyword", None),
        ),
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
    if failed_sends > 0:
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
        failed_sends = int(getattr(report, "failed_sends", 0))
        if failed_sends > 0:
            outcome = "partial" if getattr(report, "sent_cards", 0) else "complete"
            print(
                "Webhook delivery {} failure: {} failed, {} sent.".format(
                    outcome,
                    failed_sends,
                    getattr(report, "sent_cards", 0),
                ),
                file=sys.stderr,
            )
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
    try:
        loaded = (status_fn or launchd_status)()
    except LaunchdError as error:
        _print_launchd_error(error)
        return EXIT_UNEXPECTED
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
    enable_fn: Optional[Callable[[], Any]],
    bootstrap_fn: Optional[Callable[[Path], Any]],
    bootout_fn: Optional[Callable[[], Any]],
    disable_fn: Optional[Callable[[], Any]],
    system_timezone_fn: Optional[Callable[[], Any]],
    status_fn: Optional[Callable[[], bool]],
    lifecycle_lock_held: bool = False,
) -> int:
    if not lifecycle_lock_held:
        with _lifecycle_lock(_lifecycle_lock_path(settings, config_path)):
            return _start(
                settings,
                config_path,
                plist_path=plist_path,
                write_plist_fn=write_plist_fn,
                enable_fn=enable_fn,
                bootstrap_fn=bootstrap_fn,
                bootout_fn=bootout_fn,
                disable_fn=disable_fn,
                system_timezone_fn=system_timezone_fn,
                status_fn=status_fn,
                lifecycle_lock_held=True,
            )
    target = Path(plist_path or default_plist_path()).expanduser()
    previous_plist = _snapshot_plist(target)
    state_dir, log_dir, fixed_rules_path = _settings_paths(settings, config_path)
    runtime_config_path = state_dir / RUNTIME_CONFIG_FILENAME
    previous_runtime_config = _snapshot_file(runtime_config_path)
    previous_loaded = None
    try:
        previous_loaded = bool((status_fn or launchd_status)())
    except LaunchdError:
        previous_loaded = None
    state_changed = False
    try:
        _write_runtime_config(
            runtime_config_path,
            _runtime_config_payload(
                settings,
                state_dir=state_dir,
                log_dir=log_dir,
                fixed_rules_path=fixed_rules_path,
            ),
        )
        state_changed = True
        (enable_fn or enable)()
        content = render_plist(
            python_path=sys.executable,
            config_path=runtime_config_path,
            hour=settings.send_hour,
            minute=settings.send_minute,
            timezone=settings.timezone,
            system_timezone=(
                system_timezone_fn or current_system_timezone
            )(),
            working_directory=config_path.parent,
        )
        (write_plist_fn or write_plist)(target, content)
        try:
            (bootstrap_fn or bootstrap)(target)
        except Exception as first_error:
            try:
                (bootout_fn or bootout)()
                (bootstrap_fn or bootstrap)(target)
            except Exception as retry_error:
                if isinstance(first_error, LaunchdError) and isinstance(
                    retry_error, LaunchdError
                ):
                    raise _merge_launchd_errors(
                        first_error, retry_error
                    ) from retry_error
                raise
    except Exception:
        _rollback_start(
            target,
            previous_plist,
            runtime_config_path,
            previous_runtime_config,
            disable_fn or disable,
            state_changed,
            previous_loaded,
            enable_fn or enable,
            bootstrap_fn or bootstrap,
            write_plist_fn or write_plist,
        )
        raise
    print(f"started: {target}")
    return EXIT_OK


def _stop(
    *,
    bootout_fn: Optional[Callable[[], Any]],
    disable_fn: Optional[Callable[[], Any]],
    announce: bool = True,
) -> int:
    (bootout_fn or bootout)()
    (disable_fn or disable)()
    if announce:
        print("stopped")
    return EXIT_OK


def _restart(
    settings: Any,
    config_path: Path,
    *,
    plist_path: Optional[Path],
    write_plist_fn: Optional[Callable[[Path, str], Any]],
    enable_fn: Optional[Callable[[], Any]],
    bootstrap_fn: Optional[Callable[[Path], Any]],
    bootout_fn: Optional[Callable[[], Any]],
    disable_fn: Optional[Callable[[], Any]],
    system_timezone_fn: Optional[Callable[[], Any]],
    status_fn: Optional[Callable[[], bool]],
    disabled_status_fn: Optional[Callable[[], bool]],
    lifecycle_lock_held: bool = False,
) -> int:
    if not lifecycle_lock_held:
        with _lifecycle_lock(_lifecycle_lock_path(settings, config_path)):
            return _restart(
                settings,
                config_path,
                plist_path=plist_path,
                write_plist_fn=write_plist_fn,
                enable_fn=enable_fn,
                bootstrap_fn=bootstrap_fn,
                bootout_fn=bootout_fn,
                disable_fn=disable_fn,
                system_timezone_fn=system_timezone_fn,
                status_fn=status_fn,
                disabled_status_fn=disabled_status_fn,
                lifecycle_lock_held=True,
            )
    target = Path(plist_path or default_plist_path()).expanduser()
    state_dir, _, _ = _settings_paths(settings, config_path)
    runtime_config_path = state_dir / RUNTIME_CONFIG_FILENAME
    previous_plist = _snapshot_plist(target)
    previous_runtime_config = _snapshot_file(runtime_config_path)
    previous_loaded = bool((status_fn or launchd_status)())
    previous_disabled = bool(
        (disabled_status_fn or launchd_is_disabled)()
    )
    sensitive_values = _sensitive_values(
        settings,
        previous_runtime_config,
    )
    try:
        _stop(
            bootout_fn=bootout_fn,
            disable_fn=disable_fn,
            announce=False,
        )
        return _start(
            settings,
            config_path,
            plist_path=target,
            write_plist_fn=write_plist_fn,
            enable_fn=enable_fn,
            bootstrap_fn=bootstrap_fn,
            bootout_fn=bootout_fn,
            disable_fn=disable_fn,
            system_timezone_fn=system_timezone_fn,
            status_fn=lambda: False,
            lifecycle_lock_held=True,
        )
    except Exception as restart_error:
        try:
            _restore_restart_transaction(
                target,
                previous_plist,
                runtime_config_path,
                previous_runtime_config,
                previous_loaded=previous_loaded,
                previous_disabled=previous_disabled,
                write_plist_fn=write_plist_fn or write_plist,
                bootout_fn=bootout_fn or bootout,
                enable_fn=enable_fn or enable,
                bootstrap_fn=bootstrap_fn or bootstrap,
                disable_fn=disable_fn or disable,
            )
        except Exception as rollback_error:
            raise _merge_restart_errors(
                restart_error,
                rollback_error,
                sensitive_values,
            ) from rollback_error
        raise _restart_failure(restart_error, sensitive_values) from restart_error


def _snapshot_plist(path: Path):
    return _snapshot_file(path)


def _snapshot_file(path: Path):
    try:
        stat_result = path.stat()
        return path.read_bytes(), stat_result.st_mode & 0o777
    except FileNotFoundError:
        return None


def _restore_restart_transaction(
    plist_path: Path,
    plist_snapshot,
    runtime_config_path: Path,
    runtime_config_snapshot,
    *,
    previous_loaded: bool,
    previous_disabled: bool,
    write_plist_fn,
    bootout_fn,
    enable_fn,
    bootstrap_fn,
    disable_fn,
) -> None:
    restore_errors = []
    try:
        bootout_fn()
    except Exception as error:
        restore_errors.append(("launchd unload", error))
    try:
        _restore_private_file_strict(
            runtime_config_path,
            runtime_config_snapshot,
        )
    except Exception as error:
        restore_errors.append(("runtime config", error))
    try:
        _restore_plist_strict(plist_path, plist_snapshot, write_plist_fn)
    except Exception as error:
        restore_errors.append(("plist", error))

    if not restore_errors:
        try:
            if previous_loaded:
                enable_fn()
                bootstrap_fn(plist_path)
                if previous_disabled:
                    disable_fn()
            elif previous_disabled:
                disable_fn()
            else:
                enable_fn()
        except Exception as error:
            restore_errors.append(("launchd state", error))

    if restore_errors:
        details = "\n".join(
            "{} restore failed: {}".format(label, _error_detail(error))
            for label, error in restore_errors
        )
        raise LaunchdError("restart rollback failed", stderr=details)


def _restore_plist_strict(path: Path, snapshot, write_plist_fn) -> None:
    if snapshot is None:
        _unlink_and_fsync(path)
        return
    content, _ = snapshot
    text = content.decode("utf-8")
    try:
        write_plist_fn(path, text)
    except Exception:
        if write_plist_fn is write_plist:
            raise
        write_plist(path, text)
    os.chmod(path, 0o600)


def _restore_private_file_strict(path: Path, snapshot) -> None:
    if snapshot is None:
        _unlink_and_fsync(path)
        return
    content, _ = snapshot
    _write_private_bytes(path, content)


def _rollback_start(
    path: Path,
    snapshot,
    runtime_config_path: Path,
    runtime_config_snapshot,
    disable_fn,
    state_changed: bool,
    previous_loaded: Optional[bool],
    enable_fn,
    bootstrap_fn,
    write_plist_fn,
):
    _restore_private_file(runtime_config_path, runtime_config_snapshot)
    try:
        _restore_plist_strict(path, snapshot, write_plist_fn)
    except (OSError, UnicodeError):
        pass
    if state_changed and previous_loaded:
        try:
            enable_fn()
            bootstrap_fn(path)
            return
        except Exception:
            pass
    if state_changed:
        try:
            disable_fn()
        except Exception:
            pass


def _runtime_config_payload(
    settings: Any,
    *,
    state_dir: Path,
    log_dir: Path,
    fixed_rules_path: Path,
):
    llm = settings.llm
    return {
        "bitable_url": settings.bitable_url,
        "webhook_url": _secret_value(settings.webhook_url),
        "bot_keyword": getattr(settings, "bot_keyword", None),
        "fixed_rules_path": str(fixed_rules_path),
        "timezone": settings.timezone,
        "send_hour": settings.send_hour,
        "send_minute": settings.send_minute,
        "state_dir": str(state_dir),
        "log_dir": str(log_dir),
        "llm": {
            "enabled": llm.enabled,
            "base_url": llm.base_url,
            "api_key": (
                _secret_value(llm.api_key)
                if llm.enabled and llm.api_key is not None
                else None
            ),
            "model": llm.model,
            "timeout_seconds": llm.timeout_seconds,
        },
    }


def _write_runtime_config(path: Path, payload: Any) -> Path:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8")
    _write_private_bytes(path, content)
    return path


def _restore_private_file(path: Path, snapshot) -> None:
    try:
        _restore_private_file_strict(path, snapshot)
    except OSError:
        pass


def _write_private_bytes(path: Path, content: bytes) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".{}-".format(target.name),
            dir=str(target.parent),
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
        os.chmod(target, 0o600)
        _fsync_directory(target.parent)
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _lifecycle_lock_path(settings: Any, config_path: Path) -> Path:
    state_dir = _absolute_setting_path(settings.state_dir, config_path)
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / LIFECYCLE_LOCK_FILENAME


@contextmanager
def _lifecycle_lock(path: Path):
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    locked = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError as error:
            raise LifecycleLockedError("lifecycle operation is locked") from error
        yield
    finally:
        try:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _unlink_and_fsync(path: Path) -> None:
    target = Path(path)
    try:
        target.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(target.parent)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _merge_launchd_errors(first: LaunchdError, retry: LaunchdError):
    stderr = "\n".join(
        detail for detail in (first.stderr, retry.stderr) if detail
    )
    return LaunchdError(
        "launchctl bootstrap failed after retry",
        returncode=retry.returncode or first.returncode,
        stderr=stderr,
    )


def _restart_failure(error: Exception, sensitive_values) -> LaunchdError:
    return LaunchdError(
        "restart failed; previous state restored",
        stderr=_redact_sensitive_text(
            "restart error: {}".format(_error_detail(error)),
            sensitive_values,
        ),
    )


def _merge_restart_errors(
    restart_error: Exception,
    rollback_error: Exception,
    sensitive_values,
) -> LaunchdError:
    details = "restart error: {}\nrollback error: {}".format(
        _error_detail(restart_error),
        _error_detail(rollback_error),
    )
    return LaunchdError(
        "restart failed and rollback failed",
        stderr=_redact_sensitive_text(details, sensitive_values),
    )


def _error_detail(error: Exception) -> str:
    details = [str(error)]
    stderr = str(getattr(error, "stderr", "") or "")
    if stderr:
        details.append(stderr)
    return ": ".join(detail for detail in details if detail)


def _sensitive_values(settings: Any, runtime_snapshot) -> Sequence[str]:
    values = []
    webhook_url = _secret_value(settings.webhook_url)
    values.extend((webhook_url, webhook_url.rsplit("/", 1)[-1]))
    api_key = getattr(settings.llm, "api_key", None)
    if api_key is not None:
        values.append(_secret_value(api_key))
    if runtime_snapshot is not None:
        content, _ = runtime_snapshot
        try:
            previous = json.loads(content.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError):
            previous = None
        if isinstance(previous, dict):
            previous_webhook = previous.get("webhook_url")
            if isinstance(previous_webhook, str):
                values.extend(
                    (previous_webhook, previous_webhook.rsplit("/", 1)[-1])
                )
            previous_llm = previous.get("llm")
            if isinstance(previous_llm, dict):
                previous_api_key = previous_llm.get("api_key")
                if isinstance(previous_api_key, str):
                    values.append(previous_api_key)
    return tuple(value for value in values if value)


def _redact_sensitive_text(text: str, sensitive_values: Sequence[str]) -> str:
    redacted = text
    for value in sorted(set(sensitive_values), key=len, reverse=True):
        redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def _print_launchd_error(error: LaunchdError) -> None:
    print(f"Launchd error: {error}", file=sys.stderr)
    if error.stderr:
        print(error.stderr, file=sys.stderr)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    initialize_schema_fn: Optional[Callable[..., Sequence[Any]]] = None,
    load_settings_fn: Optional[Callable[[Optional[Path]], Any]] = None,
    runner_factory_fn: Optional[Callable[..., Any]] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
    plist_path: Optional[Path] = None,
    write_plist_fn: Optional[Callable[[Path, str], Any]] = None,
    enable_fn: Optional[Callable[[], Any]] = None,
    bootstrap_fn: Optional[Callable[[Path], Any]] = None,
    bootout_fn: Optional[Callable[[], Any]] = None,
    disable_fn: Optional[Callable[[], Any]] = None,
    status_fn: Optional[Callable[[], bool]] = None,
    disabled_status_fn: Optional[Callable[[], bool]] = None,
    system_timezone_fn: Optional[Callable[[], Any]] = None,
) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "version":
        print(f"requirement-monitor {__version__}")
        return EXIT_OK

    try:
        if args.command == "stop":
            return _stop(bootout_fn=bootout_fn, disable_fn=disable_fn)

        config_path = _resolve_config_path(args.config)
        settings = _load_settings(
            config_path,
            load_settings_fn,
            require_webhook=args.command != "init-table",
        )

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
                enable_fn=enable_fn,
                bootstrap_fn=bootstrap_fn,
                bootout_fn=bootout_fn,
                disable_fn=disable_fn,
                system_timezone_fn=system_timezone_fn,
                status_fn=status_fn,
            )
        if args.command == "restart":
            return _restart(
                settings,
                config_path,
                plist_path=plist_path,
                write_plist_fn=write_plist_fn,
                enable_fn=enable_fn,
                bootstrap_fn=bootstrap_fn,
                bootout_fn=bootout_fn,
                disable_fn=disable_fn,
                system_timezone_fn=system_timezone_fn,
                status_fn=status_fn,
                disabled_status_fn=disabled_status_fn,
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
            if (
                current_time.tzinfo is None
                or current_time.utcoffset() is None
            ):
                print("Clock configuration error.", file=sys.stderr)
                return EXIT_CONFIG
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
    except LaunchdError as error:
        _print_launchd_error(error)
        return EXIT_UNEXPECTED
    except LifecycleLockedError:
        print("Lifecycle operation locked.", file=sys.stderr)
        return EXIT_UNEXPECTED
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
