import json
import multiprocessing
import stat
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from requirement_monitor.state import (
    MonitorState,
    RecentSend,
    ScheduledDailyResult,
    StateCorruptionError,
    StatePersistenceError,
    StateStore,
    normalize_send_error_code,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 25, 9, 30, tzinfo=SHANGHAI)


def _hold_state_run_lock(path, ready, release, result_queue):
    store = StateStore(Path(path))
    try:
        with store.run_lock():
            ready.set()
            result_queue.put("acquired")
            release.wait(5)
    except Exception as error:
        ready.set()
        result_queue.put(str(error))


def _try_state_run_lock(path, result_queue):
    store = StateStore(Path(path))
    try:
        with store.run_lock():
            result_queue.put("acquired")
    except Exception as error:
        result_queue.put(str(error))


def test_missing_state_loads_safe_defaults(tmp_path):
    state = StateStore(tmp_path / "monitor.json").load()

    assert state == MonitorState()


def test_save_atomically_replaces_json_with_private_permissions(tmp_path):
    path = tmp_path / "state" / "monitor.json"
    store = StateStore(path)
    state = MonitorState(
        last_successful_run=NOW,
        last_scheduled_date=NOW.date(),
        active_fingerprints={"severe:abc"},
        scheduled_daily_results={
            "2026-07-25|米家": ScheduledDailyResult(
                scheduled_date=NOW.date(),
                project="米家",
                attempted_at=NOW,
                result="success",
            )
        },
        recent_sends=[
            RecentSend(
                notification_type="严重风险",
                fingerprint="severe:abc",
                success=True,
                sent_at=NOW,
                project="米家",
                requirement_id="REQ-001",
            )
        ],
    )

    store.save(state)

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == {
        "version": 1,
        "last_successful_run": "2026-07-25T09:30:00+08:00",
        "last_scheduled_date": "2026-07-25",
        "active_fingerprints": ["severe:abc"],
        "active_fingerprint_requirements": {},
        "scheduled_daily_results": {
            "2026-07-25|米家": {
                "scheduled_date": "2026-07-25",
                "project": "米家",
                "attempted_at": "2026-07-25T09:30:00+08:00",
                "result": "success",
                "error_code": None,
            }
        },
        "recent_sends": [
            {
                "notification_type": "严重风险",
                "fingerprint": "severe:abc",
                "success": True,
                "sent_at": "2026-07-25T09:30:00+08:00",
                "project": "米家",
                "requirement_id": "REQ-001",
                "error": None,
            }
        ],
        "recovery_cursor": 0,
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob(".monitor.json.*.tmp"))
    assert store.load() == state


