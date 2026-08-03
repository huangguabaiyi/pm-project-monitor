# Authoritative Planned End and Virtual Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trust explicit planned completion dates while using configured virtual stages to calculate missing current and future schedule time.

**Architecture:** Make planned end optional, distinguish manual events from fallback-duration events, restore synthetic key stages from the current process position, and keep scheduling limited to explicit dates plus AT1/AT2/PV1/PV2/regression durations.

**Tech Stack:** Python 3, Pydantic, pytest, Feishu Bitable.

---

### Task 1: Lock scheduling precedence with tests

- [ ] Add tests proving explicit planned end suppresses minimum-duration findings and controls predicted completion.
- [ ] Add tests proving missing planned end uses stage configuration.
- [ ] Add tests proving absent current/future key stages become virtual duration terms without gate failures.
- [ ] Add tests proving skipped nodes contribute zero duration.

### Task 2: Implement authoritative and virtual scheduling

- [ ] Allow planned completion to be omitted for every node status.
- [ ] Make explicit planned end yield zero fallback duration.
- [ ] Generate enabled virtual AT1/AT2/PV1/PV2/regression stages at or after the current stage.
- [ ] Calculate an empty project through a “项目排期” virtual domain.
- [ ] Keep process reminders separate from findings.

### Task 3: Normalize schedule configuration

- [ ] Keep only AT1, AT2, PV1, PV2 and regression duration fields in schedule configuration and formulas.
- [ ] Change fixed defaults to 4/4/3/2/2 workdays.
- [ ] Remove obsolete schedule fields from the live Bitable schema.

### Task 4: Verify

- [ ] Run focused risk, model, repository and card tests.
- [ ] Run the full suite and `git diff --check`.
- [ ] Run test-environment dry-run and confirm virtual durations appear without missing-gate risk.
- [ ] Send one forced test card and verify zero failed sends.
