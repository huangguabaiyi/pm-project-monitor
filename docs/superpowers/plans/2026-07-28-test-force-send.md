# Test Force Send Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a test-only `run-once --force` option that resends the current daily and severe-risk cards without deleting local state.

**Architecture:** The CLI owns environment safety and rejects `prod + force` before constructing a sender. The runner receives a default-false `force` flag and bypasses only daily/severe fingerprint checks while retaining eligibility, validation, sending, write-back, and state persistence.

**Tech Stack:** Python 3.9, argparse, Pydantic, pytest.

---

### Task 1: Runner force semantics

**Files:**
- Modify: `src/requirement_monitor/runner.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Write the failing runner test**

Add a test beside the existing manual deduplication tests:

```python
def test_manual_force_resends_daily_and_severe_cards():
    dependencies = FakeDependencies(level=RiskLevel.SEVERE)

    first = dependencies.runner().run(trigger="manual")
    duplicate = dependencies.runner().run(trigger="manual")
    forced = dependencies.runner().run(trigger="manual", force=True)

    assert first.sent_cards == 2
    assert duplicate.sent_cards == 0
    assert forced.sent_cards == 2
    assert forced.severe_cards == 1
    assert len(dependencies.webhook.payloads) == 4
```

- [ ] **Step 2: Verify the test fails**

Run:

```bash
.venv/bin/pytest tests/test_runner.py::test_manual_force_resends_daily_and_severe_cards -q
```

Expected: failure because `MonitorRunner.run` does not accept `force`.

- [ ] **Step 3: Implement force propagation**

Change the runner entry points to default `force=False`:

```python
def run(self, trigger: str, dry_run: bool = False, force: bool = False):
    ...

def _run_locked(self, trigger, dry_run, started_at, force=False):
    ...
```

Pass `force` into `_build_notifications`. For project daily cards, skip `_daily_part_already_succeeded` only when `force` is false. For severe cards, skip `state.active_fingerprints` only when `force` is false. Do not change data-error behavior or write-back/state behavior.

- [ ] **Step 4: Verify runner tests pass**

Run:

```bash
.venv/bin/pytest tests/test_runner.py::test_manual_force_resends_daily_and_severe_cards tests/test_runner.py::test_manual_does_not_resend_successful_daily_card_per_project tests/test_runner.py::test_severe_fingerprint_is_deduplicated_until_resolution_or_change -q
```

Expected: all pass.

### Task 2: CLI flag and production guard

**Files:**
- Modify: `src/requirement_monitor/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Add tests proving:

```python
def test_run_once_force_is_forwarded_to_runner():
    ...
    assert runner.calls == [{"trigger": "manual", "dry_run": False, "force": True}]

def test_force_is_rejected_for_prod_before_runner_creation():
    ...
    assert exit_code == EXIT_CONFIG
    assert runner_factory.calls == []
```

Use the existing fake settings and runner patterns in `tests/test_cli.py`; invoke `main(["run-once", "--env", "test", "--force"], ...)` and `main(["run-once", "--env", "prod", "--force"], ...)`.

- [ ] **Step 2: Verify CLI tests fail**

Run the two new test node IDs with pytest. Expected: argparse rejects `--force` or the runner does not receive it.

- [ ] **Step 3: Implement parser and guard**

Add only to the `run-once` parser:

```python
run_once_parser.add_argument("--force", action="store_true")
```

After settings are loaded and before `_run_monitor`, reject force outside test:

```python
if args.command == "run-once" and args.force and settings.runtime_environment != "test":
    raise RuntimeEnvironmentError("--force is only available in test runtime environment.")
```

Extend `_run_monitor(..., force=False)` and call:

```python
report = runner.run(trigger=trigger, dry_run=dry_run, force=force)
```

Pass `args.force` only from the `run-once` branch. Scheduled/start/restart paths retain the default false value.

- [ ] **Step 4: Verify CLI tests pass**

Run the new tests plus existing runtime-environment and dry-run CLI tests. Expected: all pass and prod sender construction is not attempted.

### Task 3: Usage documentation

**Files:**
- Modify: `README.md`
- Modify: `使用说明.md`

- [ ] **Step 1: Document normal and forced test runs**

Add:

```bash
.venv/bin/requirement-monitor run-once --env test
.venv/bin/requirement-monitor run-once --env test --force
.venv/bin/requirement-monitor run-once --env test --force --dry-run
```

Explain that normal runs deduplicate, force is test-only, and deleting Feishu notification records does not clear local `.state/monitor.json`.

- [ ] **Step 2: Verify help and documentation**

Run:

```bash
.venv/bin/requirement-monitor run-once --help
rg -n -- "--force|本机状态|测试环境" README.md 使用说明.md
```

Expected: help lists `--force`; docs state its restrictions.

### Task 4: Full verification and live test

**Files:**
- Verify only; no new files required.

- [ ] **Step 1: Run the complete automated suite**

```bash
.venv/bin/pytest -q
.venv/bin/python -m compileall -q src
git diff --check
```

Expected: all tests pass, compile succeeds, no whitespace errors.

- [ ] **Step 2: Verify production rejection**

```bash
.venv/bin/requirement-monitor run-once --env prod --force
```

Expected: configuration/runtime-environment error before any production Webhook call. The configured production Webhook is currently empty, so automated tests provide the authoritative sender-construction assertion.

- [ ] **Step 3: Send a real forced test card**

```bash
.venv/bin/requirement-monitor run-once --env test --force
.venv/bin/requirement-monitor logs
```

Expected: latest run has `sent_cards >= 1`, `failed_sends = 0`, and the test Webhook receives the card even if the normal command was deduplicated earlier the same day.

No Git commit is created unless the user explicitly requests one.
