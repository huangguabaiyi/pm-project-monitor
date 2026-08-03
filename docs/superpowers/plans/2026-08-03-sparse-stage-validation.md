# Sparse Stage Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow projects to maintain only selected progress nodes while showing absent relevant stages as a non-blocking card reminder instead of a failed gate, without losing current/future schedule duration.

**Architecture:** Add a `process_reminders` collection to `RequirementRisk`, derive missing relevant stages separately from `RiskFinding`, and make test gates evaluate only stage/domain combinations that have explicit nodes. Current/future missing AT1, AT2, PV1, PV2 and regression stages remain read-only virtual schedule terms using project configuration or fixed `4/4/3/2/2` defaults. Render reminders as an optional low-priority footer; keep data-error validation and explicit-node gate failures unchanged.

**Tech Stack:** Python 3, Pydantic models, pytest, Feishu interactive-card JSON.

---

### Task 1: Model non-risk process reminders

**Files:**
- Modify: `src/requirement_monitor/models.py:454`
- Test: `tests/test_models.py`

- [ ] Add `process_reminders: List[NonEmptyStr] = Field(default_factory=list)` to `RequirementRisk`.
- [ ] Add a model test proving reminders are optional, deduplicated by the evaluator, and do not alter `level`, `reasons`, or `findings`.
- [ ] Run `.venv/bin/pytest -q tests/test_models.py` and expect all tests to pass.

### Task 2: Separate missing nodes from failed gates

**Files:**
- Modify: `src/requirement_monitor/risk.py:278`
- Modify: `src/requirement_monitor/risk.py:458`
- Modify: `src/requirement_monitor/risk.py:762`
- Test: `tests/test_risk.py`

- [ ] Add failing tests proving an absent current-stage node, absent AT/PV rounds, absent online regression, and absent translation do not create missing-gate `RiskFinding`; virtual schedule overruns may still raise the risk level.
- [ ] Add failing tests proving explicitly created incomplete AT/PV/online-regression nodes still trigger the existing severe gates.
- [ ] Replace `_current_stage_warning` with a reminder collector that returns display-stage names without changing risk level.
- [ ] Change AT/PV gate loops to evaluate a round only when that domain has an explicit node for the round.
- [ ] Remove `test_gate.regression_missing`; keep `test_gate.regression_incomplete` only for explicit regression nodes that are not completed or skipped.
- [ ] Change translation handling so an absent translation node becomes a process reminder, while an explicit incomplete translation node remains a non-blocking Warning.
- [ ] Stop synthesizing `test.schedule_missing` from a completely absent phase; create read-only virtual AT1/AT2/PV1/PV2/regression durations for current and future schedule calculation instead.
- [ ] Run the focused risk tests and expect them to pass.

### Task 3: Render compact process reminders

**Files:**
- Modify: `src/requirement_monitor/cards.py:995`
- Modify: `src/requirement_monitor/cards.py:1310`
- Test: `tests/test_cards.py`

- [ ] Add failing card tests for a single footer line containing ordered, deduplicated missing stages without `<at>` tags.
- [ ] Add a helper that renders `流程补充提醒：尚未维护…；如项目涉及，请后续补充。` as the final optional markdown unit.
- [ ] Append the reminder after risks, warnings, blockers, and actions in severe cards and after the normal requirement body in non-severe requirement cards.
- [ ] Mark the reminder unit as low priority so card splitting/size trimming can discard it before risk content.
- [ ] Run `.venv/bin/pytest -q tests/test_cards.py` and expect all tests to pass.

### Task 4: Preserve writeback and notification semantics

**Files:**
- Modify: `tests/test_repository.py`
- Modify: `tests/test_runner.py`

- [ ] Add repository assertions proving process reminders are not written to `风险原因`.
- [ ] Add runner assertions proving reminders do not generate severe notifications or @ mentions by themselves; independently calculated virtual-path schedule risks retain normal notification behavior.
- [ ] Run `.venv/bin/pytest -q tests/test_repository.py tests/test_runner.py` and expect all tests to pass.

### Task 5: Document and verify behavior

**Files:**
- Modify: `README.md`
- Modify: `使用说明.md`

- [ ] Document sparse maintenance, explicit-node gate semantics, authoritative planned completion, virtual schedule fallback, `4/4/3/2/2` defaults, and the process-reminder footer.
- [ ] Run `.venv/bin/pytest -q` and expect the full suite to pass.
- [ ] Run `git diff --check` and expect no whitespace errors.
- [ ] Run `.venv/bin/requirement-monitor run-once --env test --dry-run --force` against the current table and verify missing stages no longer produce severe risks.
- [ ] Do not commit changes unless the user explicitly asks for a commit.
