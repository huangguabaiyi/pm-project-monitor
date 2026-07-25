from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from requirement_monitor.models import (
    DataSnapshot,
    DeliveryNode,
    FixedRules,
    LLMEnrichment,
    NodeRisk,
    NodeStatus,
    ProjectConfig,
    Requirement,
    RequirementRisk,
    RiskLevel,
    SendResult,
    ValidationIssue,
)
from requirement_monitor.runner import MonitorRunner
from requirement_monitor.state import MonitorState, StatePersistenceError


SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 25, 9, 30, tzinfo=SHANGHAI)


def at(day, hour=18):
    return datetime(2026, 7, day, hour, tzinfo=SHANGHAI)


def make_requirement(
    requirement_id="REQ-001",
    project="米家",
    *,
    briefing_completed=True,
    notification_enabled=True,
    archived=False,
):
    return Requirement(
        record_id="rec-{}".format(requirement_id.lower()),
        requirement_id=requirement_id,
        name="智能场景提醒",
        project=project,
        current_stage="开发中",
        project_owner_id="ou-project",
        project_owner_name="项目负责人",
        product_owner_id="ou-product",
        product_owner_name="产品负责人",
        target_version="9.0",
        merge_at=at(30),
        launch_at=None,
        briefing_completed=briefing_completed,
        notification_enabled=notification_enabled,
        archived=archived,
        requirement_notes="关注端到端稳定性",
    )


def make_node(requirement_id="REQ-001"):
    return DeliveryNode(
        record_id="node-{}".format(requirement_id.lower()),
        requirement_id=requirement_id,
        domain="客户端",
        work_type="开发",
        name="各端开发",
        owner_id="ou-node",
        owner_name="节点负责人",
        planned_start=at(24, 9),
        planned_end=at(26),
        status=NodeStatus.IN_PROGRESS,
    )


def make_config(project="米家", *, llm_enabled=True):
    return ProjectConfig(
        record_id="config-{}".format(project),
        project=project,
        duration_mode="workday",
        llm_enabled=llm_enabled,
        llm_notes="项目说明",
    )


def make_rules():
    return FixedRules(
        server_launch_weekdays={1, 3},
        server_launch_cutoff="17:30",
        checklist_days_before=3,
        at_workdays=5,
        at_natural_days=7,
        pv_days=3,
        bugfix_days=2,
        regression_days=3,
    )


def make_risk(requirement, level=RiskLevel.WARNING, *, owner_id="ou-node"):
    node_risk = NodeRisk(
        node_record_id="node-{}".format(requirement.requirement_id.lower()),
        requirement_id=requirement.requirement_id,
        node_name="各端开发",
        domain="客户端",
        owner_id=owner_id,
        owner_name="节点负责人",
        planned_end=at(26),
        status=NodeStatus.IN_PROGRESS,
        level=level,
        predicted_completion=at(31),
        safe_deadline=at(27),
        buffer_days=-1 if level == RiskLevel.SEVERE else 1,
        reasons=["预计晚于安全时间"] if level else [],
        actions=["今天确认交付计划"],
    )
    return RequirementRisk(
        requirement_record_id=requirement.record_id,
        requirement_id=requirement.requirement_id,
        requirement_name=requirement.name,
        project=requirement.project,
        target_version=requirement.target_version,
        merge_at=requirement.merge_at,
        project_owner_id=requirement.project_owner_id,
        project_owner_name=requirement.project_owner_name,
        level=level,
        predicted_completion=at(31),
        buffer_days=-1 if level == RiskLevel.SEVERE else 1,
        affected_domains=["客户端"],
        reasons=["预计晚于安全时间"],
        actions=["今天确认交付计划"],
        node_risks=[node_risk],
    )


class FakeFeishu:
    def __init__(self, events):
        self.events = events
        self.error = None
        self.status = {"authenticated": True}

    def auth_status(self):
        self.events.append("auth")
        if self.error is not None:
            raise self.error
        return self.status


