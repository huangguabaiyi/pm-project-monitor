# Requirement Progress Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Mac-local requirement progress monitor that reads the approved Feishu Bitable, computes deterministic delivery risks, optionally enriches results with an LLM, and sends person-centric Feishu cards through a Webhook every workday at 20:00 or on manual command.

**Architecture:** A Python 3.9 package exposes a CLI and composes focused adapters for the existing `feishu` CLI, deterministic business rules, optional OpenAI-compatible LLM enrichment, Feishu card rendering, Webhook delivery, JSON state, and macOS `launchd`. The main runner keeps data validation and risk calculation independent from LLM availability and isolates each invalid record so other requirements still send.

**Tech Stack:** Python 3.9, Pydantic 2, HTTPX, pytest, pytest-httpx, existing `feishu` CLI 1.3.2+, macOS `launchd`, Feishu Bitable and incoming Webhook cards.

---

## File Structure

Create the following focused files:

- `pyproject.toml` — package metadata, console entry point, runtime and test dependencies.
- `.gitignore` — virtual environment, caches, local config, state, and logs.
- `config.example.json` — non-secret configuration example.
- `README.md` — installation, table initialization, commands, configuration, and troubleshooting.
- `src/requirement_monitor/__init__.py` — package version.
- `src/requirement_monitor/cli.py` — public commands and exit codes.
- `src/requirement_monitor/config.py` — JSON configuration plus environment overrides.
- `src/requirement_monitor/models.py` — domain models, enums, validation issues, and result types.
- `src/requirement_monitor/calendar.py` — workday and natural-day arithmetic.
- `src/requirement_monitor/fixed_rules.py` — deterministic parsing of the read-only business-rules file.
- `src/requirement_monitor/feishu_cli.py` — subprocess wrapper around the authenticated `feishu` CLI.
- `src/requirement_monitor/schema.py` — six-table schema manifest and idempotent bootstrap operations.
- `src/requirement_monitor/repository.py` — Bitable record loading, validation, and system-field writes.
- `src/requirement_monitor/risk.py` — project-rule resolution, safe DDL, and three-level risk engine.
- `src/requirement_monitor/llm.py` — optional OpenAI-compatible enrichment with strict downgrade protection.
- `src/requirement_monitor/cards.py` — daily, severe, data-error, and text-fallback payloads.
- `src/requirement_monitor/webhook.py` — retries and card-to-text degradation.
- `src/requirement_monitor/state.py` — atomic local JSON state and notification fingerprints.
- `src/requirement_monitor/runner.py` — end-to-end orchestration.
- `src/requirement_monitor/launchd.py` — LaunchAgent installation, removal, status, and time-window guard.
- `tests/` — unit and integration-style tests mirroring each module.

## Task 1: Scaffold the Python Package and CLI Entry Point

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `config.example.json`
- Create: `src/requirement_monitor/__init__.py`
- Create: `src/requirement_monitor/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI smoke test**

```python
from requirement_monitor.cli import main


def test_version_command(capsys):
    exit_code = main(["version"])
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "requirement-monitor 0.1.0"
```

- [ ] **Step 2: Run the test and verify import failure**

Run: `python3 -m pytest tests/test_cli.py -v`

Expected: FAIL because `requirement_monitor` does not exist.

- [ ] **Step 3: Add package metadata and the minimal CLI**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "requirement-progress-monitor"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [
  "httpx>=0.27,<1",
  "pydantic>=2.8,<3",
]

[project.optional-dependencies]
test = [
  "pytest>=8,<9",
  "pytest-httpx>=0.30,<1",
]

[project.scripts]
requirement-monitor = "requirement_monitor.cli:console_main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

```python
# src/requirement_monitor/__init__.py
__version__ = "0.1.0"
```

```python
# src/requirement_monitor/cli.py
from __future__ import annotations

import argparse
from typing import Optional, Sequence

from requirement_monitor import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="requirement-monitor")
    parser.add_argument("command", choices=["version"])
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(f"requirement-monitor {__version__}")
        return 0
    return 2


def console_main() -> None:
    raise SystemExit(main())
```

Add `.gitignore` entries for `.venv/`, `__pycache__/`, `.pytest_cache/`, `*.pyc`, `config.local.json`, `.state/`, and `logs/`. Use this exact non-secret `config.example.json`:

```json
{
  "bitable_url": "https://mi.feishu.cn/wiki/TA6nwzFi0i4fdOkIamzcxj34nRd?fromScene=spaceOverview&table=tblQlOtlW0xmcKBE&view=vewCeVIyDY",
  "fixed_rules_path": "固定业务规则",
  "timezone": "Asia/Shanghai",
  "send_hour": 20,
  "send_minute": 0,
  "state_dir": ".state",
  "log_dir": "logs",
  "llm": {
    "enabled": false
  }
}
```

- [ ] **Step 4: Install the editable package and run the test**

Run: `python3 -m venv .venv && .venv/bin/pip install -e '.[test]' && .venv/bin/pytest tests/test_cli.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the scaffold**

```bash
git add pyproject.toml .gitignore config.example.json src/requirement_monitor tests/test_cli.py
git commit -m "chore: scaffold requirement monitor package"
```

