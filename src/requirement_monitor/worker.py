from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy import select

from .config import load_settings
from .database import JobRunRow, NotificationDeliveryRow, NotificationOutboxRow, ScheduledJobRow, session_scope
from .scheduler import next_cron_run
from .service import analyze_all_requirements, create_job, enqueue_notification, evaluate_all_requirements, get_ai_settings, get_requirement, get_webhook_settings, list_jobs, list_requirements
from .webhook import WebhookSender


logger = logging.getLogger("requirement_monitor.worker")


def _risk_owner_mentions(requirement: Dict[str, object]) -> list[str]:
    mentions: list[str] = []
    seen: set[str] = set()
    nodes = requirement.get("nodes") if isinstance(requirement.get("nodes"), list) else []
    for node in nodes:
        if not isinstance(node, dict) or int(node.get("risk_level") or 0) == 0:
            continue
        owners = node.get("owners") if isinstance(node.get("owners"), list) else []
        for owner in owners:
            if not isinstance(owner, dict):
                continue
            name = str(owner.get("display_name") or "").strip()
            open_id = str(owner.get("feishu_open_id") or "").strip()
            key = open_id or name
            if not key or key in seen:
                continue
            seen.add(key)
            mentions.append(f"<at id={open_id}>{name or open_id}</at>" if open_id else f"@{name}")
    return mentions


def _link_buttons(requirement: Dict[str, object]) -> list[Dict[str, object]]:
    labels = [
        ("Meego", requirement.get("meego_url")),
        ("Figma", requirement.get("figma_url")),
        ("需求文档", requirement.get("requirement_url")),
    ]
    buttons = []
    for label, url in labels:
        if not url:
            continue
        buttons.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "url": str(url),
                "type": "default",
            }
        )
    return buttons


def _risk_card(requirement: Dict[str, object], *, include_ai: bool = True) -> Dict[str, object]:
    labels = {1: "预警", 2: "严重"}
    reasons = list(requirement.get("risk_reasons") or [])[:5]
    mentions = _risk_owner_mentions(requirement)
    content = "**{} · {}**\n风险等级：{}\n当前节点：{}\n{}".format(
        f"#{requirement['sequence_id']}",
        requirement["name"],
        labels.get(requirement["risk_level"], "正常"),
        "、".join(requirement.get("current_nodes") or []) or "暂无",
        "\n".join(f"- {reason}" for reason in reasons),
    )
    if mentions:
        content += "\n\n负责人：" + " ".join(mentions)
    analysis = requirement.get("ai_analysis")
    if include_ai and isinstance(analysis, dict):
        actions = list(analysis.get("actions") or [])[:3]
        action_lines = [f"- {item.get('action')}" for item in actions if isinstance(item, dict) and item.get("action")]
        content += "\n\n**AI 综合分析**\n{}".format(analysis.get("summary") or "暂无结论")
        if action_lines:
            content += "\n**建议动作**\n" + "\n".join(action_lines)
    elements: list[Dict[str, object]] = [{"tag": "markdown", "content": content}]
    buttons = _link_buttons(requirement)
    if buttons:
        elements.append({"tag": "action", "actions": buttons})
    return {"msg_type": "interactive", "card": {"header": {"template": "red" if requirement["risk_level"] == 2 else "orange", "title": {"tag": "plain_text", "content": "需求进展风险提醒"}}, "elements": elements}}