class FakeRepository:
    def __init__(self, events, snapshot, issues=None):
        self.events = events
        self.snapshot = snapshot
        self.issues = list(issues or [])
        self.requirement_writes = []
        self.node_writes = []
        self.notification_batches = []
        self.load_error = None
        self.requirement_write_error = None
        self.node_write_error = None
        self.notification_error = None

    def load_snapshot(self):
        self.events.append("snapshot")
        if self.load_error is not None:
            raise self.load_error
        return self.snapshot, list(self.issues)

    def write_requirement_risks(self, risks):
        self.events.append("write_requirements")
        if self.requirement_write_error is not None:
            raise self.requirement_write_error
        self.requirement_writes.append(list(risks))

    def write_node_risks(self, risks):
        self.events.append("write_nodes")
        if self.node_write_error is not None:
            raise self.node_write_error
        self.node_writes.append(list(risks))

    def append_notification_records(self, records):
        self.events.append("notification_records")
        if self.notification_error is not None:
            raise self.notification_error
        self.notification_batches.append(list(records))


class FakeLLM:
    def __init__(self, events):
        self.events = events
        self.available = True
        self.raise_error = False

    def enrich(self, risk, fixed_rules, project_description):
        self.events.append("llm:{}".format(risk.requirement_id))
        assert "服务端" in fixed_rules
        assert "项目说明" in project_description
        if self.raise_error:
            raise RuntimeError("llm unavailable")
        if not self.available:
            return LLMEnrichment(
                available=False,
                rule_level=risk.level,
                effective_level=risk.level,
                failure_reason="timeout",
            )
        return LLMEnrichment(
            available=True,
            rule_level=risk.level,
            llm_level=risk.level,
            effective_level=risk.level,
            summary="按基础规则执行",
        )


class FakeWebhook:
    def __init__(self, events):
        self.events = events
        self.payloads = []
        self.results = []

    def send(self, payload):
        self.events.append("send")
        self.payloads.append(payload)
        if self.results:
            return self.results.pop(0)
        return SendResult(success=True, attempts=1, format_used="card")


class FakeStateStore:
    def __init__(self, events, state=None):
        self.events = events
        self.state = state or MonitorState()
        self.saved = []
        self.load_error = None
        self.save_error = None

    def load(self):
        self.events.append("state_load")
        if self.load_error is not None:
            raise self.load_error
        return self.state.model_copy(deep=True)

    def save(self, state):
        self.events.append("state_save")
        if self.save_error is not None:
            raise self.save_error
        self.state = state.model_copy(deep=True)
        self.saved.append(self.state)


class FakeDependencies:
    def __init__(self, *, level=RiskLevel.WARNING, issues=None):
        self.events = []
        requirement = make_requirement()
        snapshot = DataSnapshot(
            requirements=[requirement],
            nodes=[make_node()],
            project_configs=[make_config()],
        )
        self.feishu = FakeFeishu(self.events)
        self.repository = FakeRepository(self.events, snapshot, issues)
        self.llm = FakeLLM(self.events)
        self.webhook = FakeWebhook(self.events)
        self.state_store = FakeStateStore(self.events)
        self.level = level
        self.evaluator_calls = []
        self.rules_error = None

    def load_rules(self):
        self.events.append("rules")
        if self.rules_error is not None:
            raise self.rules_error
        return make_rules(), "服务端周二周四上线，17:30 截止"

    def evaluate(self, requirement, nodes, blockers, fixed_rules, now, project_config):
        self.events.append("evaluate:{}".format(requirement.requirement_id))
        self.evaluator_calls.append(project_config)
        return make_risk(requirement, self.level)

    def runner(self):
        return MonitorRunner(
            feishu=self.feishu,
            repository=self.repository,
            fixed_rules_loader=self.load_rules,
            risk_evaluator=self.evaluate,
            llm=self.llm,
            webhook=self.webhook,
            state_store=self.state_store,
            now=lambda: NOW,
        )


def error_issue():
    return ValidationIssue(
        table_name="进展节点表",
        record_id="bad-node",
        requirement_id="REQ-BAD",
        field_name="计划完成时间",
        current_value="明天",
        expected_format="日期时间",
        fix_suggestion="填写飞书日期字段",
        message="日期格式无效",
    )


def test_run_orders_dependencies_and_writes_before_sending():
    dependencies = FakeDependencies()

    report = dependencies.runner().run(trigger="manual")

    assert report.processed_requirements == 1
    assert report.sent_cards == 1
    assert dependencies.evaluator_calls == [
        dependencies.repository.snapshot.project_configs[0]
    ]
    assert dependencies.events == [
        "auth",
        "rules",
        "snapshot",
        "state_load",
        "evaluate:REQ-001",
        "llm:REQ-001",
        "write_requirements",
        "write_nodes",
        "send",
        "notification_records",
        "state_save",
    ]