## Task 2: Define Configuration and Domain Models

**Files:**
- Create: `src/requirement_monitor/config.py`
- Create: `src/requirement_monitor/models.py`
- Test: `tests/test_config.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing configuration and model tests**

```python
from pathlib import Path

from requirement_monitor.config import load_settings


def test_environment_overrides_secret_values(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"bitable_url":"https://mi.feishu.cn/wiki/base",'
        '"fixed_rules_path":"固定业务规则","timezone":"Asia/Shanghai",'
        '"send_hour":20,"send_minute":0,"state_dir":".state",'
        '"log_dir":"logs","llm":{"enabled":false}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("REQUIREMENT_MONITOR_WEBHOOK_URL", "https://example.invalid/hook")
    settings = load_settings(config_path)
    assert settings.webhook_url.get_secret_value() == "https://example.invalid/hook"
    assert settings.fixed_rules_path == Path("固定业务规则")
```

```python
from datetime import datetime

from requirement_monitor.models import DeliveryNode, NodeStatus, RiskLevel


def test_delivery_node_requires_owner_and_deadline():
    node = DeliveryNode(
        record_id="rec-node",
        requirement_id="REQ-1",
        domain="服务端",
        work_type="研发",
        name="服务端开发",
        owner_id="ou-owner",
        owner_name="张三",
        planned_end=datetime(2026, 8, 3, 18, 0),
        status=NodeStatus.IN_PROGRESS,
    )
    assert node.risk_level == RiskLevel.NORMAL
```

- [ ] **Step 2: Run the tests and verify missing symbols**

Run: `.venv/bin/pytest tests/test_config.py tests/test_models.py -v`

Expected: FAIL because configuration and model modules do not exist.

- [ ] **Step 3: Implement settings and stable domain types**

Define these exact public types in `models.py`:

```python
class RiskLevel(IntEnum):
    NORMAL = 0
    WARNING = 1
    SEVERE = 2


class NodeStatus(str, Enum):
    NOT_STARTED = "未开始"
    IN_PROGRESS = "进行中"
    COMPLETED = "已完成"
    SKIPPED = "已跳过"
    CANCELLED = "已取消"


class ValidationIssue(BaseModel):
    table_name: str
    record_id: Optional[str] = None
    requirement_id: Optional[str] = None
    field_name: str
    message: str


class Requirement(BaseModel):
    record_id: str
    requirement_id: str
    name: str
    project: str
    current_stage: str
    project_owner_id: str
    project_owner_name: str
    product_owner_id: Optional[str] = None
    product_owner_name: Optional[str] = None
    target_version: str
    merge_at: datetime
    launch_at: Optional[datetime] = None
    briefing_completed: bool
    notification_enabled: bool
    archived: bool
    project_config_record_id: Optional[str] = None
    requirement_notes: str = ""


class DeliveryNode(BaseModel):
    record_id: str
    requirement_id: str
    domain: str
    work_type: str
    name: str
    owner_id: str
    owner_name: str
    planned_start: Optional[datetime] = None
    planned_end: datetime
    actual_end: Optional[datetime] = None
    status: NodeStatus
    progress_note: str = ""
    updated_at: Optional[datetime] = None
    risk_level: RiskLevel = RiskLevel.NORMAL
    risk_reasons: list[str] = Field(default_factory=list)
    safe_deadline: Optional[datetime] = None
```

Also define `Blocker`, `ProjectConfig`, `FixedRules`, `DataSnapshot`, `NodeRisk`, `RequirementRisk`, `LLMEnrichment`, `SendResult`, and `RunReport` with explicit fields matching the design specification. Use Pydantic validators to reject empty IDs and timezone-naive Bitable timestamps after parsing.

In `config.py`, define `LLMSettings` and `Settings`. Implement `load_settings(path: Optional[Path] = None)` so an omitted path reads `REQUIREMENT_MONITOR_CONFIG` and otherwise defaults to `config.local.json`. Load JSON first, then override `webhook_url`, `llm.api_key`, `llm.base_url`, and `llm.model` from `REQUIREMENT_MONITOR_WEBHOOK_URL`, `REQUIREMENT_MONITOR_LLM_API_KEY`, `REQUIREMENT_MONITOR_LLM_BASE_URL`, and `REQUIREMENT_MONITOR_LLM_MODEL`. Reject missing Webhook configuration with a clear validation error.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_config.py tests/test_models.py -v`

Expected: PASS.

- [ ] **Step 5: Commit configuration and models**

```bash
git add src/requirement_monitor/config.py src/requirement_monitor/models.py tests/test_config.py tests/test_models.py
git commit -m "feat: add monitor configuration and domain models"
```

## Task 3: Implement Calendar Arithmetic and Fixed Rule Parsing

**Files:**
- Create: `src/requirement_monitor/calendar.py`
- Create: `src/requirement_monitor/fixed_rules.py`
- Test: `tests/test_calendar.py`
- Test: `tests/test_fixed_rules.py`

- [ ] **Step 1: Write failing workday and rule-parser tests**

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from requirement_monitor.calendar import add_days, days_available


TZ = ZoneInfo("Asia/Shanghai")


def test_add_workdays_skips_weekend():
    friday = datetime(2026, 7, 24, 18, 0, tzinfo=TZ)
    assert add_days(friday, 1, mode="workday").date().isoformat() == "2026-07-27"


def test_natural_days_include_weekend():
    friday = datetime(2026, 7, 24, 18, 0, tzinfo=TZ)
    monday = datetime(2026, 7, 27, 18, 0, tzinfo=TZ)
    assert days_available(friday, monday, mode="natural") == 3
```

```python
from requirement_monitor.fixed_rules import parse_fixed_rules


def test_parse_current_fixed_business_rules():
    rules = parse_fixed_rules(
        "服务端的上线时间固定为每周二和周四，需在前一天提交对应的 checklist 上线表格，且下午5点30分后禁止上线\n"
        "AT1轮加二轮的测试周期一般需要一周半以上，\n"
        "PV 测试一般在 3 天左右，加上 2 天解 Bug 的时间，总计大约 5 天\n"
        "线上回归一般在3天左右\n"
    )
    assert rules.server_launch_weekdays == {1, 3}
    assert rules.server_launch_cutoff == "17:30"
    assert rules.checklist_days_before == 1
    assert rules.at_workdays == 8
    assert rules.at_natural_days == 11
    assert rules.pv_days == 3
    assert rules.bugfix_days == 2
    assert rules.regression_days == 3
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_calendar.py tests/test_fixed_rules.py -v`

Expected: FAIL because the modules do not exist.

- [ ] **Step 3: Implement deterministic date helpers and strict parsing**

Implement `add_days`, `subtract_days`, and `days_available` with modes `workday` and `natural`. Preserve local wall-clock time and skip only Saturday and Sunday in workday mode.

Implement `parse_fixed_rules(text: str) -> FixedRules` with explicit regular expressions for Tuesday/Thursday, previous-day Checklist, 17:30 cutoff, one-and-a-half-week AT, PV days, Bug-fix days, and regression days. Raise `FixedRuleParseError` listing every missing rule instead of silently inventing values. Implement `load_fixed_rules(path: Path)` as a UTF-8 read followed by parsing; never write to the file.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_calendar.py tests/test_fixed_rules.py -v`

Expected: PASS.

- [ ] **Step 5: Commit calendar and fixed-rule support**

```bash
git add src/requirement_monitor/calendar.py src/requirement_monitor/fixed_rules.py tests/test_calendar.py tests/test_fixed_rules.py
git commit -m "feat: parse fixed rules and calculate workdays"
```

## Task 4: Wrap the Existing Feishu CLI

**Files:**
- Create: `src/requirement_monitor/feishu_cli.py`
- Test: `tests/test_feishu_cli.py`

- [ ] **Step 1: Write failing subprocess-wrapper tests**

```python
import subprocess

import pytest

from requirement_monitor.feishu_cli import FeishuCLI, FeishuCLIError


def test_run_json_returns_decoded_object(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, '{"logged_in":true}', ""),
    )
    assert FeishuCLI().run_json(["auth", "status"]) == {"logged_in": True}


def test_run_json_raises_sanitized_error(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "token_invalid"),
    )
    with pytest.raises(FeishuCLIError, match="token_invalid"):
        FeishuCLI().run_json(["bitable", "meta", "app"])
```

- [ ] **Step 2: Run the test and verify failure**

Run: `.venv/bin/pytest tests/test_feishu_cli.py -v`

Expected: FAIL because `FeishuCLI` does not exist.

- [ ] **Step 3: Implement the CLI gateway**

`FeishuCLI.run_json(arguments)` must execute `feishu` with `subprocess.run`, a 60-second timeout, UTF-8 text output, no shell, and JSON decoding. Add typed helpers for `auth_status`, `meta`, `fields`, `records`, `search`, `rename_table`, `create_table`, `create_field`, `create_view`, `batch_create`, and `batch_update`. Commands must match the verified local syntax, including `feishu bitable create-table APP --name NAME --fields JSON` and `feishu bitable create-field APP TABLE --name NAME --type NUMBER --property JSON`.

Do not include Webhook or LLM secrets in exception messages or logs.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_feishu_cli.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the Feishu adapter**

```bash
git add src/requirement_monitor/feishu_cli.py tests/test_feishu_cli.py
git commit -m "feat: add authenticated feishu cli gateway"
```

## Task 5: Build an Idempotent Six-Table Schema Installer

**Files:**
- Create: `src/requirement_monitor/schema.py`
- Test: `tests/test_schema.py`
- Modify: `src/requirement_monitor/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing schema planning tests**

```python
from requirement_monitor.schema import build_schema_plan


def test_existing_data_table_is_renamed_and_missing_tables_are_created():
    meta = {"app_token": "app", "tables": [{"table_id": "tbl-main", "name": "数据表"}]}
    plan = build_schema_plan(meta, fields_by_table={"tbl-main": [{"field_name": "文本", "type": 1}]})
    assert plan[0].kind == "rename_table"
    assert plan[0].payload == {"table_id": "tbl-main", "name": "需求主表"}
    assert {item.payload.get("name") for item in plan if item.kind == "create_table"} == {
        "进展节点表", "阻塞项表", "项目配置表", "基础配置表", "通知记录表"
    }


def test_second_schema_plan_is_empty_for_complete_schema(complete_schema_meta, complete_schema_fields):
    assert build_schema_plan(complete_schema_meta, complete_schema_fields) == []
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_schema.py -v`

Expected: FAIL because the schema module does not exist.

- [ ] **Step 3: Implement the exact schema manifest and installer**

Define six tables with exact Chinese names. Use field types Text `1`, Number `2`, SingleSelect `3`, DateTime `5`, Checkbox `7`, User `11`, and SingleLink `18`.

The demand table fields must match Section 6.1 of the approved design. The progress table must include `关联需求`, `交付域`, `工作类型`, `节点名称`, `负责人`, `计划开始时间`, `计划完成时间`, `实际完成时间`, `当前状态`, `进展说明`, `最后更新时间`, `系统风险等级`, `系统风险原因`, and `最晚安全DDL`. The blocker, project configuration, basic configuration, and notification tables must use the approved field lists without extra project-management fields.

Use a two-phase installer:

1. Rename the existing `数据表` and primary field `文本` to `需求主表` and `需求编号`.
2. Create the other five tables and non-link fields.
3. Discover the new table IDs.
4. Add SingleLink fields with `{"table_id":"TARGET","multiple":false}`.
5. Seed basic configuration rows for the 16 default process nodes; delivery domains `公共流程`, `客户端`, `服务端`, `车辆`, `中枢平台`, `嵌入式`, `插件`, `助手`, and `其他`; work types `公共环节`, `研发`, `测试`, `联调`, and `发布`; and test roles `客户端测试`, `服务端测试`, `车辆测试`, `专项测试`, and `其他测试`.

Expose `init-table --dry-run` and `init-table --apply` in `cli.py`. Dry-run prints ordered operations without changing Feishu. Apply executes each operation and stops on the first schema error. Running apply a second time must produce no changes.

- [ ] **Step 4: Run schema and CLI tests**

Run: `.venv/bin/pytest tests/test_schema.py tests/test_cli.py -v`

Expected: PASS, including idempotency and dry-run output.

- [ ] **Step 5: Commit schema initialization**

```bash
git add src/requirement_monitor/schema.py src/requirement_monitor/cli.py tests/test_schema.py tests/test_cli.py
git commit -m "feat: initialize requirement monitor bitable schema"
```

## Task 6: Load, Validate, and Update Bitable Records

**Files:**
- Create: `src/requirement_monitor/repository.py`
- Test: `tests/test_repository.py`

- [ ] **Step 1: Write failing partial-validation tests**

```python
from requirement_monitor.repository import parse_snapshot


def test_invalid_node_is_reported_without_dropping_valid_requirement(raw_tables):
    raw_tables["进展节点表"][0]["fields"]["计划完成时间"] = "not-a-date"
    snapshot, issues = parse_snapshot(raw_tables)
    assert [item.requirement_id for item in snapshot.requirements] == ["REQ-1"]
    assert snapshot.nodes == []
    assert issues[0].table_name == "进展节点表"
    assert issues[0].field_name == "计划完成时间"


def test_ineligible_requirements_are_filtered_after_parsing(raw_tables):
    raw_tables["需求主表"][0]["fields"]["需求宣讲是否完成"] = False
    snapshot, issues = parse_snapshot(raw_tables)
    assert issues == []
    assert snapshot.eligible_requirements() == []
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_repository.py -v`

Expected: FAIL because repository parsing does not exist.

- [ ] **Step 3: Implement repository parsing and writes**

Implement `BitableRepository.load_snapshot()` by discovering tables by exact name, reading all pages with `--automatic-fields`, and parsing each record independently. Parse Feishu personnel arrays into stable `owner_id` and `owner_name`, timestamps into timezone-aware `datetime`, links into target record IDs, and single-select values into strings.

Return `(DataSnapshot, list[ValidationIssue])`. A malformed requirement excludes that requirement and its child records. A malformed node or blocker excludes only that child record. Missing key tables raise `RepositorySchemaError`.

Implement batch writers:

- `write_requirement_risks(results)` updates current risk, reasons, predicted completion, remaining buffer, and check time.
- `write_node_risks(results)` updates node risk, reasons, and safe DDL.
- `append_notification_records(records)` writes send and degradation outcomes.

Chunk batch operations at 500 records.

- [ ] **Step 4: Run repository tests**

Run: `.venv/bin/pytest tests/test_repository.py -v`

Expected: PASS.

- [ ] **Step 5: Commit repository support**

```bash
git add src/requirement_monitor/repository.py tests/test_repository.py
git commit -m "feat: load and validate requirement table records"
```

## Task 7: Implement the Deterministic Risk Engine

**Files:**
- Create: `src/requirement_monitor/risk.py`
- Test: `tests/test_risk.py`

- [ ] **Step 1: Write failing risk classification tests**

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from requirement_monitor.models import RiskLevel
from requirement_monitor.risk import evaluate_requirement


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 24, 12, 0, tzinfo=TZ)


def test_overdue_node_with_remaining_buffer_is_warning(requirement, node, rules):
    node.planned_end = datetime(2026, 7, 23, 18, 0, tzinfo=TZ)
    requirement.merge_at = datetime(2026, 8, 14, 18, 0, tzinfo=TZ)
    result = evaluate_requirement(requirement, [node], [], rules, NOW)
    assert result.level == RiskLevel.WARNING


def test_insufficient_test_window_is_severe(requirement, at_nodes, rules):
    requirement.merge_at = datetime(2026, 7, 31, 18, 0, tzinfo=TZ)
    result = evaluate_requirement(requirement, at_nodes, [], rules, NOW)
    assert result.level == RiskLevel.SEVERE
    assert "AT" in " ".join(result.reasons)


def test_invalid_server_launch_day_is_severe(requirement, server_nodes, rules):
    requirement.launch_at = datetime(2026, 8, 5, 17, 0, tzinfo=TZ)
    result = evaluate_requirement(requirement, server_nodes, [], rules, NOW)
    assert result.level == RiskLevel.SEVERE


def test_archived_requirement_is_never_evaluated(archived_requirement, rules):
    assert evaluate_requirement(archived_requirement, [], [], rules, NOW) is None
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_risk.py -v`

Expected: FAIL because the risk engine does not exist.

- [ ] **Step 3: Implement project rules, safe DDL, and risk aggregation**

Implement `resolve_effective_rules(fixed, project_config)` with per-field structured overrides and no natural-language interpretation.

Split aggregate test budgets deterministically:

- AT1 receives `ceil(at_days / 2)` and AT2 receives the remainder.
- PV1 receives `ceil(pv_days / 2)` and PV2 receives the remainder.
- Bug-fix reserve follows PV2.
- Online regression uses its own configured duration.
- Domain-specific special test days are added only to the matching delivery domain.

For each domain, order known nodes using the default process order from the basic configuration table, compute unfinished downstream duration, and set each node's safe DDL to `merge_at - downstream_duration`. Use the latest predicted domain completion as the requirement prediction.

Implement the approved rules exactly:

- Normal when no deadline, buffer, blocker, update-freshness, test-window, or launch rule is violated.
- Warning for an overdue node still covered by buffer, buffer of at most 2 workdays, blocker due within 1 workday, no update for 2 workdays, or missing near-term test schedule.
- Severe when minimum duration cannot fit, a domain misses merge, a merge-impacting blocker is overdue, buffer is negative, server launch weekday is not Tuesday or Thursday, server launch is after 17:30, or Checklist is incomplete on the day before launch.

Return `None` for ineligible requirements. Aggregate reasons without duplicates and keep deterministic reasons separate from future LLM reasons.

- [ ] **Step 4: Run risk tests**

Run: `.venv/bin/pytest tests/test_risk.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the risk engine**

```bash
git add src/requirement_monitor/risk.py tests/test_risk.py
git commit -m "feat: calculate delivery deadlines and deterministic risks"
```

## Task 8: Add Optional LLM Enrichment With Hard Fallback

**Files:**
- Create: `src/requirement_monitor/llm.py`
- Test: `tests/test_llm.py`

- [ ] **Step 1: Write failing success, downgrade, and token-error tests**

```python
import httpx

from requirement_monitor.models import RiskLevel
from requirement_monitor.llm import LLMClient


def test_llm_cannot_downgrade_rule_risk(httpx_mock, llm_settings, severe_risk):
    httpx_mock.add_response(json={"choices": [{"message": {"content": '{"risk_level":"普通","summary":"正常","reasons":[],"actions":[]}'}}]})
    enrichment = LLMClient(llm_settings).enrich(severe_risk, "固定规则", "项目说明")
    assert enrichment.effective_level == RiskLevel.SEVERE
    assert enrichment.available is True


def test_invalid_token_returns_unavailable_enrichment(httpx_mock, llm_settings, normal_risk):
    httpx_mock.add_response(status_code=401, json={"error": {"message": "invalid token"}})
    enrichment = LLMClient(llm_settings).enrich(normal_risk, "固定规则", "项目说明")
    assert enrichment.available is False
    assert enrichment.failure_reason == "authentication_error"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_llm.py -v`

Expected: FAIL because the LLM adapter does not exist.

- [ ] **Step 3: Implement a strict OpenAI-compatible adapter**

POST to `{base_url}/chat/completions` with `Authorization: Bearer TOKEN`, configured model, temperature `0`, and a 20-second timeout. The system prompt must state that fixed rules are read-only, document input is restricted to the provided fixed-rules text, dates cannot be changed, and risk may only be upgraded.

Require JSON content with keys `risk_level`, `summary`, `reasons`, and `actions`. Validate with Pydantic. Map Chinese levels `普通`, `预警`, and `严重` to `RiskLevel`. Set `effective_level = max(rule_level, llm_level)`.

Return an unavailable `LLMEnrichment` instead of raising for disabled configuration, missing API key, authentication failure, timeout, rate limit, empty response, invalid JSON, or schema mismatch. Never block runner execution.

- [ ] **Step 4: Run LLM tests**

Run: `.venv/bin/pytest tests/test_llm.py -v`

Expected: PASS.

- [ ] **Step 5: Commit LLM fallback support**

```bash
git add src/requirement_monitor/llm.py tests/test_llm.py
git commit -m "feat: add optional llm risk enrichment"
```

## Task 9: Render Person-Centric Feishu Cards

**Files:**
- Create: `src/requirement_monitor/cards.py`
- Test: `tests/test_cards.py`

- [ ] **Step 1: Write failing daily-card and severe-card tests**

```python
from requirement_monitor.cards import build_daily_card, build_severe_card


def test_daily_card_groups_nodes_by_owner_and_shows_both_deadlines(daily_report):
    payload = build_daily_card(daily_report)
    text = str(payload)
    assert "张三" in text
    assert "计划 DDL" in text
    assert "最晚安全 DDL" in text
    assert "服务端" in text
    assert "未来 7 天" in text


def test_severe_card_mentions_project_owner(severe_report):
    payload = build_severe_card(severe_report)
    assert '<at id="ou-project-owner">项目负责人</at>' in str(payload)
    assert payload["card"]["header"]["template"] == "red"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_cards.py -v`

Expected: FAIL because card rendering does not exist.

- [ ] **Step 3: Implement four card builders and text fallback**

Implement public functions `build_daily_card(report: RunReport) -> Dict[str, object]`, `build_severe_card(risk: RequirementRisk) -> Dict[str, object]`, `build_data_error_card(issues: List[ValidationIssue]) -> Dict[str, object]`, and `build_plain_text_fallback(title: str, lines: List[str]) -> Dict[str, object]`. Import `Dict` and `List` from `typing` for Python 3.9 compatibility.

Use these exact helpers as the shared payload boundary:

```python
def mention(open_id: str, name: str) -> str:
    return f'<at id="{open_id}">{name}</at>'


def interactive_card(title: str, template: str, markdown_blocks: List[str]) -> Dict[str, object]:
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": block}}
                for block in markdown_blocks
            ],
        },
    }


