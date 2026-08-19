from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy import select

from .config import load_settings
from .database import JobRunRow, NotificationDeliveryRow, NotificationOutboxRow, ScheduledJobRow, session_scope
from .service import analyze_all_requirements, create_job, enqueue_notification, evaluate_all_requirements, get_ai_settings, get_webhook_settings, list_jobs, list_requirements
from .webhook import WebhookSender


def _risk_card(requirement: Dict[str, object], *, include_ai: bool = True) -> Dict[str, object]:
    labels = {1: "预警", 2: "严重"}
    reasons = list(requirement.get("risk_reasons") or [])[:5]
    content = "**{} · {}**\n风险等级：{}\n当前节点：{}\n{}".format(
        f"#{requirement['sequence_id']}",
        requirement["name"],
        labels.get(requirement["risk_level"], "正常"),
        "、".join(requirement.get("current_nodes") or []) or "暂无",
        "\n".join(f"- {reason}" for reason in reasons),
    )
    analysis = requirement.get("ai_analysis")
    if include_ai and isinstance(analysis, dict):
        actions = list(analysis.get("actions") or [])[:3]
        action_lines = [f"- {item.get('action')}" for item in actions if isinstance(item, dict) and item.get("action")]
        content += "\n\n**AI 综合分析**\n{}".format(analysis.get("summary") or "暂无结论")
        if action_lines:
            content += "\n**建议动作**\n" + "\n".join(action_lines)
    return {"msg_type": "interactive", "card": {"header": {"template": "red" if requirement["risk_level"] == 2 else "orange", "title": {"tag": "plain_text", "content": "需求进展风险提醒"}}, "elements": [{"tag": "markdown", "content": content}]}}


def run_risk_scan(database_url: str) -> Dict[str, int]:
    counts = evaluate_all_requirements(database_url)
    ai_counts = analyze_all_requirements(database_url)
    ai_settings = get_ai_settings(database_url)
    for requirement in list_requirements(database_url):
        if requirement["archived"] or requirement["risk_level"] == 0:
            continue
        fingerprint = hashlib.sha256(json.dumps([requirement["id"], requirement["risk_level"], requirement["risk_reasons"], requirement.get("ai_analysis")], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        enqueue_notification(database_url, _risk_card(requirement, include_ai=bool(ai_settings["include_in_feishu"])), deduplication_key=f"risk:{fingerprint}")
    return {**counts, **{f"ai_{key}": value for key, value in ai_counts.items()}}


def _claim_due_jobs(database_url: str) -> list[tuple[str, str]]:
    now = datetime.now(timezone.utc)
    claimed: list[tuple[str, str]] = []
    with session_scope(database_url) as session:
        jobs = list(session.scalars(select(ScheduledJobRow).where(ScheduledJobRow.enabled.is_(True), ScheduledJobRow.next_run_at <= now).with_for_update(skip_locked=True)))
        for job in jobs:
            run_key = f"{job.id}:{job.next_run_at.isoformat()}"
            if session.scalar(select(JobRunRow).where(JobRunRow.run_key == run_key)):
                continue
            run = JobRunRow(job_id=job.id, run_key=run_key)
            session.add(run)
            job.last_run_at = now
            job.last_status = "running"
            job.next_run_at = now + timedelta(seconds=max(10, job.interval_seconds))
            session.flush()
            claimed.append((run.id, job.job_type))
    return claimed


def _finish(database_url: str, run_id: str, status: str, *, summary=None, error: Optional[str] = None) -> None:
    with session_scope(database_url) as session:
        run = session.get(JobRunRow, run_id)
        if run is None:
            return
        run.status = status
        run.finished_at = datetime.now(timezone.utc)
        run.result_summary = summary or {}
        run.error_message = error
        job = session.get(ScheduledJobRow, run.job_id)
        if job:
            job.last_status = status


def deliver_outbox(database_url: str, config_path: Path, limit: int = 50) -> Dict[str, int]:
    stored = get_webhook_settings(database_url, include_secrets=True)
    has_stored_config = bool(stored["test_configured"] or stored["prod_configured"])
    if has_stored_config:
        if not stored["enabled"]:
            return {"delivered": 0, "failed": 0, "dead": 0}
        environment = str(stored["runtime_environment"])
        webhook_url = stored[f"{environment}_webhook_url"]
        if not webhook_url:
            raise ValueError(f"Webhook URL is missing for {environment}")
        bot_keyword = str(stored["bot_keyword"] or "") or None
    else:
        settings = load_settings(config_path, require_webhook=True)
        assert settings.webhook_url is not None
        webhook_url = settings.webhook_url.get_secret_value()
        bot_keyword = settings.bot_keyword
    sender = WebhookSender(str(webhook_url), bot_keyword=bot_keyword)
    delivered = failed = dead = 0
    try:
        now = datetime.now(timezone.utc)
        with session_scope(database_url) as session:
            ids = list(session.scalars(select(NotificationOutboxRow.id).where(NotificationOutboxRow.status == "pending", NotificationOutboxRow.available_at <= now).order_by(NotificationOutboxRow.created_at).limit(limit)))
        for outbox_id in ids:
            with session_scope(database_url) as session:
                row = session.get(NotificationOutboxRow, outbox_id)
                payload = dict(row.payload) if row and row.status == "pending" else None
            if payload is None:
                continue
            result = sender.send(payload)
            with session_scope(database_url) as session:
                row = session.get(NotificationOutboxRow, outbox_id)
                if row is None or row.status != "pending":
                    continue
                row.attempt_count += 1
                session.add(NotificationDeliveryRow(outbox_id=row.id, success=result.success, status_code=result.status_code, error_message=result.error))
                if result.success:
                    row.status = "sent"
                    row.sent_at = datetime.now(timezone.utc)
                    delivered += 1
                elif row.attempt_count >= row.max_attempts:
                    row.status = "dead"
                    row.last_error = result.error
                    dead += 1
                else:
                    row.last_error = result.error
                    row.available_at = datetime.now(timezone.utc) + timedelta(seconds=min(3600, 30 * 2 ** max(0, row.attempt_count - 1)))
                    failed += 1
    finally:
        sender.close()
    return {"delivered": delivered, "failed": failed, "dead": dead}


def execute_due_jobs(database_url: str, config_path: Path) -> int:
    claimed = _claim_due_jobs(database_url)
    for run_id, job_type in claimed:
        try:
            summary = run_risk_scan(database_url) if job_type == "risk_scan" else deliver_outbox(database_url, config_path)
            _finish(database_url, run_id, "succeeded", summary=summary)
        except Exception as error:
            _finish(database_url, run_id, "failed", error=str(error))
    return len(claimed)


def worker_loop(database_url: str, config_path: Path, *, poll_seconds: int = 30, once: bool = False) -> None:
    existing_types = {job["job_type"] for job in list_jobs(database_url)}
    if "risk_scan" not in existing_types:
        create_job(database_url, {"name": "风险扫描", "job_type": "risk_scan", "interval_seconds": 3600})
    if "outbox_delivery" not in existing_types:
        create_job(database_url, {"name": "通知投递", "job_type": "outbox_delivery", "interval_seconds": 30})
    while True:
        execute_due_jobs(database_url, config_path)
        if once:
            return
        time.sleep(max(5, poll_seconds))
