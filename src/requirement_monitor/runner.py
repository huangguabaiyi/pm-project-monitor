import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from pydantic import Field

from requirement_monitor.cards import (
    build_daily_card,
    build_data_error_card,
    build_severe_card,
    interactive_card,
)
from requirement_monitor.fixed_rules import parse_fixed_rules
from requirement_monitor.models import (
    FixedRules,
    LLMEnrichment,
    ProjectConfig,
    Requirement,
    RequirementRisk,
    RiskLevel,
    RunReport,
    SendResult,
    ValidationIssue,
)
from requirement_monitor.risk import evaluate_requirement
from requirement_monitor.state import MonitorState, RecentSend, StateStore


class MonitorRunReport(RunReport):
    payloads: List[Dict[str, object]] = Field(default_factory=list)
    dry_run: bool = False


@dataclass(frozen=True)
class _PendingNotification:
    notification_type: str
    payload: Dict[str, object]
    fingerprint: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.NORMAL
    project: Optional[str] = None
    requirement_record_id: Optional[str] = None
    requirement_id: Optional[str] = None
    recipient_ids: Tuple[str, ...] = ()
    llm_used: bool = False
    llm_degradation_reason: str = ""


class MonitorRunner:
    def __init__(
        self,
        *,
        feishu,
        repository,
        webhook,
        state_store: StateStore,
        fixed_rules_path: Optional[Path] = None,
        fixed_rules_loader: Optional[
            Callable[[], Tuple[FixedRules, str]]
        ] = None,
        risk_evaluator: Callable[..., Optional[RequirementRisk]] = evaluate_requirement,
        llm=None,
        now: Optional[Callable[[], datetime]] = None,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        if fixed_rules_loader is None and fixed_rules_path is None:
            raise ValueError("fixed_rules_path or fixed_rules_loader is required")
        self.feishu = feishu
        self.repository = repository
        self.webhook = webhook
        self.state_store = state_store
        self.fixed_rules_path = Path(fixed_rules_path) if fixed_rules_path else None
        self.fixed_rules_loader = fixed_rules_loader
        self.risk_evaluator = risk_evaluator
        self.llm = llm
        timezone = ZoneInfo(timezone_name)
        self._now = now or (lambda: datetime.now(timezone))

    def run(self, trigger: str, dry_run: bool = False) -> MonitorRunReport:
        started_at = self._now()
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise ValueError("runner clock must return a timezone-aware datetime")
        report = MonitorRunReport(
            trigger=trigger,
            started_at=started_at,
            dry_run=dry_run,
        )

        authentication_error = self._authentication_error()
        if authentication_error is not None:
            return self._finish_authentication_failure(
                report, authentication_error, dry_run
            )

        fixed_rules, fixed_rules_text = self._load_fixed_rules()
        snapshot, issues = self.repository.load_snapshot()
        state = self.state_store.load()
        report.total_requirements = len(snapshot.requirements)
        report.validation_issues = list(issues)
        report.invalid_records = len(issues)

        eligible = snapshot.eligible_requirements()
        report.eligible_requirement_count = len(eligible)
        project_configs = self._project_configs(snapshot.project_configs)
        risks: List[RequirementRisk] = []
        for requirement in eligible:
            project_config = project_configs.get(requirement.project)
            try:
                risk = self.risk_evaluator(
                    requirement,
                    snapshot.nodes,
                    snapshot.blockers,
                    fixed_rules,
                    started_at,
                    project_config,
                )
            except MemoryError:
                raise
            except Exception as error:
                issue = self._evaluation_issue(requirement, error)
                report.validation_issues.append(issue)
                report.invalid_records += 1
                continue
            if risk is None:
                continue
            risk = self._enrich_risk(
                risk,
                project_config,
                fixed_rules_text,
                requirement,
                report,
            )
            risks.append(risk)

        report.requirement_risks = risks
        report.processed_requirements = len(risks)
        self._set_level_counts(report)

        if not dry_run:
            self.repository.write_requirement_risks(risks)
            self.repository.write_node_risks(
                [node_risk for risk in risks for node_risk in risk.node_risks]
            )

        pending, severe_by_fingerprint = self._build_notifications(
            report, state, trigger
        )
        report.payloads = [item.payload for item in pending]
        if dry_run:
            report.finished_at = self._now()
            return report

        send_records: List[Mapping[str, Any]] = []
        recent_sends: List[RecentSend] = []
        successful_severe = set()
        daily_results: List[SendResult] = []
        for item in pending:
            result = self.webhook.send(item.payload)
            report.send_results.append(result)
            if result.success:
                report.sent_cards += 1
                if item.notification_type == "严重风险" and item.fingerprint:
                    report.severe_cards += 1
                    successful_severe.add(item.fingerprint)
            else:
                report.failed_sends += 1
            if item.notification_type == "项目日报":
                daily_results.append(result)
            send_records.append(self._notification_record(item, result, started_at))
            recent_sends.append(self._recent_send(item, result, started_at))

        if send_records:
            self.repository.append_notification_records(send_records)

        active_fingerprints = {
            fingerprint
            for fingerprint in severe_by_fingerprint
            if fingerprint in state.active_fingerprints
            or fingerprint in successful_severe
        }
        finished_at = self._now()
        report.finished_at = finished_at
        successful_run = report.failed_sends == 0 and not report.errors
        scheduled_daily_complete = (
            trigger != "scheduled"
            or state.last_scheduled_date == started_at.date()
            or (daily_results and all(result.success for result in daily_results))
            or not self._project_groups(risks)
        )
        next_state = state.model_copy(
            update={
                "last_successful_run": (
                    finished_at if successful_run else state.last_successful_run
                ),
                "last_scheduled_date": (
                    started_at.date()
                    if trigger == "scheduled" and scheduled_daily_complete
                    else state.last_scheduled_date
                ),
                "active_fingerprints": active_fingerprints,
                "recent_sends": (state.recent_sends + recent_sends)[-50:],
            }
        )
        self.state_store.save(next_state)
        return report

    def _authentication_error(self) -> Optional[str]:
        try:
            status = self.feishu.auth_status()
        except MemoryError:
            raise
        except Exception as error:
            return "Feishu authentication failed: {}".format(error)
        if not isinstance(status, Mapping):
            return "Feishu authentication failed: invalid auth response"
        authenticated = status.get("authenticated")
        if authenticated is False:
            return "Feishu authentication failed: not authenticated"
        status_text = str(status.get("status", "")).strip().lower()
        if status_text in {"unauthenticated", "not_authenticated", "expired"}:
            return "Feishu authentication failed: {}".format(status_text)
        return None

    def _finish_authentication_failure(
        self,
        report: MonitorRunReport,
        message: str,
        dry_run: bool,
    ) -> MonitorRunReport:
        report.errors.append(message)
        payload = interactive_card("需求进展监控异常", "red", [message])
        report.payloads = [payload]
        if not dry_run:
            result = self.webhook.send(payload)
            report.send_results.append(result)
            if result.success:
                report.sent_cards = 1
            else:
                report.failed_sends = 1
        report.finished_at = self._now()
        return report

    def _load_fixed_rules(self) -> Tuple[FixedRules, str]:
        if self.fixed_rules_loader is not None:
            return self.fixed_rules_loader()
        if self.fixed_rules_path is None:
            raise ValueError("fixed_rules_path is required")
        text = self.fixed_rules_path.read_text(encoding="utf-8")
        return parse_fixed_rules(text), text

    def _enrich_risk(
        self,
        risk: RequirementRisk,
        project_config: Optional[ProjectConfig],
        fixed_rules_text: str,
        requirement: Requirement,
        report: MonitorRunReport,
    ) -> RequirementRisk:
        if (
            self.llm is None
            or project_config is None
            or not project_config.llm_enabled
        ):
            return risk
        report.llm_attempted = True
        project_description = "\n".join(
            value
            for value in (project_config.llm_notes, requirement.requirement_notes)
            if value
        )
        try:
            enrichment = self.llm.enrich(
                risk, fixed_rules_text, project_description
            )
        except MemoryError:
            raise
        except Exception:
            enrichment = LLMEnrichment(
                available=False,
                rule_level=risk.level,
                effective_level=risk.level,
                failure_reason="runner_error",
            )
        if not enrichment.available:
            report.llm_degraded = True
            if enrichment.failure_reason:
                report.llm_failure_reasons = self._deduplicate(
                    report.llm_failure_reasons + [enrichment.failure_reason]
                )
        return risk.model_copy(
            update={
                "level": max(risk.level, enrichment.effective_level),
                "reasons": self._deduplicate(risk.reasons + enrichment.reasons),
                "actions": self._deduplicate(risk.actions + enrichment.actions),
                "llm_enrichment": enrichment,
            }
        )

    def _build_notifications(
        self,
        report: MonitorRunReport,
        state: MonitorState,
        trigger: str,
    ) -> Tuple[List[_PendingNotification], Dict[str, RequirementRisk]]:
        pending: List[_PendingNotification] = []
        groups = self._project_groups(report.requirement_risks)
        send_daily = (
            trigger == "manual"
            or state.last_scheduled_date != report.started_at.date()
        )
        if send_daily:
            for project, risks in groups.items():
                project_report = report.model_copy(
                    update={
                        "requirement_risks": risks,
                        "normal_requirements": sum(
                            risk.level == RiskLevel.NORMAL for risk in risks
                        ),
                        "warning_requirements": sum(
                            risk.level == RiskLevel.WARNING for risk in risks
                        ),
                        "severe_requirements": sum(
                            risk.level == RiskLevel.SEVERE for risk in risks
                        ),
                    }
                )
                pending.append(
                    _PendingNotification(
                        notification_type="项目日报",
                        payload=build_daily_card(project_report),
                        fingerprint="daily:{}:{}".format(
                            report.started_at.date().isoformat(), project
                        ),
                        risk_level=max(
                            (risk.level for risk in risks),
                            default=RiskLevel.NORMAL,
                        ),
                        project=project,
                        recipient_ids=tuple(
                            self._deduplicate(
                                [
                                    person_id
                                    for risk in risks
                                    for person_id in (
                                        risk.project_owner_id,
                                        *(
                                            node.owner_id
                                            for node in risk.node_risks
                                        ),
                                    )
                                ]
                            )
                        ),
                        llm_used=any(
                            risk.llm_enrichment is not None
                            and risk.llm_enrichment.available
                            for risk in risks
                        ),
                        llm_degradation_reason=self._llm_failure_text(risks),
                    )
                )

        severe_by_fingerprint: Dict[str, RequirementRisk] = OrderedDict()
        for risk in report.requirement_risks:
            if risk.level != RiskLevel.SEVERE:
                continue
            fingerprint = severe_fingerprint(risk)
            severe_by_fingerprint.setdefault(fingerprint, risk)
        for fingerprint, risk in severe_by_fingerprint.items():
            if fingerprint in state.active_fingerprints:
                continue
            pending.append(
                _PendingNotification(
                    notification_type="严重风险",
                    payload=build_severe_card(risk),
                    fingerprint=fingerprint,
                    risk_level=RiskLevel.SEVERE,
                    project=risk.project,
                    requirement_record_id=risk.requirement_record_id,
                    requirement_id=risk.requirement_id,
                    recipient_ids=tuple(
                        self._deduplicate(
                            [risk.project_owner_id]
                            + [node.owner_id for node in risk.node_risks]
                        )
                    ),
                    llm_used=(
                        risk.llm_enrichment is not None
                        and risk.llm_enrichment.available
                    ),
                    llm_degradation_reason=self._llm_failure_text([risk]),
                )
            )

        if report.validation_issues:
            pending.append(
                _PendingNotification(
                    notification_type="数据异常",
                    payload=build_data_error_card(report.validation_issues),
                    fingerprint="data-errors:{}:{}".format(
                        report.started_at.isoformat(),
                        len(report.validation_issues),
                    ),
                    risk_level=RiskLevel.SEVERE,
                )
            )
        return pending, severe_by_fingerprint

    @staticmethod
    def _project_configs(
        configs: Sequence[ProjectConfig],
    ) -> Dict[str, ProjectConfig]:
        result: Dict[str, ProjectConfig] = {}
        for config in configs:
            result.setdefault(config.project, config)
        return result

    @staticmethod
    def _project_groups(
        risks: Sequence[RequirementRisk],
    ) -> "OrderedDict[str, List[RequirementRisk]]":
        groups: "OrderedDict[str, List[RequirementRisk]]" = OrderedDict()
        for risk in risks:
            groups.setdefault(risk.project, []).append(risk)
        return OrderedDict((project, groups[project]) for project in sorted(groups))

    @staticmethod
    def _set_level_counts(report: MonitorRunReport) -> None:
        report.normal_requirements = sum(
            risk.level == RiskLevel.NORMAL for risk in report.requirement_risks
        )
        report.warning_requirements = sum(
            risk.level == RiskLevel.WARNING for risk in report.requirement_risks
        )
        report.severe_requirements = sum(
            risk.level == RiskLevel.SEVERE for risk in report.requirement_risks
        )

    @staticmethod
    def _evaluation_issue(
        requirement: Requirement, error: Exception
    ) -> ValidationIssue:
        return ValidationIssue(
            table_name="需求主表",
            record_id=requirement.record_id,
            requirement_id=requirement.requirement_id,
            field_name="系统风险计算",
            expected_format="可计算的需求、节点、阻塞项和项目配置",
            fix_suggestion="检查关联记录与项目配置后重试",
            skip_scope="requirement",
            message=str(error) or error.__class__.__name__,
        )

    @staticmethod
    def _notification_record(
        item: _PendingNotification,
        result: SendResult,
        sent_at: datetime,
    ) -> Mapping[str, Any]:
        return {
            "fingerprint": item.fingerprint or "",
            "requirement_record_id": item.requirement_record_id,
            "notification_type": item.notification_type,
            "risk_level": item.risk_level,
            "summary": MonitorRunner._summary(item),
            "recipient_ids": list(item.recipient_ids),
            "sent_at": sent_at,
            "send_result": "成功" if result.success else "失败",
            "error": result.error or "",
            "llm_used": item.llm_used,
            "llm_degradation_reason": item.llm_degradation_reason,
        }

    @staticmethod
    def _recent_send(
        item: _PendingNotification,
        result: SendResult,
        sent_at: datetime,
    ) -> RecentSend:
        return RecentSend(
            notification_type=item.notification_type,
            fingerprint=item.fingerprint,
            success=result.success,
            sent_at=sent_at,
            project=item.project,
            requirement_id=item.requirement_id,
            error=result.error,
        )

    @staticmethod
    def _summary(item: _PendingNotification) -> str:
        if item.requirement_id:
            return "{}｜{}".format(item.project or "", item.requirement_id)
        return item.project or item.notification_type

    @staticmethod
    def _llm_failure_text(risks: Sequence[RequirementRisk]) -> str:
        reasons = [
            risk.llm_enrichment.failure_reason
            for risk in risks
            if risk.llm_enrichment is not None
            and not risk.llm_enrichment.available
            and risk.llm_enrichment.failure_reason
        ]
        return ",".join(MonitorRunner._deduplicate(reasons))

    @staticmethod
    def _deduplicate(values: Sequence[str]) -> List[str]:
        return list(dict.fromkeys(value for value in values if value))


def severe_fingerprint(risk: RequirementRisk) -> str:
    components = {
        "requirement_id": risk.requirement_id,
        "project": risk.project,
        "project_owner_id": risk.project_owner_id,
        "risk_level": int(risk.level),
        "predicted_completion": _datetime_text(risk.predicted_completion),
        "merge_at": _datetime_text(risk.merge_at),
        "affected_domains": sorted(risk.affected_domains),
        "nodes": sorted(
            (
                {
                    "node_record_id": node.node_record_id,
                    "domain": node.domain,
                    "owner_id": node.owner_id,
                    "risk_level": int(node.level),
                    "planned_end": _datetime_text(node.planned_end),
                    "predicted_completion": _datetime_text(
                        node.predicted_completion
                    ),
                    "safe_deadline": _datetime_text(node.safe_deadline),
                }
                for node in risk.node_risks
            ),
            key=lambda item: item["node_record_id"],
        ),
        "blockers": sorted(
            (
                {
                    "record_id": blocker.record_id,
                    "owner_id": blocker.owner_id,
                    "status": blocker.status,
                    "planned_resolution_at": _datetime_text(
                        blocker.planned_resolution_at
                    ),
                    "actual_resolution_at": _datetime_text(
                        blocker.actual_resolution_at
                    ),
                }
                for blocker in risk.blockers
            ),
            key=lambda item: item["record_id"],
        ),
    }
    encoded = json.dumps(
        components,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "severe:{}".format(hashlib.sha256(encoded).hexdigest())


def _datetime_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None