def build_plain_text_fallback(title: str, lines: List[str]) -> Dict[str, object]:
    return {
        "msg_type": "text",
        "content": {"text": "\n".join([title] + lines)},
    }
```

Use Feishu `interactive` payloads with `wide_screen_mode`. Daily cards are grouped first by project and then by owner. Within each owner, order overdue, today, warning, then future nodes. Include all unfinished nodes in the next 7 days and a count for later nodes. Each row must contain demand, domain, node, planned DDL, safe DDL, and status. Use `<at id="OPEN_ID">NAME</at>` mentions.

Use blue, yellow, and red headers for normal, warning, and severe highest risk. Add the LLM degradation footer only when enrichment was attempted but unavailable.

- [ ] **Step 4: Run card tests**

Run: `.venv/bin/pytest tests/test_cards.py -v`

Expected: PASS and serialized payloads contain no `None` values.

- [ ] **Step 5: Commit card rendering**

```bash
git add src/requirement_monitor/cards.py tests/test_cards.py
git commit -m "feat: render person-centric feishu progress cards"
```

## Task 10: Send Webhooks With Retry and Payload Degradation

**Files:**
- Create: `src/requirement_monitor/webhook.py`
- Test: `tests/test_webhook.py`

- [ ] **Step 1: Write failing retry and degradation tests**

```python
from requirement_monitor.webhook import WebhookSender


