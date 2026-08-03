# Offline Config And Compact Risk Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all routine settings load from `config.local.json` without required environment variables, and replace the severe-card node matrix with compact risk-family sections.

**Architecture:** Extend the validated settings model with environment-specific local Webhooks while keeping legacy environment overrides and the production `main`-branch gate. Add a presentation-only risk-family aggregation layer that maps existing `RiskFinding` records back to relevant nodes, owners, domains, stages, and earliest safe DDL without changing deterministic risk calculation.

**Tech Stack:** Python 3, Pydantic, pytest, Feishu interactive cards, existing CLI/runtime snapshot infrastructure.

---

### Task 1: Load All Routine Settings From One Local File

**Files:**
- Modify: `src/requirement_monitor/config.py`
- Modify: `config.example.json`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

Add tests proving that, with relevant environment variables cleared, a configuration containing:

```json
{
  "runtime_environment": "test",
  "webhooks": {
    "test": "https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
    "prod": "https://open.feishu.cn/open-apis/bot/v2/hook/prod-token"
  },
  "bot_keyword": "需求进展推送",
  "llm": {
    "enabled": true,
    "base_url": "https://api.example.com/v1",
    "api_key": "local-secret",
    "model": "example-model"
  }
}
```

loads the test Webhook by default, loads the prod Webhook with `runtime_environment="prod"`, and preserves `--env`/environment-variable override priority. Add a compatibility test showing legacy `webhook_url` still supplies the test Webhook. Add a test that local configuration permissions become `0600`.

- [ ] **Step 2: Run targeted tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_config.py
```

Expected: failures because `webhooks` is currently forbidden and file `runtime_environment` is overwritten by the hard-coded test default.

- [ ] **Step 3: Implement the settings model and precedence**

Add a Pydantic model equivalent to:

```python
class WebhookSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    test: Optional[SecretStr] = None
    prod: Optional[SecretStr] = None
```

Validate each non-empty URL with `is_allowed_webhook_url`. Resolve environment in this order: command override, `REQUIREMENT_MONITOR_ENV`, file `runtime_environment`, default `test`. Resolve the current Webhook in this order: matching environment variable, legacy test environment variable, local `webhooks.<environment>`, legacy local `webhook_url` for test only. Preserve the normalized selected URL in `Settings.webhook_url` for downstream compatibility.

- [ ] **Step 4: Enforce private local-file permissions**

When the resolved file is named `config.local.json`, ensure its mode is `0600` before returning settings. If permission modification fails, raise `ConfigError` with the exact repair command `chmod 600 <path>`. Do not apply this behavior to tracked `config.example.json` or temporary test fixtures unless their filename is `config.local.json`.

- [ ] **Step 5: Update the example configuration**

Replace the single Webhook placeholder with:

```json
"runtime_environment": "test",
"webhooks": {
  "test": null,
  "prod": null
}
```

Keep all secrets null in the tracked example.

- [ ] **Step 6: Run targeted tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_config.py tests/test_runtime_environment.py tests/test_cli.py
```

Expected: all selected tests pass.

### Task 2: Aggregate Existing Findings Into Risk Families

**Files:**
- Modify: `src/requirement_monitor/risk_grouping.py`
- Modify: `src/requirement_monitor/models.py`
- Test: `tests/test_risk_grouping.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing risk-family tests**

Add tests that group consequence-level reason codes into stable presentation families:

```text
节点延期与缓冲耗尽
测试周期不足
合板窗口不足
排期缺失
门禁未通过
进展停滞
阻塞风险
其他预警
```

Tests must prove that related findings merge stage/domain references without dropping source findings, severe families sort before warning families, and unknown codes remain visible under their original reason text rather than disappearing.

- [ ] **Step 2: Run targeted tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_risk_grouping.py tests/test_models.py
```

Expected: failures because family metadata and grouping do not exist.

- [ ] **Step 3: Add presentation-family models**

Add a compact model carrying:

```python
class RiskFamily(BaseModel):
    code: NonEmptyStr
    title: NonEmptyStr
    level: RiskLevel
    stage_refs: List[NonEmptyStr]
    domain_refs: List[NonEmptyStr]
    source_findings: List[RiskFinding]
```

Keep risk calculation models and stored findings unchanged.

- [ ] **Step 4: Implement deterministic family mapping**

Map reason-code prefixes and known exact codes to the approved families. Merge references with stable order. Preserve unknown findings as one-item families using the original reason code and text. Expose one function that accepts findings and stage ordering and returns ordered `RiskFamily` records.