def run_risk_scan(database_url: str) -> Dict[str, int]:
    logger.info("risk_scan.start")
    counts = evaluate_all_requirements(database_url)
    ai_settings = get_ai_settings(database_url)
    enqueued = 0
    for summary in list_requirements(database_url):
        if summary["archived"] or summary["risk_level"] == 0:
            continue
        requirement = get_requirement(database_url, str(summary["id"])) or summary
        payload = _risk_card(requirement, include_ai=bool(ai_settings["include_in_feishu"]))
        fingerprint = hashlib.sha256(json.dumps([requirement["id"], payload], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        result = enqueue_notification(database_url, payload, deduplication_key=f"risk:{fingerprint}")
        if not result.get("duplicate"):
            enqueued += 1
        logger.info("risk_scan.notification requirement_id=%s risk_level=%s status=%s duplicate=%s", requirement["id"], requirement["risk_level"], result["status"], result.get("duplicate"))
    summary = {**counts, "enqueued": enqueued}
    logger.info("risk_scan.finish summary=%s", summary)
    return summary


def run_ai_analysis(database_url: str) -> Dict[str, int]:
    logger.info("ai_analysis.start")
    summary = analyze_all_requirements(database_url)
    logger.info("ai_analysis.finish summary=%s", summary)
    return summary


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
            if (job.schedule_kind or "interval") == "cron" and job.cron_expression:
                job.next_run_at = next_cron_run(job.cron_expression, job.timezone or "Asia/Shanghai", now)
            else:
                job.next_run_at = now + timedelta(seconds=max(10, job.interval_seconds))
            session.flush()
            claimed.append((run.id, job.job_type))
            logger.info("job.claimed job_id=%s job_type=%s run_id=%s", job.id, job.job_type, run.id)
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
    if error:
        logger.error("job.finish run_id=%s status=%s error=%s", run_id, status, error)
    else:
        logger.info("job.finish run_id=%s status=%s summary=%s", run_id, status, summary or {})


def deliver_outbox(database_url: str, config_path: Path, limit: int = 50) -> Dict[str, int]:
    logger.info("outbox_delivery.start")
    stored = get_webhook_settings(database_url, include_secrets=True)
    has_stored_config = bool(stored["test_configured"] or stored["prod_configured"])
    if has_stored_config:
        if not stored["enabled"]:
            logger.info("outbox_delivery.skip reason=webhook_disabled")
            return {"delivered": 0, "failed": 0, "dead": 0}
        environment = str(stored["runtime_environment"])
        webhook_url = stored[f"{environment}_webhook_url"]
        if not webhook_url:
            logger.error("outbox_delivery.config_missing environment=%s", environment)
            raise ValueError(f"Webhook URL is missing for {environment}")
        bot_keyword = str(stored["bot_keyword"] or "") or None
        logger.info("outbox_delivery.config source=database environment=%s bot_keyword=%s", environment, "set" if bot_keyword else "empty")
    else:
        settings = load_settings(config_path, require_webhook=True)
        assert settings.webhook_url is not None
        webhook_url = settings.webhook_url.get_secret_value()
        bot_keyword = settings.bot_keyword
        logger.info("outbox_delivery.config source=file bot_keyword=%s", "set" if bot_keyword else "empty")
    sender = WebhookSender(str(webhook_url), bot_keyword=bot_keyword)
    delivered = failed = dead = 0
    try:
        now = datetime.now(timezone.utc)
        with session_scope(database_url) as session:
            ids = list(session.scalars(select(NotificationOutboxRow.id).where(NotificationOutboxRow.status == "pending", NotificationOutboxRow.available_at <= now).order_by(NotificationOutboxRow.created_at).limit(limit)))
        logger.info("outbox_delivery.pending count=%s", len(ids))
        for outbox_id in ids:
            with session_scope(database_url) as session:
                row = session.get(NotificationOutboxRow, outbox_id)
                payload = dict(row.payload) if row and row.status == "pending" else None
            if payload is None:
                continue
            logger.info("outbox_delivery.send outbox_id=%s", outbox_id)
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
                    row.last_error = None
                    delivered += 1
                    logger.info("outbox_delivery.sent outbox_id=%s attempts=%s status_code=%s feishu_code=%s", outbox_id, result.attempts, result.status_code, result.feishu_code)
                elif row.attempt_count >= row.max_attempts:
                    row.status = "dead"
                    row.last_error = result.error
                    dead += 1
                    logger.error("outbox_delivery.dead outbox_id=%s attempts=%s status_code=%s feishu_code=%s error=%s", outbox_id, row.attempt_count, result.status_code, result.feishu_code, result.error)
                else:
                    row.last_error = result.error
                    row.available_at = datetime.now(timezone.utc) + timedelta(seconds=min(3600, 30 * 2 ** max(0, row.attempt_count - 1)))
                    failed += 1
                    logger.warning("outbox_delivery.failed outbox_id=%s attempts=%s next_available_at=%s status_code=%s feishu_code=%s error=%s", outbox_id, row.attempt_count, row.available_at, result.status_code, result.feishu_code, result.error)
    finally:
        sender.close()
    summary = {"delivered": delivered, "failed": failed, "dead": dead}
    logger.info("outbox_delivery.finish summary=%s", summary)
    return summary


def execute_due_jobs(database_url: str, config_path: Path) -> int:
    claimed = _claim_due_jobs(database_url)
    if claimed:
        logger.info("jobs.execute count=%s", len(claimed))
    for run_id, job_type in claimed:
        try:
            if job_type == "risk_scan":
                summary = run_risk_scan(database_url)
            elif job_type == "ai_analysis":
                summary = run_ai_analysis(database_url)
            else:
                summary = deliver_outbox(database_url, config_path)
            _finish(database_url, run_id, "succeeded", summary=summary)
        except Exception as error:
            _finish(database_url, run_id, "failed", error=str(error))
    return len(claimed)


def worker_loop(database_url: str, config_path: Path, *, poll_seconds: int = 30, once: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.info("worker.start poll_seconds=%s once=%s config=%s", poll_seconds, once, config_path)
    existing_types = {job["job_type"] for job in list_jobs(database_url)}
    if "risk_scan" not in existing_types:
        create_job(database_url, {"name": "风险扫描", "job_type": "risk_scan", "interval_seconds": 3600})
    if "outbox_delivery" not in existing_types:
        create_job(database_url, {"name": "通知投递", "job_type": "outbox_delivery", "interval_seconds": 30})
    if "ai_analysis" not in existing_types:
        create_job(database_url, {"name": "AI 自动总结", "job_type": "ai_analysis", "interval_seconds": 3600})
    while True:
        execute_due_jobs(database_url, config_path)
        if once:
            return
        time.sleep(max(5, poll_seconds))