def test_sender_retries_transient_failures(httpx_mock, webhook_url, card_payload):
    httpx_mock.add_response(status_code=500)
    httpx_mock.add_response(status_code=502)
    httpx_mock.add_response(json={"code": 0})
    result = WebhookSender(webhook_url, sleep=lambda seconds: None).send(card_payload)
    assert result.success is True
    assert result.attempts == 3


def test_invalid_card_degrades_to_text(httpx_mock, webhook_url, invalid_card_payload):
    httpx_mock.add_response(status_code=400, json={"code": 190001, "msg": "invalid card"})
    httpx_mock.add_response(json={"code": 0})
    result = WebhookSender(webhook_url, sleep=lambda seconds: None).send(invalid_card_payload)
    assert result.success is True
    assert result.format_used == "text"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_webhook.py -v`

Expected: FAIL because the sender does not exist.

- [ ] **Step 3: Implement validation, retry, and fallback**

Validate that interactive cards contain `msg_type`, `card.header`, and `card.elements`. Send with HTTPX and retry only network errors, HTTP 429, and HTTP 5xx responses using delays `[10, 30, 120]`. On a Feishu card-format rejection, send one plain-text fallback without replaying the invalid card. Treat HTTP success with non-zero Feishu response code as failure.

Return `SendResult` with success, attempts, status code, Feishu code, format used, and sanitized error. Never log the Webhook URL.

- [ ] **Step 4: Run Webhook tests**

Run: `.venv/bin/pytest tests/test_webhook.py -v`

Expected: PASS.

- [ ] **Step 5: Commit Webhook delivery**

```bash
git add src/requirement_monitor/webhook.py tests/test_webhook.py
git commit -m "feat: send feishu cards with retries and fallback"
```

## Task 11: Orchestrate Runs, State, Writes, and Notification Deduplication

**Files:**
- Create: `src/requirement_monitor/state.py`
- Create: `src/requirement_monitor/runner.py`
- Test: `tests/test_state.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Write failing end-to-end orchestration tests with fakes**

