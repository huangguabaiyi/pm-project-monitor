import json
import stat
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
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 25, 9, 30, tzinfo=SHANGHAI)


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