def test_atomic_replace_failure_preserves_previous_state(tmp_path, monkeypatch):
    path = tmp_path / "monitor.json"
    store = StateStore(path)
    original = MonitorState(active_fingerprints={"old"})
    store.save(original)

    def fail_replace(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr("requirement_monitor.state.os.replace", fail_replace)

    with pytest.raises(StatePersistenceError) as error:
        store.save(MonitorState(active_fingerprints={"new"}))

    assert str(error.value) == "STATE_WRITE_FAILED"
    assert json.loads(path.read_text(encoding="utf-8"))["active_fingerprints"] == [
        "old"
    ]
    assert not list(tmp_path.glob(".monitor.json.*.tmp"))


@pytest.mark.parametrize(
    "contents",
    ["{not-json", json.dumps({"active_fingerprints": "not-a-list"})],
)
def test_corrupt_state_is_backed_up_and_fails_safe(tmp_path, contents):
    path = tmp_path / "monitor.json"
    path.write_text(contents, encoding="utf-8")
    store = StateStore(path, now=lambda: NOW)

    with pytest.raises(
        StateCorruptionError, match="STATE_CORRUPT_BACKED_UP"
    ) as error:
        store.load()

    backups = list(tmp_path.glob("monitor.json.corrupt-20260725T093000+0800.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == contents
    assert not path.exists()
    assert str(error.value) == "STATE_CORRUPT_BACKED_UP"
    assert error.value.__cause__ is None


def test_state_schema_rejects_secret_or_arbitrary_payload_fields(tmp_path):
    path = tmp_path / "monitor.json"
    store = StateStore(path)

    with pytest.raises(Exception):
        RecentSend.model_validate(
            {
                "notification_type": "日报",
                "success": True,
                "sent_at": NOW,
                "webhook_url": "https://example.test/secret-token",
            }
        )

    store.save(MonitorState())
    assert "secret-token" not in path.read_text(encoding="utf-8")


def test_state_persistence_errors_never_expose_webhook_or_api_key(
    tmp_path, monkeypatch
):
    webhook_token = "https://open.feishu.cn/open-apis/bot/v2/hook/webhook-secret"
    api_key = "sk-super-secret-api-key"
    path = tmp_path / "monitor.json"
    store = StateStore(path)

    def fail_replace(source, destination):
        raise OSError("{} {}".format(webhook_token, api_key))

    monkeypatch.setattr("requirement_monitor.state.os.replace", fail_replace)

    with pytest.raises(StatePersistenceError) as error:
        store.save(MonitorState())

    rendered = str(error.value)
    assert rendered == "STATE_WRITE_FAILED"
    assert webhook_token not in rendered
    assert api_key not in rendered
    assert error.value.__cause__ is None
    assert not path.exists()


def test_recent_send_error_is_normalized_before_state_is_written(tmp_path):
    webhook_token = "https://open.feishu.cn/open-apis/bot/v2/hook/webhook-secret"
    api_key = "supersecretapikey"
    path = tmp_path / "monitor.json"
    store = StateStore(path)
    state = MonitorState(
        recent_sends=[
            RecentSend(
                notification_type="项目日报",
                success=False,
                sent_at=NOW,
                error="{} {}".format(webhook_token, api_key),
            )
        ]
    )

    store.save(state)

    rendered = path.read_text(encoding="utf-8")
    assert webhook_token not in rendered
    assert api_key not in rendered
    assert json.loads(rendered)["recent_sends"][0]["error"] == "SEND_ERROR"


def test_client_error_remains_diagnostic_after_normalization():
    assert normalize_send_error_code("client_error") == "client_error"


def test_recovery_journal_replays_scheduled_attempt_and_severe_confirmation(
    tmp_path,
):
    path = tmp_path / "monitor.json"
    store = StateStore(path)
    scheduled = ScheduledDailyResult(
        scheduled_date=NOW.date(),
        project="米家",
        attempted_at=NOW,
        result="success",
    )

    store.record_scheduled_attempt("2026-07-25|米家", scheduled)
    store.record_severe_confirmation(
        "severe:confirmed", "REQ-001", NOW
    )

    recovered = store.load()
    assert recovered.scheduled_daily_results["2026-07-25|米家"] == scheduled
    assert "severe:confirmed" in recovered.active_fingerprints
    assert recovered.active_fingerprint_requirements == {
        "severe:confirmed": "REQ-001"
    }
    assert recovered.recovery_cursor == 2

    store.save(recovered)
    assert store.load() == recovered
    journal_path = path.with_name("monitor.json.recovery")
    assert stat.S_IMODE(journal_path.stat().st_mode) == 0o600


def test_recovery_appends_from_two_threads_do_not_lose_events(tmp_path):
    path = tmp_path / "monitor.json"
    stores = [StateStore(path), StateStore(path)]
    start = threading.Barrier(2)
    original_loaders = [store._load_recovery_journal for store in stores]

    for store, original_loader in zip(stores, original_loaders):
        def delayed_loader(loader=original_loader):
            journal = loader()
            time.sleep(0.05)
            return journal

        store._load_recovery_journal = delayed_loader

    def append(index):
        start.wait()
        stores[index].record_scheduled_attempt(
            "2026-07-25|项目{}".format(index),
            ScheduledDailyResult(
                scheduled_date=NOW.date(),
                project="项目{}".format(index),
                attempted_at=NOW,
                result="attempted",
            ),
        )

    threads = [threading.Thread(target=append, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    recovered = StateStore(path).load()
    assert not any(thread.is_alive() for thread in threads)
    assert set(recovered.scheduled_daily_results) == {
        "2026-07-25|项目0",
        "2026-07-25|项目1",
    }


def test_run_lock_rejects_second_process_and_has_private_permissions(tmp_path):
    path = tmp_path / "monitor.json"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder_results = context.Queue()
    contender_results = context.Queue()
    holder = context.Process(
        target=_hold_state_run_lock,
        args=(str(path), ready, release, holder_results),
    )
    contender = context.Process(
        target=_try_state_run_lock,
        args=(str(path), contender_results),
    )
    try:
        holder.start()
        assert ready.wait(5)
        assert holder_results.get(timeout=5) == "acquired"
        contender.start()
        assert contender_results.get(timeout=5) == "RUN_LOCKED"
    finally:
        release.set()
        if holder.pid is not None:
            holder.join(timeout=5)
        if contender.pid is not None:
            contender.join(timeout=5)
        if holder.is_alive():
            holder.terminate()
        if contender.is_alive():
            contender.terminate()

    lock_path = path.with_name("monitor.json.run.lock")
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_run_lock_releases_after_body_error_without_masking(tmp_path):
    store = StateStore(tmp_path / "monitor.json")

    with pytest.raises(RuntimeError, match="run failed"):
        with store.run_lock():
            raise RuntimeError("run failed")

    with store.run_lock():
        pass


def test_corrupt_recovery_journal_remains_fail_closed_until_reset(tmp_path):
    path = tmp_path / "monitor.json"
    recovery_path = path.with_name("monitor.json.recovery")
    recovery_path.write_text("{broken recovery", encoding="utf-8")
    store = StateStore(path, now=lambda: NOW)

    with pytest.raises(StateCorruptionError, match="STATE_RECOVERY_CORRUPT"):
        store.load()
    with pytest.raises(StateCorruptionError, match="STATE_RECOVERY_CORRUPT"):
        store.load()

    marker = path.with_name("monitor.json.recovery.corrupt")
    backups = list(
        tmp_path.glob("monitor.json.recovery.corrupt-20260725T093000+0800.bak")
    )
    assert marker.exists()
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken recovery"

    store.reset_recovery_state()
    assert store.load() == MonitorState()


def test_marker_write_failure_keeps_corrupt_recovery_evidence_in_place(
    tmp_path,
):
    path = tmp_path / "monitor.json"
    recovery_path = path.with_name("monitor.json.recovery")
    marker_path = path.with_name("monitor.json.recovery.corrupt")
    recovery_path.write_text("{broken recovery", encoding="utf-8")
    store = StateStore(path, now=lambda: NOW)
    original_atomic_write = store._atomic_write

    def fail_marker_write(target_path, payload, error_code):
        if target_path == marker_path:
            raise StatePersistenceError(error_code)
        return original_atomic_write(target_path, payload, error_code)

    store._atomic_write = fail_marker_write

    for _ in range(2):
        with pytest.raises(
            StatePersistenceError,
            match="STATE_RECOVERY_MARKER_WRITE_FAILED",
        ):
            store.load()
        assert recovery_path.read_text(encoding="utf-8") == "{broken recovery"
        assert not marker_path.exists()

    assert list(tmp_path.glob("monitor.json.recovery.corrupt-*.bak")) == []