```python
from requirement_monitor.runner import MonitorRunner


def test_llm_failure_still_sends_daily_card(fake_dependencies):
    fake_dependencies.llm.available = False
    report = MonitorRunner(**fake_dependencies.as_kwargs()).run(trigger="manual")
    assert report.sent_cards == 1
    assert report.llm_degraded is True
    assert fake_dependencies.webhook.payloads


def test_bad_record_does_not_block_valid_requirement(fake_dependencies):
    fake_dependencies.repository.issues.append(fake_dependencies.invalid_date_issue)
    report = MonitorRunner(**fake_dependencies.as_kwargs()).run(trigger="manual")
    assert report.processed_requirements == 1
    assert report.invalid_records == 1
    assert len(fake_dependencies.webhook.payloads) == 2


def test_same_severe_fingerprint_is_not_resent_in_one_run(fake_dependencies):
    fake_dependencies.repository.snapshot.requirements.append(
        fake_dependencies.repository.snapshot.requirements[0].model_copy()
    )
    report = MonitorRunner(**fake_dependencies.as_kwargs()).run(trigger="manual")
    assert report.severe_cards == 1
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_state.py tests/test_runner.py -v`

Expected: FAIL because state and runner modules do not exist.

- [ ] **Step 3: Implement atomic state and the orchestration pipeline**