@pytest.mark.parametrize("raise_error", [False, True])
def test_llm_failure_still_sends_daily_card(raise_error):
    dependencies = FakeDependencies()
    dependencies.llm.available = False
    dependencies.llm.raise_error = raise_error

    report = dependencies.runner().run(trigger="manual")

    assert report.sent_cards == 1
    assert report.llm_degraded is True
    assert report.llm_failure_reasons
    assert dependencies.webhook.payloads
    assert "AI 补充分析不可用" in str(dependencies.webhook.payloads[0])


def test_bad_record_does_not_block_valid_requirement_and_builds_error_card():
    dependencies = FakeDependencies(issues=[error_issue()])

    report = dependencies.runner().run(trigger="manual")

    assert report.processed_requirements == 1
    assert report.invalid_records == 1
    assert report.sent_cards == 2
    assert len(dependencies.webhook.payloads) == 2
    assert "数据异常" in str(dependencies.webhook.payloads[1])


def test_ineligible_requirements_are_filtered_before_evaluation():
    dependencies = FakeDependencies()
    dependencies.repository.snapshot.requirements.extend(
        [
            make_requirement("REQ-002", briefing_completed=False),
            make_requirement("REQ-003", notification_enabled=False),
            make_requirement("REQ-004", archived=True),
        ]
    )

    report = dependencies.runner().run(trigger="manual")

    assert report.total_requirements == 4
    assert report.eligible_requirement_count == 1
    assert report.processed_requirements == 1
    assert [event for event in dependencies.events if event.startswith("evaluate:")] == [
        "evaluate:REQ-001"
    ]


def test_manual_sends_one_daily_card_per_project_every_run():
    dependencies = FakeDependencies()
    second = make_requirement("REQ-002", project="IoT平台")
    dependencies.repository.snapshot.requirements.append(second)
    dependencies.repository.snapshot.nodes.append(make_node("REQ-002"))
    dependencies.repository.snapshot.project_configs.append(make_config("IoT平台"))

    first = dependencies.runner().run(trigger="manual")
    second_report = dependencies.runner().run(trigger="manual")

    assert first.sent_cards == 2
    assert second_report.sent_cards == 2
    assert len(dependencies.webhook.payloads) == 4


def test_scheduled_run_does_not_repeat_daily_cards_on_same_local_date():
    dependencies = FakeDependencies()

    first = dependencies.runner().run(trigger="scheduled")
    second = dependencies.runner().run(trigger="scheduled")

    assert first.sent_cards == 1
    assert second.sent_cards == 0
    assert len(dependencies.webhook.payloads) == 1
    assert dependencies.state_store.state.last_scheduled_date == NOW.date()
    scheduled = dependencies.state_store.state.scheduled_daily_results[
        "2026-07-25|米家"
    ]
    assert scheduled.result == "success"


def test_failed_scheduled_project_is_not_automatically_retried_same_day():
    dependencies = FakeDependencies()
    dependencies.webhook.results = [
        SendResult(
            success=False,
            attempts=4,
            format_used="card",
            error="service_error",
        )
    ]

    first = dependencies.runner().run(trigger="scheduled")
    second = dependencies.runner().run(trigger="scheduled")

    assert first.failed_sends == 1
    assert second.sent_cards == 0
    assert second.failed_sends == 0
    assert len(dependencies.webhook.payloads) == 1
    scheduled = dependencies.state_store.state.scheduled_daily_results[
        "2026-07-25|米家"
    ]
    assert scheduled.result == "failed"
    assert scheduled.error_code == "service_error"


def test_scheduled_deduplication_is_per_date_and_project():
    dependencies = FakeDependencies()

    first = dependencies.runner().run(trigger="scheduled")
    second_requirement = make_requirement("REQ-002", project="IoT平台")
    dependencies.repository.snapshot.requirements.append(second_requirement)
    dependencies.repository.snapshot.nodes.append(make_node("REQ-002"))
    dependencies.repository.snapshot.project_configs.append(make_config("IoT平台"))
    second = dependencies.runner().run(trigger="scheduled")

    assert first.sent_cards == 1
    assert second.sent_cards == 1
    assert len(dependencies.webhook.payloads) == 2
    assert "IoT平台" in str(dependencies.webhook.payloads[1])
    assert set(dependencies.state_store.state.scheduled_daily_results) == {
        "2026-07-25|米家",
        "2026-07-25|IoT平台",
    }


