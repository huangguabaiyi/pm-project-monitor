from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import select

from .config import load_settings
from .database import (
    JobRunRow,
    NotificationDeliveryRow,
    NotificationOutboxRow,
    ScheduledJobRow,
    DatabaseRepository,
    session_scope,
)
from .models import SendResult
from .runner import MonitorRunner
from .state import StateStore
from .webhook import WebhookSender
from .service import enqueue_notification


class _DatabaseRuntime:
    def auth_status(self):
        return {"authenticated": True}


class OutboxWebhookSender:
    def __init__(self, database_url: str, *, channel: str = "default") -> None:
        self.database_url = database_url
        self.channel = channel

    def send(self, payload: Dict[str, Any]) -> SendResult:
        fingerprint = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        enqueue_notification(
            self.database_url,
            payload,
            channel=self.channel,
            deduplication_key=f"{self.channel}:{fingerprint}",
        )
        return SendResult(success=True, attempts=0, format_used="card")

    def close(self) -> None:
        return None


def run_risk_scan(database_url: str, config_path: Path) -> Dict[str, object]:
    settings = load_settings(config_path, require_webhook=False)
    if settings.data_source != "database":
        raise ValueError("worker requires data_source=database")
    state_dir = Path(settings.state_dir)
    if not state_dir.is_absolute():
        state_dir = config_path.parent / state_dir
    fixed_rules_path = Path(settings.fixed_rules_path)
    if not fixed_rules_path.is_absolute():
        fixed_rules_path = config_path.parent / fixed_rules_path
    runner = MonitorRunner(
        feishu=_DatabaseRuntime(),
        repository=DatabaseRepository(database_url),
        webhook=OutboxWebhookSender(database_url),
        state_store=StateStore(state_dir / "monitor.json"),
        fixed_rules_path=fixed_rules_path,
        timezone_name=settings.timezone,
    )
    try:
        report = runner.run(trigger="scheduled", dry_run=False, force=False)
        return {
            "errors": list(report.errors),
            "sent_cards": report.sent_cards,
            "failed_sends": report.failed_sends,
            "severe_requirements": report.severe_requirements,
            "warning_requirements": report.warning_requirements,
        }
    finally:
        runner.webhook.close()


def _claim_due_jobs(database_url: str) -> list[JobRunRow]:
    now = datetime.now(timezone.utc)
    claimed: list[JobRunRow] = []
    with session_scope(database_url) as session:
        jobs = list(
            session.scalars(
                select(ScheduledJobRow).where(
                    ScheduledJobRow.enabled.is_(True),
                    ScheduledJobRow.next_run_at <= now,
                )
            )
        )
        for job in jobs:
            scheduled_at = job.next_run_at
            run_key = f"{job.id}:{scheduled_at.isoformat()}"
            existing = session.scalar(select(JobRunRow).where(JobRunRow.run_key == run_key))
            if existing is not None:
                job.next_run_at = now + timedelta(seconds=job.interval_seconds)
                continue
            run = JobRunRow(job_id=job.id, run_key=run_key, status="running", started_at=now)
            session.add(run)
            job.last_run_at = now
            job.last_status = "running"
            job.next_run_at = now + timedelta(seconds=max(10, job.interval_seconds))
            session.flush()
            claimed.append(run)
    return claimed


def _finish_run(database_url: str, run_id: str, *, status: str, summary: Optional[Dict[str, object]] = None, error: Optional[str] = None) -> None:
    with session_scope(database_url) as session:
        run = session.get(JobRunRow, run_id)
        if run is None:
            return
        run.status = status
        run.finished_at = datetime.now(timezone.utc)
        run.result_summary = summary or {}
        run.error_message = error
        job = session.get(ScheduledJobRow, run.job_id)
        if job is not None:
            job.last_status = status


def execute_due_jobs(database_url: str, config_path: Path) -> int:
    claimed = _claim_due_jobs(database_url)
    for run in claimed:
        with session_scope(database_url) as session:
            job = session.get(ScheduledJobRow, run.job_id)
            job_type = job.job_type if job else ""
        try:
            if job_type == "risk_scan":
                summary = run_risk_scan(database_url, config_path)
            elif job_type == "outbox_delivery":
                summary = deliver_outbox(database_url, config_path)
            else:
                raise ValueError(f"unsupported job type: {job_type}")
            _finish_run(database_url, run.id, status="succeeded", summary=summary)
        except Exception as error:
            _finish_run(database_url, run.id, status="failed", error=str(error))
    return len(claimed)


def deliver_outbox(database_url: str, config_path: Path, limit: int = 50) -> Dict[str, int]:
    settings = load_settings(config_path, require_webhook=True)
    webhook_url = settings.webhook_url.get_secret_value()
    sender = WebhookSender(webhook_url, bot_keyword=settings.bot_keyword)
    delivered = failed = dead = 0
    try:
        now = datetime.now(timezone.utc)
        with session_scope(database_url) as session:
            rows = list(
                session.scalars(
                    select(NotificationOutboxRow)
                    .where(
                        NotificationOutboxRow.status == "pending",
                        NotificationOutboxRow.available_at <= now,
                    )
                    .order_by(NotificationOutboxRow.created_at)
                    .limit(limit)
                )
            )
        for row in rows:
            result = sender.send(row.payload)
            with session_scope(database_url) as session:
                current = session.get(NotificationOutboxRow, row.id)
                if current is None:
                    continue
                current.attempt_count += 1
                session.add(NotificationDeliveryRow(outbox_id=current.id, success=result.success, status_code=result.status_code, error_message=result.error))
                if result.success:
                    current.status = "sent"
                    current.sent_at = datetime.now(timezone.utc)
                    delivered += 1
                else:
                    current.last_error = result.error or "webhook delivery failed"
                    if current.attempt_count >= current.max_attempts:
                        current.status = "dead"
                        dead += 1
                    else:
                        current.available_at = datetime.now(timezone.utc) + timedelta(seconds=min(3600, 30 * (2 ** max(0, current.attempt_count - 1))))
                        failed += 1
    finally:
        sender.close()
    return {"delivered": delivered, "failed": failed, "dead": dead}


def worker_loop(database_url: str, config_path: Path, *, poll_seconds: int = 30, once: bool = False) -> None:
    while True:
        execute_due_jobs(database_url, config_path)
        try:
            deliver_outbox(database_url, config_path)
        except Exception:
            pass
        if once:
            return
        time.sleep(max(5, poll_seconds))