`StateStore` must atomically replace a JSON file containing last successful run, last scheduled date, active fingerprints, and recent send summaries. Store no secrets.

`MonitorRunner.run(trigger)` must execute in this order:

1. Verify Feishu authentication.
2. Load fixed rules and Bitable snapshot.
3. Keep validation issues while processing valid records.
4. Filter out briefing-incomplete, notification-disabled, and archived requirements.
5. Resolve project rules and evaluate deterministic risk.
6. Attempt LLM enrichment when enabled.
7. Write requirement and node system fields.
8. Build one daily card per project.
9. Build separate severe cards only for fingerprints not already sent in the current state.
10. Build one data-error card when validation issues exist.
11. Send payloads and append notification records.
12. Persist fingerprints and run summary only after atomic state write succeeds.

For a manual run, always send the current daily project cards. For a scheduled run, do not send a second daily card for the same local date. Severe fingerprints remain deduplicated until the risk resolves or a fingerprint component changes.

- [ ] **Step 4: Run runner tests and the complete unit suite**

Run: `.venv/bin/pytest tests/test_state.py tests/test_runner.py -v && .venv/bin/pytest -v`

Expected: PASS.

- [ ] **Step 5: Commit orchestration**

```bash
git add src/requirement_monitor/state.py src/requirement_monitor/runner.py tests/test_state.py tests/test_runner.py
git commit -m "feat: orchestrate monitoring runs and notification state"
```

