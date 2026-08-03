# Missing Schedule Stage Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the exact key stages without confirmed planned completion beneath schedule buffer risks instead of `环节：未标注`.

**Architecture:** The risk engine derives missing-schedule stage references from unfinished phase events and attaches them to domain schedule findings. The card renderer recognizes domain schedule reason codes and labels populated references as `未确定排期`, while preserving the existing `环节` label for unrelated findings.

**Tech Stack:** Python 3, Pydantic, pytest, Feishu interactive cards.

---

### Task 1: Attach missing schedule stages to findings

**Files:**
- Modify: `src/requirement_monitor/risk.py`
- Test: `tests/test_risk.py`

- [ ] **Step 1: Write failing tests for virtual and incomplete node stages**

Add tests proving `schedule.buffer_low` includes ordered `stage_refs` for virtual key stages and for a real key-stage node whose `planned_end` is missing, while excluding explicitly scheduled stages.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_risk.py -k 'buffer_low_exposes_missing_schedule_stages'`

Expected: assertions fail because `schedule.buffer_low.stage_refs` is empty.

- [ ] **Step 3: Implement missing-stage extraction**

Add a helper equivalent to:

```python
def _missing_schedule_stage_refs(phases):
    return [
        _phase_display_name(phase.label)
        for phase in phases
        if phase.unfinished
        and phase.label in {"AT1", "AT2", "PV1", "PV2", "线上回归"}
        and (
            not any(event.node is not None for event in phase.events)
            or any(
                event.node is not None
                and not _node_done(event.node)
                and event.node.planned_end is None
                for event in phase.events
            )
        )
    ]
```

Attach the result to `stage_refs` for low-buffer, negative-buffer, minimum-window and completion-after-merge domain findings.

- [ ] **Step 4: Run focused risk tests and verify GREEN**

Run: `.venv/bin/pytest -q tests/test_risk.py`

Expected: all risk tests pass.

### Task 2: Render the schedule-specific label

**Files:**
- Modify: `src/requirement_monitor/cards.py`
- Test: `tests/test_cards.py`

- [ ] **Step 1: Write a failing card test**

Add a card test with `schedule.buffer_low` and stage references `PV 测试第一轮`, `PV 测试第二轮`, `线上回归`.

Expected card text:

```text
未确定排期：PV 测试第一轮、PV 测试第二轮、线上回归
交付域：平台、客户端、服务端
```

The text must not contain `环节：未标注`.

- [ ] **Step 2: Run the card test and verify RED**

Run: `.venv/bin/pytest -q tests/test_cards.py -k 'missing_schedule_stages'`

Expected: test fails because the renderer still uses `环节：`.

- [ ] **Step 3: Implement reason-code-aware labeling**

Update `_risk_group_element` so schedule reason codes use `未确定排期` when `stage_refs` is populated. All other reason codes retain `环节`.

- [ ] **Step 4: Run card tests and verify GREEN**

Run: `.venv/bin/pytest -q tests/test_cards.py`

Expected: all card tests pass.

### Task 3: Verify runtime output

**Files:**
- No production file changes expected.

- [ ] **Step 1: Run focused suites**

Run: `.venv/bin/pytest -q tests/test_risk.py tests/test_risk_grouping.py tests/test_cards.py`

- [ ] **Step 2: Run full verification**

Run: `.venv/bin/pytest -q && git diff --check`

- [ ] **Step 3: Inspect real card payload**

Run: `.venv/bin/requirement-monitor run-once --env test --force --dry-run`

Verify that schedule buffer findings list exact missing stages and no longer show `环节：未标注`.

- [ ] **Step 4: Send one test card**

Run: `.venv/bin/requirement-monitor run-once --env test --force`

Verify the latest log contains `sent_cards=1` and `failed_sends=0`.