def test_manual_run_can_resend_after_scheduled_attempt():
    dependencies = FakeDependencies()

    scheduled = dependencies.runner().run(trigger="scheduled")
    manual = dependencies.runner().run(trigger="manual")

    assert scheduled.sent_cards == 1
    assert manual.sent_cards == 1
    assert len(dependencies.webhook.payloads) == 2


def test_severe_fingerprint_is_deduplicated_until_resolution_or_change():
    dependencies = FakeDependencies(level=RiskLevel.SEVERE)

    first = dependencies.runner().run(trigger="manual")
    second = dependencies.runner().run(trigger="manual")
    dependencies.level = RiskLevel.NORMAL
    resolved = dependencies.runner().run(trigger="manual")
    dependencies.level = RiskLevel.SEVERE
    reappeared = dependencies.runner().run(trigger="manual")

    assert first.sent_cards == 2
    assert first.severe_cards == 1
    assert second.sent_cards == 1
    assert second.severe_cards == 0
    assert resolved.sent_cards == 1
    assert dependencies.state_store.saved[-2].active_fingerprints == set()
    assert reappeared.sent_cards == 2
    assert reappeared.severe_cards == 1


def test_duplicate_severe_fingerprint_is_only_sent_once_in_a_run():
    dependencies = FakeDependencies(level=RiskLevel.SEVERE)
    duplicate = dependencies.repository.snapshot.requirements[0].model_copy()
    dependencies.repository.snapshot.requirements.append(duplicate)

    report = dependencies.runner().run(trigger="manual")

    assert report.processed_requirements == 2
    assert report.severe_cards == 1
    assert report.sent_cards == 2


def test_changed_severe_fingerprint_sends_again():
    dependencies = FakeDependencies(level=RiskLevel.SEVERE)
    owner_id = ["ou-node"]

    def changing_evaluator(requirement, nodes, blockers, fixed_rules, now, project_config):
        return make_risk(requirement, RiskLevel.SEVERE, owner_id=owner_id[0])

    dependencies.evaluate = changing_evaluator
    runner = dependencies.runner()
    runner.risk_evaluator = changing_evaluator

    runner.run(trigger="manual")
    owner_id[0] = "ou-new-owner"
    second = runner.run(trigger="manual")

    assert second.severe_cards == 1
    assert second.sent_cards == 2


def test_dry_run_returns_payloads_without_side_effects():
    dependencies = FakeDependencies(level=RiskLevel.SEVERE, issues=[error_issue()])
    dependencies.repository.snapshot.requirements[0].name = "超长需求" * 200

    report = dependencies.runner().run(trigger="manual", dry_run=True)

    assert len(report.payloads) == 3
    assert report.sent_cards == 0
    assert dependencies.repository.requirement_writes == []
    assert dependencies.repository.node_writes == []
    assert dependencies.repository.notification_batches == []
    assert dependencies.webhook.payloads == []
    assert dependencies.state_store.saved == []
    assert "write_requirements" not in dependencies.events
    assert "state_load" in dependencies.events
    assert not any(event.startswith("llm:") for event in dependencies.events)
    assert report.llm_attempted is False
    assert report.llm_skipped is True
    assert report.requirement_risks[0].llm_enrichment is None
    assert all("LLM skipped" in str(payload) for payload in report.payloads)


def test_state_only_tracks_successful_severe_sends_after_notification_write():
    dependencies = FakeDependencies(level=RiskLevel.SEVERE)
    dependencies.webhook.results = [
        SendResult(success=True, attempts=1, format_used="card"),
        SendResult(
            success=False,
            attempts=4,
            format_used="card",
            error="service_error",
        ),
    ]

    report = dependencies.runner().run(trigger="manual")

    assert report.sent_cards == 1
    assert report.failed_sends == 1
    assert report.severe_cards == 0
    assert dependencies.state_store.state.active_fingerprints == set()
    assert dependencies.events.index("notification_records") < dependencies.events.index(
        "state_save"
    )
    assert dependencies.state_store.state.last_successful_run is None


def test_authentication_failure_stops_table_work_and_returns_exception_payload():
    dependencies = FakeDependencies()
    dependencies.feishu.error = RuntimeError("not logged in")

    report = dependencies.runner().run(trigger="manual")

    assert report.processed_requirements == 0
    assert report.sent_cards == 1
    assert report.errors == ["AUTH_ERROR"]
    assert "监控异常" in str(dependencies.webhook.payloads[0])
    assert dependencies.repository.requirement_writes == []
    assert dependencies.state_store.saved == []