## Task 12: Add Public Commands and macOS LaunchAgent Management

**Files:**
- Create: `src/requirement_monitor/launchd.py`
- Modify: `src/requirement_monitor/cli.py`
- Test: `tests/test_launchd.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing LaunchAgent and time-window tests**

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from requirement_monitor.launchd import in_scheduled_window, render_plist


TZ = ZoneInfo("Asia/Shanghai")


def test_scheduled_window_accepts_2000_and_rejects_resume_at_2100():
    assert in_scheduled_window(datetime(2026, 7, 24, 20, 3, tzinfo=TZ), 20, 0) is True
    assert in_scheduled_window(datetime(2026, 7, 24, 21, 0, tzinfo=TZ), 20, 0) is False


def test_plist_runs_only_weekdays_at_configured_time(tmp_path):
    content = render_plist(
        python_path="/tmp/venv/bin/python",
        config_path=tmp_path / "config.local.json",
        hour=20,
        minute=0,
    )
    assert content.count("<key>Weekday</key>") == 5
    assert "<integer>20</integer>" in content
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_launchd.py tests/test_cli.py -v`

Expected: FAIL because launchd management and commands are absent.

- [ ] **Step 3: Implement LaunchAgent management and all approved commands**

Render `~/Library/LaunchAgents/com.mi.requirement-monitor.plist` with five `StartCalendarInterval` entries for Monday through Friday and the configured time. Program arguments must call the current virtual-environment Python with `-m requirement_monitor.cli scheduled-run --config CONFIG_PATH`.

Implement:

- `start` — write the plist with mode `0600`, run `launchctl bootstrap gui/$UID PLIST`, and print status.
- `stop` — run `launchctl bootout gui/$UID/com.mi.requirement-monitor` and leave the plist disabled until the next `start`.
- `restart` — stop then start.
- `status` — report loaded state, configured schedule, latest run, latest send, and latest LLM degradation.
- `run-once` — call `MonitorRunner.run(trigger="manual")` regardless of schedule; `--dry-run` renders and prints payloads without table writes or Webhook sends.
- `logs` — print the configured log path and tail the last 100 lines.
- `scheduled-run` — exit successfully without sending unless local time is a weekday and within five minutes after the configured schedule; otherwise call `MonitorRunner.run(trigger="scheduled")`.
- `init-table --dry-run|--apply` — connect Task 5 schema operations.

Use explicit exit codes: `0` success, `2` configuration error, `3` Feishu authentication or schema error, `4` complete Webhook failure, `5` unexpected internal error.

