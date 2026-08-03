# Development Domain Test Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every delivery domain containing development work receives the complete AT1/AT2/PV1/PV2/regression schedule, and render missing schedules grouped by their actual delivery domain.

**Architecture:** Keep domain schedule generation in `risk.py`, but change test-chain eligibility from “test node or configured role” to “development node, test node, configured role, or empty-project fallback.” Preserve each grouped risk’s source findings so the card layer can reconstruct stage-to-domain relationships without changing the deterministic risk findings emitted by the engine.

**Tech Stack:** Python 3.9, Pydantic v2, pytest, Feishu interactive-card JSON, Feishu Bitable CLI integration.

---

### Task 1: Development Domains Receive Full Test Chains

**Files:**
- Modify: `src/requirement_monitor/risk.py:1195-1220`
- Modify: `src/requirement_monitor/risk.py:1415-1426`
- Test: `tests/test_risk.py:1791`

- [ ] **Step 1: Write failing development-domain tests**

Create a platform `工作类型=研发` node and assert its schedule formula includes AT1, AT2, PV1, PV2, and online regression. Create a `工作类型=设计`-only domain and assert it does not receive AT/PV virtual stages.

- [ ] **Step 2: Run tests and verify RED**

```bash
.venv/bin/pytest -q \
  tests/test_risk.py::test_development_domain_receives_full_virtual_test_chain \
  tests/test_risk.py::test_design_only_domain_does_not_receive_at_or_pv_virtual_stages
```

Expected: the development-domain test fails because platform development currently omits AT/PV.

- [ ] **Step 3: Implement development-domain eligibility**

Update `_domain_test_schedule_enabled()` so eligibility is equivalent to:

```python
domain == "项目排期"
or any(node.work_type in {"研发", "测试"} for node in nodes)
or rules.test_role_states.get(f"{domain}测试", False)
```

An unrelated enabled test role must not make a design-only domain eligible.

- [ ] **Step 4: Run focused risk tests and verify GREEN**

Run the new tests plus existing virtual-stage, disabled-role, and dynamic-role tests.

### Task 2: Preserve Domain-to-Stage Relationships

**Files:**
- Modify: `src/requirement_monitor/models.py:29-58`
- Modify: `src/requirement_monitor/risk_grouping.py:14-180`
- Test: `tests/test_models.py`
- Test: `tests/test_risk_grouping.py`

- [ ] **Step 1: Add a failing grouping test**

Group two `schedule.buffer_low` findings where platform lacks AT1/AT2/PV1/PV2/regression and client lacks only PV1/PV2/regression. Assert the final `RiskGroup` retains both original findings.

- [ ] **Step 2: Run grouping test and verify RED**

Expected: `RiskGroup` has no `source_findings` field.

- [ ] **Step 3: Preserve source findings**

Add this field to `RiskGroup`:

```python
source_findings: List[RiskFinding] = Field(default_factory=list)
```

Append findings in `_RiskGroupState` and pass them to the resulting `RiskGroup`.

- [ ] **Step 4: Run model and grouping suites**

```bash
.venv/bin/pytest -q tests/test_models.py tests/test_risk_grouping.py
```

### Task 3: Render Missing Schedules by Domain

**Files:**
- Modify: `src/requirement_monitor/cards.py:440-490`
- Modify: `src/requirement_monitor/cards.py:1180-1290`
- Test: `tests/test_cards.py`

- [ ] **Step 1: Add failing daily and severe card tests**

Required daily structure:

```text
未确定排期
平台：AT 测试第一轮、AT 测试第二轮、PV 测试第一轮、PV 测试第二轮、线上回归
客户端：PV 测试第一轮、PV 测试第二轮、线上回归
```

For a mixed severe family, require the platform missing-schedule line plus `环节：服务端上线` and `交付域：服务端` for its non-schedule finding.

- [ ] **Step 2: Run card tests and verify RED**

Expected: current cards render ambiguous merged stage and domain lists.

- [ ] **Step 3: Implement ordered domain-stage mapping**

Build ordered `(domain, stages)` pairs from schedule-related source findings. Deduplicate stages per domain and order them by project process order. For mixed families, derive ordinary stages and domains only from non-schedule findings.

- [ ] **Step 4: Run the full card suite**

```bash
.venv/bin/pytest -q tests/test_cards.py
```

### Task 4: Documentation and End-to-End Verification

**Files:**
- Modify: `README.md:140`
- Modify: `使用说明.md:139`
- Verify: `docs/superpowers/specs/2026-08-03-development-domain-test-chain-design.md`

- [ ] **Step 1: Correct documentation**

Document that development nodes require the full test chain, while design-only domains do not.

- [ ] **Step 2: Run complete verification**

```bash
.venv/bin/pytest -q
git diff --check
```

Expected: all tests pass with only the repository’s two known skipped tests.

- [ ] **Step 3: Verify current Feishu data**

```bash
.venv/bin/requirement-monitor run-once --env test --force --dry-run
```

Verify that platform no longer uses the regression-only `2026-08-26` safe DDL, platform is listed with missing AT/PV/regression, and client/server do not list AT1/AT2 because their explicit dates exist.

- [ ] **Step 4: Send a test card**

```bash
.venv/bin/requirement-monitor run-once --env test --force
tail -n 1 logs/requirement-monitor.log
```

Expected: `sent_cards=1`, `failed_sends=0`.

- [ ] **Step 5: Independent final review**

Request read-only review of development-domain eligibility, domain-stage pairing, mixed-family rendering, and LLM-independent baseline sending. Fix findings and rerun all affected verification.

Do not create a commit because the user has not requested one and the worktree contains other approved uncommitted changes.