@pytest.mark.parametrize(
    "status",
    [
        {"logged_in": True},
        {"authenticated": True},
        {"status": "success"},
        {"status": "authenticated"},
    ],
)
def test_authentication_requires_an_explicit_known_success(status):
    dependencies = FakeDependencies()
    dependencies.feishu.status = status

    report = dependencies.runner().run(trigger="manual")

    assert report.processed_requirements == 1


@pytest.mark.parametrize(
    "status",
    [
        {},
        {"status": "unknown"},
        {"status": "ready"},
        {"logged_in": False},
        {"authenticated": False},
        {"logged_in": "true"},
        {"data": {"logged_in": True}},
    ],
)
def test_empty_unknown_or_failed_auth_status_stops_run(status):
    dependencies = FakeDependencies()
    dependencies.feishu.status = status

    report = dependencies.runner().run(trigger="manual")

    assert report.errors == ["AUTH_ERROR"]
    assert report.sent_cards == 1
    assert dependencies.events == ["auth", "send"]
    assert "AUTH_ERROR" in str(dependencies.webhook.payloads[0])


def test_default_runner_clock_is_timezone_aware():
    dependencies = FakeDependencies()
    dependencies.feishu.error = RuntimeError("not logged in")
    runner = MonitorRunner(
        feishu=dependencies.feishu,
        repository=dependencies.repository,
        fixed_rules_loader=dependencies.load_rules,
        risk_evaluator=dependencies.evaluate,
        llm=dependencies.llm,
        webhook=dependencies.webhook,
        state_store=dependencies.state_store,
    )

    report = runner.run(trigger="manual", dry_run=True)

    assert report.started_at.tzinfo is not None
    assert report.started_at.utcoffset() is not None


def test_notification_record_failure_is_normalized_and_state_is_not_saved():
    dependencies = FakeDependencies()
    dependencies.repository.notification_error = RuntimeError(
        "bitable write failed sk-secret"
    )

    report = dependencies.runner().run(trigger="manual")

    assert dependencies.state_store.saved == []
    assert report.errors == ["NOTIFICATION_WRITE_ERROR"]
    assert "sk-secret" not in str(report)
    assert len(dependencies.webhook.payloads) == 2
    assert "NOTIFICATION_WRITE_ERROR" in str(dependencies.webhook.payloads[-1])


@pytest.mark.parametrize(
    ("phase", "error_code"),
    [
        ("rules", "FIXED_RULES_ERROR"),
        ("snapshot", "SNAPSHOT_ERROR"),
        ("state", "STATE_ERROR"),
        ("requirement_write", "SYSTEM_WRITE_ERROR"),
        ("node_write", "SYSTEM_WRITE_ERROR"),
    ],
)
def test_run_level_failure_stops_daily_and_sends_one_sanitized_system_error(
    phase, error_code
):
    webhook_token = "https://open.feishu.cn/open-apis/bot/v2/hook/webhook-secret"
    api_key = "sk-super-secret-api-key"
    dependencies = FakeDependencies()
    failure = RuntimeError("{} {}".format(webhook_token, api_key))
    if phase == "rules":
        dependencies.rules_error = failure
    elif phase == "snapshot":
        dependencies.repository.load_error = failure
    elif phase == "state":
        dependencies.state_store.load_error = failure
    elif phase == "requirement_write":
        dependencies.repository.requirement_write_error = failure
    else:
        dependencies.repository.node_write_error = failure

    report = dependencies.runner().run(trigger="manual")

    rendered = "{} {}".format(report, dependencies.webhook.payloads)
    assert report.errors == [error_code]
    assert report.sent_cards == 1
    assert len(dependencies.webhook.payloads) == 1
    assert error_code in rendered
    assert webhook_token not in rendered
    assert api_key not in rendered
    assert dependencies.repository.notification_batches == []
    assert dependencies.state_store.saved == []


def test_state_save_failure_is_normalized_and_does_not_escape_secret():
    dependencies = FakeDependencies()
    dependencies.state_store.save_error = StatePersistenceError(
        "STATE_WRITE_FAILED sk-secret"
    )

    report = dependencies.runner().run(trigger="manual")

    assert report.errors == ["STATE_WRITE_ERROR"]
    assert "sk-secret" not in str(report)
    assert len(dependencies.webhook.payloads) == 2
    assert "STATE_WRITE_ERROR" in str(dependencies.webhook.payloads[-1])