- [ ] **Step 4: Run CLI and launchd tests**

Run: `.venv/bin/pytest tests/test_launchd.py tests/test_cli.py -v && .venv/bin/pytest -v`

Expected: PASS.

- [ ] **Step 5: Commit CLI and LaunchAgent support**

```bash
git add src/requirement_monitor/launchd.py src/requirement_monitor/cli.py tests/test_launchd.py tests/test_cli.py
git commit -m "feat: manage scheduled mac monitoring commands"
```

## Task 13: Validate Against the Test Bitable and Test Webhook

**Files:**
- Create: `tests/integration/test_live_bitable.py`
- Create: `tests/integration/test_live_webhook.py`
- Modify: `README.md`

- [ ] **Step 1: Add opt-in live integration tests**

```python
import os

import pytest

from requirement_monitor.config import load_settings
from requirement_monitor.feishu_cli import FeishuCLI


@pytest.mark.skipif(
    os.getenv("REQUIREMENT_MONITOR_LIVE_TEST") != "1",
    reason="live Feishu test is opt-in",
)
def test_live_bitable_metadata_is_readable():
    settings = load_settings()
    meta = FeishuCLI().meta(settings.bitable_url)
    assert meta["type"] == "bitable"
    assert meta["app_token"]
```

The live Webhook test must send one clearly labeled `【测试】需求进展机器人连通性验证` text message and assert Feishu response code `0`. It must be skipped unless `REQUIREMENT_MONITOR_LIVE_TEST=1`.

- [ ] **Step 2: Run all non-live tests first**

Run: `.venv/bin/pytest -v`

Expected: PASS with live tests skipped.

- [ ] **Step 3: Dry-run and apply the approved table schema**

Set the table URL without storing secrets in Git:

```bash
cp config.example.json config.local.json
export REQUIREMENT_MONITOR_CONFIG="$PWD/config.local.json"
.venv/bin/requirement-monitor init-table --dry-run
.venv/bin/requirement-monitor init-table --apply
.venv/bin/requirement-monitor init-table --dry-run
```

Expected: the first dry-run lists the rename and missing tables/fields, apply succeeds, and the final dry-run reports zero operations.

- [ ] **Step 4: Run opt-in live connectivity and one manual dry run**

Load the Webhook URL into the current shell without writing it to disk:

```bash
read -s REQUIREMENT_MONITOR_WEBHOOK_URL
export REQUIREMENT_MONITOR_WEBHOOK_URL
export REQUIREMENT_MONITOR_LIVE_TEST=1
.venv/bin/pytest tests/integration -v
.venv/bin/requirement-monitor run-once --dry-run
```

Expected: Bitable metadata and Webhook tests pass. Dry-run prints the project cards and writes no system fields or notification records.

- [ ] **Step 5: Seed controlled test records and send one real manual run**

Create records covering normal, warning, severe, invalid-date, archived, and briefing-incomplete cases. Include paired service, client, and vehicle development/test nodes. Then run:

```bash
.venv/bin/requirement-monitor run-once
```

Expected: valid active requirements send, archived and briefing-incomplete requirements do not send, invalid data produces an error card, severe risk mentions the project owner, and disabling the LLM API key still sends the base cards.

- [ ] **Step 6: Commit live-test harness and operating documentation**

README must document installation, authenticated Feishu CLI requirement, local configuration, table initialization, `start`, `stop`, `restart`, `status`, `run-once`, `logs`, LLM fallback, no missed-run catch-up, and recovery from Feishu authentication expiry.

```bash
git add tests/integration README.md
git commit -m "test: verify live feishu table and webhook workflow"
```

## Task 14: Final Verification and Release Readiness

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run formatting-neutral static checks**

Run: `.venv/bin/python -m compileall -q src tests && git diff --check`

Expected: both commands exit with code `0`.

- [ ] **Step 2: Run the complete automated suite**

Run: `.venv/bin/pytest -v`

Expected: all unit tests pass and live tests skip unless explicitly enabled.

- [ ] **Step 3: Verify CLI behavior**

Run:

```bash
.venv/bin/requirement-monitor version
.venv/bin/requirement-monitor status
.venv/bin/requirement-monitor init-table --dry-run
.venv/bin/requirement-monitor run-once --dry-run
```

Expected: version is `0.1.0`, status is readable even when stopped, schema reports no missing operations after initialization, and dry-run renders cards without sending.

- [ ] **Step 4: Verify LLM-independent operation**

Run:

```bash
unset REQUIREMENT_MONITOR_LLM_API_KEY
.venv/bin/requirement-monitor run-once --dry-run
```

Expected: deterministic risks and base cards are produced, with an AI-unavailable footer and no crash.

- [ ] **Step 5: Verify LaunchAgent start and stop**

Run:

```bash
.venv/bin/requirement-monitor start
.venv/bin/requirement-monitor status
.venv/bin/requirement-monitor stop
.venv/bin/requirement-monitor status
```

Expected: status changes from loaded to stopped, and no process remains active after stop.

- [ ] **Step 6: Commit final documentation corrections**

```bash
git add README.md
git commit -m "docs: finalize requirement monitor operations guide"
```