- [ ] **Step 5: Run targeted tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_risk_grouping.py tests/test_models.py
```

Expected: all selected tests pass.

### Task 3: Replace The Severe Node Matrix With Risk-Centered Sections

**Files:**
- Modify: `src/requirement_monitor/cards.py`
- Test: `tests/test_cards.py`

- [ ] **Step 1: Write failing severe-card tests**

Add tests proving that severe cards:

- do not contain the seven node-table headers;
- show each severe risk family once;
- show merged stage/domain scope;
- show deduplicated real `<at>` owners derived from matching `NodeRisk` records;
- use the project owner when no matching node owner exists;
- show the earliest matching `safe_deadline`;
- render `项目排期` instead of `未标注`;
- combine warning families into an `其他预警` block without omitting titles;
- show the compact one-line schedule formula exactly once;
- omit the duplicated final `相关负责人` section.

- [ ] **Step 2: Run targeted tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_cards.py
```

Expected: failures because the severe card still emits `_stage_header()` and `_stage_row_units()`.

- [ ] **Step 3: Build risk-family presentation context**

For every family, match nodes when either stage or domain intersects. Deduplicate owners by `(open_id, name)`. Use the earliest non-null `safe_deadline` among matched unfinished nodes. If no node matches, use the project owner and display `项目排期` as the stage scope.

- [ ] **Step 4: Compact the requirement summary**

Render at most three lines:

```text
需求｜OKR｜版本｜当前环节
合板 MM-DD｜预计 MM-DD｜延期 N 自然日｜关键路径 域
计算：阶段 N 工作日 + 阶段 N 工作日 → MM-DD HH:MM
```

Remove launch date and the duplicated full natural-day subtraction formula. Keep the Bug reserve source suffix.

- [ ] **Step 5: Rebuild severe-card units**

Keep project-owner alert, summary, valid link buttons, severe family blocks, one compact warning block, blockers, and action. Remove stage table and final related-owner block. Keep existing byte limits, element limits, deterministic split titles, and atomic family splitting behavior.

- [ ] **Step 6: Run targeted tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_cards.py tests/test_risk_grouping.py
```

Expected: all selected tests pass.

### Task 4: Migrate Local Configuration And Documentation

**Files:**
- Modify: `config.local.json` (Git ignored)
- Modify: `README.md`
- Modify: `使用说明.md`

- [ ] **Step 1: Migrate the local file**

Set `runtime_environment` to `test`, write the previously supplied test Webhook into `webhooks.test`, leave `webhooks.prod` null, set `bot_keyword` to `需求进展推送`, retain current paths/schedule/LLM-disabled values, and set mode `0600`.

- [ ] **Step 2: Update operator documentation**

Replace required `export` instructions with direct commands. Document optional environment compatibility, test/prod selection, local file secrecy, production branch protection, the compact card layout, and risk-family merging.

- [ ] **Step 3: Verify documentation examples**

Search for stale statements claiming Webhooks or the bot keyword must be exported before normal use. Keep environment-variable examples only in an explicitly labeled compatibility/temporary-override section.

### Task 5: Full Verification And Real Dry-Run

**Files:**
- Verify all modified files

- [ ] **Step 1: Run static checks**

```bash
.venv/bin/python -m compileall -q src tests
git diff --check
```

Expected: both commands exit `0`.

- [ ] **Step 2: Run the full automated suite**

```bash
.venv/bin/pytest -q
```

Expected: zero failures.

- [ ] **Step 3: Verify schema remains current**

```bash
.venv/bin/requirement-monitor init-table --config config.local.json --dry-run
```

Expected: `Schema is up to date.`

- [ ] **Step 4: Run without environment variables**

Use a clean environment for application-specific variables and execute:

```bash
env -u REQUIREMENT_MONITOR_CONFIG \
    -u REQUIREMENT_MONITOR_ENV \
    -u REQUIREMENT_MONITOR_WEBHOOK_URL \
    -u REQUIREMENT_MONITOR_TEST_WEBHOOK_URL \
    -u REQUIREMENT_MONITOR_PROD_WEBHOOK_URL \
    -u REQUIREMENT_MONITOR_BOT_KEYWORD \
    -u REQUIREMENT_MONITOR_LLM_API_KEY \
    -u REQUIREMENT_MONITOR_LLM_BASE_URL \
    -u REQUIREMENT_MONITOR_LLM_MODEL \
    .venv/bin/requirement-monitor run-once --config config.local.json --dry-run
```

Expected: card JSON is generated without a configuration error and no Webhook is called.

- [ ] **Step 5: Inspect the generated card**

Confirm the real dry-run output contains the compact summary, one-line formula, risk-family blocks and real `<at>` values, and does not contain the seven node-table headers, `相关负责人`, or `环节：未标注`.

No Git commit is created unless the user explicitly requests one.
