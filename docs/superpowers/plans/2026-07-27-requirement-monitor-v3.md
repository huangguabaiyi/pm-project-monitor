# Requirement Monitor v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic Feishu URL extraction, isolated test/prod Webhooks with a `main` branch production gate, and readable risk grouping by reason, stage, and delivery domain.

**Architecture:** Keep environment selection at the configuration/CLI boundary, URL normalization at the repository boundary, and risk grouping as a pure model transformation before card rendering. The runner continues to send resolved payloads through the existing Webhook sender; LLM behavior remains optional and cannot affect these guarantees.

**Tech Stack:** Python 3.9+, Pydantic 2, argparse, subprocess/Git CLI, httpx, pytest, Feishu Bitable CLI/Webhook APIs.

**Execution note:** Do not create Git commits unless the user explicitly requests them. Use passing test checkpoints instead.

---

## File Structure

- Create `src/requirement_monitor/runtime_environment.py`: runtime environment enum, Git branch lookup, and production safety validation.
- Create `src/requirement_monitor/risk_grouping.py`: pure risk finding grouping and stable display ordering.
- Modify `src/requirement_monitor/config.py`: resolve `test`/`prod`, select the correct Webhook, retain legacy test-only compatibility.
- Modify `src/requirement_monitor/cli.py`: add `--env`, call the production branch gate, and persist the selected environment in runtime snapshots.
- Modify `src/requirement_monitor/models.py`: add `RiskFinding` and `RiskGroup`, attach findings to node and requirement risks.
- Modify `src/requirement_monitor/repository.py`: extract true URLs from Feishu object/list shapes.
- Modify `src/requirement_monitor/risk.py`: emit structured findings at the point where deterministic reasons are created.
- Modify `src/requirement_monitor/cards.py`: render grouped reasons and split oversized risk sections without silent truncation.
- Modify `src/requirement_monitor/runner.py`: support multiple continuation payloads produced by daily/severe card builders.
- Modify `README.md`, `使用说明.md`, and `config.example.json`: document environment selection, secrets, and card behavior.
- Create `tests/test_runtime_environment.py`: branch guard tests.
- Create `tests/test_risk_grouping.py`: grouping and order tests.
- Modify `tests/test_config.py`, `tests/test_cli.py`, `tests/test_repository.py`, `tests/test_risk.py`, `tests/test_cards.py`, and `tests/test_runner.py`.

---

### Task 1: Resolve Test and Production Webhook Configuration

**Files:**
- Modify: `src/requirement_monitor/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Add failing environment selection tests**

Add tests that clear all `REQUIREMENT_MONITOR_*WEBHOOK_URL` variables, then cover:

```python
def test_default_environment_uses_test_webhook(tmp_path, monkeypatch):
    config_path = write_config_without_webhook(tmp_path)
    monkeypatch.setenv(
        "REQUIREMENT_MONITOR_TEST_WEBHOOK_URL", VALID_WEBHOOK_URL
    )

    settings = load_settings(config_path)

    assert settings.runtime_environment == "test"
    assert settings.webhook_url.get_secret_value() == VALID_WEBHOOK_URL


def test_prod_environment_requires_explicit_prod_webhook(tmp_path, monkeypatch):
    config_path = write_config_without_webhook(tmp_path)
    monkeypatch.setenv("REQUIREMENT_MONITOR_ENV", "prod")
    monkeypatch.setenv("REQUIREMENT_MONITOR_WEBHOOK_URL", VALID_WEBHOOK_URL)

    with pytest.raises(ConfigError, match="production Webhook URL is missing"):
        load_settings(config_path)
```

Also test command override precedence with `load_settings(config_path, runtime_environment="prod")`, invalid environment values, and production/test URL validation without printing secrets.

- [ ] **Step 2: Run the targeted tests and confirm failure**

Run:

```bash
.venv/bin/pytest -q tests/test_config.py -k 'environment or webhook'
```

Expected: FAIL because `Settings.runtime_environment` and the new environment variables do not exist.

- [ ] **Step 3: Implement deterministic Webhook resolution**

Add a runtime environment field to `Settings`:

```python
RuntimeEnvironment = Literal["test", "prod"]


class Settings(BaseModel):
    runtime_environment: RuntimeEnvironment = "test"
    webhook_url: Optional[SecretStr] = None
```

Change `load_settings` to accept an optional command override:

```python
def load_settings(
    path: Optional[Path] = None,
    *,
    require_webhook: bool = True,
    runtime_environment: Optional[str] = None,
) -> Settings:
```

Resolve the selected URL with these rules:

```python
def _resolve_webhook_url(
    config_data: Dict[str, Any], runtime_environment: str
) -> Optional[str]:
    if runtime_environment == "prod":
        return os.getenv("REQUIREMENT_MONITOR_PROD_WEBHOOK_URL")
    return (
        os.getenv("REQUIREMENT_MONITOR_TEST_WEBHOOK_URL")
        or os.getenv("REQUIREMENT_MONITOR_WEBHOOK_URL")
        or config_data.get("webhook_url")
    )
```

Never copy the unselected Webhook into `Settings`. Keep existing official endpoint validation on the resolved `webhook_url`.

- [ ] **Step 4: Run all configuration tests**

Run:

```bash
.venv/bin/pytest -q tests/test_config.py
```

Expected: all configuration tests pass and no captured output contains Webhook tokens.

---

### Task 2: Add the Production `main` Branch Safety Gate

**Files:**
- Create: `src/requirement_monitor/runtime_environment.py`
- Create: `tests/test_runtime_environment.py`
- Modify: `src/requirement_monitor/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing pure branch validation tests**

Create tests for exact `main`, feature branches, detached HEAD, and Git command failure:

```python
def test_prod_requires_exact_main_branch():
    validate_runtime_environment("prod", branch="main")


@pytest.mark.parametrize("branch", ["feature/x", "develop", "MAIN", None])
def test_prod_rejects_non_main_branch(branch):
    with pytest.raises(RuntimeEnvironmentError, match="main"):
        validate_runtime_environment("prod", branch=branch)


def test_test_environment_accepts_any_branch():
    validate_runtime_environment("test", branch="feature/x")
```

- [ ] **Step 2: Run tests and verify the module is missing**

Run:

```bash
.venv/bin/pytest -q tests/test_runtime_environment.py
```

Expected: FAIL because `runtime_environment.py` is not implemented.

- [ ] **Step 3: Implement branch lookup and validation**

Implement a small isolated module:

```python
class RuntimeEnvironmentError(ValueError):
    pass


def current_git_branch(
    repository: Path,
    *,
    command_runner=subprocess.run,
) -> Optional[str]:
    result = command_runner(
        ["git", "-C", str(repository), "symbolic-ref", "--short", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def validate_runtime_environment(environment: str, *, branch: Optional[str]) -> None:
    if environment == "prod" and branch != "main":
        raise RuntimeEnvironmentError(
            "Production Webhook is only available from the main branch."
        )
```

Do not include repository paths, Webhook tokens, or raw Git stderr in the public error.

- [ ] **Step 4: Add `--env` to operational commands**

Add `choices=("test", "prod")` to `run-once`, `start`, and `restart`. Pass the selected value into `load_settings`. Before constructing repositories, runners, runtime snapshots, or Webhook clients, validate the branch for `prod`.

The existing `status`, `logs`, `stop`, `version`, and `init-table` commands remain environment-independent.

- [ ] **Step 5: Persist the selected environment**

Add this field to `_runtime_config_payload`:

```python
"runtime_environment": settings.runtime_environment,
```

The runtime snapshot contains only the selected resolved `webhook_url`, never both test and production URLs.

- [ ] **Step 6: Verify CLI and runtime snapshot behavior**

Run:

```bash
.venv/bin/pytest -q tests/test_runtime_environment.py tests/test_cli.py
```

Expected: all tests pass; feature branch prod commands return configuration exit code `2` before any send or write side effect.

---

### Task 3: Extract Real URLs from Feishu Field Objects

**Files:**
- Modify: `src/requirement_monitor/repository.py`
- Test: `tests/test_repository.py`

- [ ] **Step 1: Add failing URL shape tests**

Add focused tests for all supported input shapes:

```python
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"text": "需求文档", "link": "https://mi.feishu.cn/docx/abc"},
         "https://mi.feishu.cn/docx/abc"),
        ({"name": "Meego", "url": "https://meego.example/123"},
         "https://meego.example/123"),
        ([{"title": "翻译", "href": "https://translate.example/1"}],
         "https://translate.example/1"),
        ({"text": "只有文档名称"}, None),
        ("https://docs.example/req", "https://docs.example/req"),
    ],
)
def test_optional_url_extracts_real_link_without_using_display_text(value, expected):
    assert _optional_url(value, "需求文档链接") == expected
```

Add a separate test asserting a raw non-empty string such as `"错误链接"` raises `_FieldParseError` and isolates only the affected requirement.

- [ ] **Step 2: Run the repository tests and confirm failure**

Run:

```bash
.venv/bin/pytest -q tests/test_repository.py -k 'url or link'
```

Expected: FAIL because `_optional_url` currently reads `text` before real link keys.

- [ ] **Step 3: Implement recursive candidate extraction**

Add a helper that distinguishes display-only mappings from raw invalid strings:

```python
_URL_KEYS = ("link", "url", "href")


def _real_url_candidate(value: Any) -> Tuple[Optional[str], bool]:
    if value is None:
        return None, False
    if isinstance(value, str):
        return value.strip() or None, True
    if isinstance(value, Mapping):
        for key in _URL_KEYS:
            candidate, supplied = _real_url_candidate(value.get(key))
            if candidate:
                return candidate, supplied
        raw_value = value.get("value")
        if isinstance(raw_value, str) and raw_value.strip().startswith(("http://", "https://")):
            return raw_value.strip(), True
        return None, False
    if isinstance(value, (list, tuple)):
        for item in value:
            candidate, supplied = _real_url_candidate(item)
            if candidate:
                return candidate, supplied
        return None, False
    return None, False
```

`_optional_url` returns `None` for display-only objects, but validates and rejects supplied raw candidates that are not valid HTTP/HTTPS URLs.

- [ ] **Step 4: Run the complete repository test suite**

Run:

```bash
.venv/bin/pytest -q tests/test_repository.py
```

Expected: all repository tests pass, including blank edit-row handling.

---

### Task 4: Introduce Structured Risk Findings

**Files:**
- Modify: `src/requirement_monitor/models.py`
- Modify: `src/requirement_monitor/risk.py`
- Test: `tests/test_models.py`
- Test: `tests/test_risk.py`

- [ ] **Step 1: Add failing model tests**

Define the expected structured model contract in tests:

```python
def test_risk_finding_deduplicates_stage_and_domain_references():
    finding = RiskFinding(
        reason_code="test_gate.at_incomplete",
        reason_text="AT 测试未通过，不能进入 PV",
        stage_refs=["AT 测试第一轮", "AT 测试第一轮"],
        domain_refs=["客户端", "客户端", "车辆"],
        level=RiskLevel.SEVERE,
        source="test_gate",
    )

    assert finding.stage_refs == ["AT 测试第一轮"]
    assert finding.domain_refs == ["客户端", "车辆"]
```

Add `findings=[]` defaults to `NodeRisk` and `RequirementRisk` so legacy test fixtures remain valid.

- [ ] **Step 2: Add failing risk-source tests**

Cover at least these deterministic categories:

- node test duration below minimum;
- node delay consumes buffer;
- missing test schedule;
- AT incomplete blocks PV;
- PV incomplete blocks launch/merge;
- missing online regression;
- domain completion later than merge;
- blocker overdue;
- server launch rule failure;
- translation incomplete warning.

Each test asserts `reason_code`, readable text, stage references, domain references, and level.

- [ ] **Step 3: Run model and risk tests to verify failure**

Run:

```bash
.venv/bin/pytest -q tests/test_models.py tests/test_risk.py -k 'finding or gate or duration or regression'
```

Expected: FAIL because structured findings do not exist.

- [ ] **Step 4: Implement `RiskFinding`**

Add the model:

```python
class RiskFinding(BaseModel):
    reason_code: NonEmptyStr
    reason_text: NonEmptyStr
    stage_refs: List[NonEmptyStr] = Field(default_factory=list)
    domain_refs: List[NonEmptyStr] = Field(default_factory=list)
    level: RiskLevel
    source: NonEmptyStr

    @model_validator(mode="after")
    def deduplicate_references(self):
        self.stage_refs = list(dict.fromkeys(self.stage_refs))
        self.domain_refs = list(dict.fromkeys(self.domain_refs))
        return self
```

Attach `findings: List[RiskFinding] = Field(default_factory=list)` to `NodeRisk` and `RequirementRisk`.

- [ ] **Step 5: Emit findings where reasons originate**

Use stable codes instead of parsing final card text. Examples:

```python
RiskFinding(
    reason_code="test.duration_below_minimum",
    reason_text="测试周期低于项目最低要求",
    stage_refs=[event.stage_name],
    domain_refs=[node.domain],
    level=RiskLevel.SEVERE,
    source="node_schedule",
)
```

```python
RiskFinding(
    reason_code="test_gate.at_incomplete",
    reason_text="AT 测试未通过，不能进入 PV",
    stage_refs=[failed_stage],
    domain_refs=[domain],
    level=RiskLevel.SEVERE,
    source="test_gate",
)
```

Continue populating legacy `reasons` from `finding.reason_text` or existing text so notification records and system field writes remain backward compatible.

- [ ] **Step 6: Run full model and risk tests**

Run:

```bash
.venv/bin/pytest -q tests/test_models.py tests/test_risk.py
```

Expected: all tests pass and every existing deterministic risk result remains unchanged in level and timing.

---

### Task 5: Group Findings by Reason, Stage, and Domain

**Files:**
- Create: `src/requirement_monitor/risk_grouping.py`
- Create: `tests/test_risk_grouping.py`
- Modify: `src/requirement_monitor/models.py`

- [ ] **Step 1: Write failing grouping tests**

Add tests for identical reason codes across stages/domains:

```python
def test_groups_same_reason_across_stages_and_domains():
    groups = group_risk_findings(
        [
            finding("test_gate.at_incomplete", "AT 测试未通过，不能进入 PV",
                    "AT 测试第一轮", "客户端"),
            finding("test_gate.at_incomplete", "AT 测试未通过，不能进入 PV",
                    "AT 测试第二轮", "车辆"),
        ],
        stage_order={"AT 测试第一轮": 1, "AT 测试第二轮": 2},
    )

    assert len(groups) == 1
    assert groups[0].stage_refs == ["AT 测试第一轮", "AT 测试第二轮"]
    assert groups[0].domain_refs == ["客户端", "车辆"]
```

Also test that different codes remain separate, unknown legacy text uses exact text as a fallback key, severe groups sort before warnings, and stage references follow configured process order.

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```bash
.venv/bin/pytest -q tests/test_risk_grouping.py
```

Expected: FAIL because the grouping module and `RiskGroup` model do not exist.

- [ ] **Step 3: Implement `RiskGroup` and pure grouping**

Add:

```python
class RiskGroup(BaseModel):
    reason_code: NonEmptyStr
    reason_text: NonEmptyStr
    stage_refs: List[NonEmptyStr] = Field(default_factory=list)
    domain_refs: List[NonEmptyStr] = Field(default_factory=list)
    level: RiskLevel
```

Group by `reason_code`, merge references with stable de-duplication, retain the highest level, and sort stages with the configured process order. For compatibility findings without a code, use `legacy:<exact text>`.

- [ ] **Step 4: Run grouping tests**

Run:

```bash
.venv/bin/pytest -q tests/test_risk_grouping.py
```

Expected: all grouping tests pass with deterministic ordering.

---

### Task 6: Render Readable Grouped Risks and Continuation Cards

**Files:**
- Modify: `src/requirement_monitor/cards.py`
- Modify: `src/requirement_monitor/runner.py`
- Test: `tests/test_cards.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Add failing card layout tests**

Assert grouped reasons are rendered as independent blocks:

```python
def test_severe_card_groups_same_reason_and_lists_all_stages_and_domains(risk):
    payloads = build_severe_cards(risk)
    rendered = json.dumps(payloads, ensure_ascii=False)

    assert rendered.count("AT 测试未通过，不能进入 PV") == 1
    assert "AT 测试第一轮、AT 测试第二轮" in rendered
    assert "客户端、车辆" in rendered
```

Add tests proving the old comma-joined `**风险原因**：...` block is absent, links still use real URLs, and all `<at>` tags remain intact.

- [ ] **Step 2: Add failing continuation tests**

Construct enough unique groups to exceed one card and assert:

```python
payloads = build_severe_cards(large_risk)
assert len(payloads) > 1
assert "第 1/" in json.dumps(payloads[0], ensure_ascii=False)
assert every_reason_appears_exactly_once(payloads, expected_reasons)
```

Daily cards require the same no-loss behavior for grouped risks.

- [ ] **Step 3: Run card tests and confirm failure**

Run:

```bash
.venv/bin/pytest -q tests/test_cards.py -k 'group or continuation or link'
```

Expected: FAIL because the builders return one payload and join raw reasons.

- [ ] **Step 4: Implement reusable risk group elements**

Add a renderer:

```python
def _risk_group_element(index: int, group: RiskGroup) -> Dict[str, object]:
    lines = [f"**{index}. {escape_value(group.reason_text, 300)}**"]
    if group.stage_refs:
        lines.append("环节：{}".format("、".join(group.stage_refs)))
    if group.domain_refs:
        lines.append("交付域：{}".format("、".join(group.domain_refs)))
    return _markdown_element("\n".join(lines))
```

Daily and severe builders call `group_risk_findings` and use these blocks instead of `_join_values(risk.reasons)`.

- [ ] **Step 5: Return payload lists and split without truncation**

Introduce plural builders:

```python
def build_daily_cards(report: RunReport) -> List[Dict[str, object]]:
    ...


def build_severe_cards(risk: RequirementRisk) -> List[Dict[str, object]]:
    ...
```

Pack whole risk group elements until adding another would exceed `_MAX_ELEMENTS` or `_MAX_PAYLOAD_BYTES`, then start a continuation card. Add `第 N/M 部分` to titles after the final part count is known. Never split a single risk group across cards and never silently discard a group.

Retain singular wrapper functions only if existing external tests/imports require backward compatibility; wrappers may return the first payload only for non-oversized fixtures, while the runner must use plural builders.

- [ ] **Step 6: Update the runner to send all parts**

Replace singular payload creation with loops over `build_daily_cards` and `build_severe_cards`. Each part has its own outbound payload but shares the same business notification fingerprint plus a deterministic part suffix, so failed parts can be retried without suppressing successful parts.

- [ ] **Step 7: Run card and runner tests**

Run:

```bash
.venv/bin/pytest -q tests/test_cards.py tests/test_runner.py
```

Expected: all tests pass; compact/text Webhook degradation remains supported for each continuation payload.

---

### Task 7: Update Documentation and Configuration Examples

**Files:**
- Modify: `README.md`
- Modify: `使用说明.md`
- Modify: `config.example.json`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Update setup commands**

Document test usage without storing secrets:

```bash
export REQUIREMENT_MONITOR_ENV=test
export REQUIREMENT_MONITOR_TEST_WEBHOOK_URL='https://open.feishu.cn/open-apis/bot/v2/hook/...'
export REQUIREMENT_MONITOR_BOT_KEYWORD='需求进展推送'
.venv/bin/requirement-monitor run-once --env test
```

Document production usage separately and state that it only runs from `main`:

```bash
export REQUIREMENT_MONITOR_ENV=prod
export REQUIREMENT_MONITOR_PROD_WEBHOOK_URL='https://open.feishu.cn/open-apis/bot/v2/hook/...'
.venv/bin/requirement-monitor restart --env prod
```

- [ ] **Step 2: Update link field instructions**

Explain that users may paste Feishu document links normally; the program reads the true link behind the displayed document title. Display-only values are treated as empty, while raw invalid strings isolate the requirement.

- [ ] **Step 3: Update card examples**

Show grouped risk blocks with reason, stages, and delivery domains. Remove examples that display one long comma-separated risk line.

- [ ] **Step 4: Verify help and docs do not expose secrets**

Run:

```bash
.venv/bin/requirement-monitor run-once --help
.venv/bin/requirement-monitor start --help
rg -n '2cabe008|PROD_WEBHOOK_URL=.+' README.md 使用说明.md config.example.json
```

Expected: help lists `--env {test,prod}` and no real Webhook token appears in tracked documentation.

---

### Task 8: Full Verification and Live Test-Environment Smoke Test

**Files:**
- Test: entire repository
- Read/write only through existing test Webhook and current test Bitable configuration

- [ ] **Step 1: Run formatting/static sanity checks already supported by the project**

Run:

```bash
.venv/bin/python -m compileall -q src tests
git diff --check
```

Expected: no syntax, conflict marker, or whitespace errors.

- [ ] **Step 2: Run the complete unit suite**

Run:

```bash
.venv/bin/pytest -q
```

Expected: all tests pass; integration tests requiring explicit live flags remain skipped.

- [ ] **Step 3: Run a live Bitable dry-run in test mode**

Run with the existing local test configuration:

```bash
test -n "${REQUIREMENT_MONITOR_TEST_WEBHOOK_URL:-}" || {
  echo 'Set REQUIREMENT_MONITOR_TEST_WEBHOOK_URL in the current shell first.' >&2
  exit 1
}
REQUIREMENT_MONITOR_ENV=test \
REQUIREMENT_MONITOR_BOT_KEYWORD='需求进展推送' \
.venv/bin/requirement-monitor run-once --env test --dry-run
```

Expected:

- exit code `0`;
- blank editing rows produce local `WARNING` only;
- display-only Feishu link values do not create URL errors;
- grouped risks include all affected stages and domains;
- no Webhook request is made.

- [ ] **Step 4: Run one real test Webhook smoke test**

Use only `REQUIREMENT_MONITOR_TEST_WEBHOOK_URL`; never provide the production URL during development:

```bash
test -n "${REQUIREMENT_MONITOR_TEST_WEBHOOK_URL:-}" || {
  echo 'Set REQUIREMENT_MONITOR_TEST_WEBHOOK_URL in the current shell first.' >&2
  exit 1
}
REQUIREMENT_MONITOR_ENV=test \
REQUIREMENT_MONITOR_BOT_KEYWORD='需求进展推送' \
.venv/bin/requirement-monitor run-once --env test
```

Expected: all generated cards report successful delivery and the Feishu test group receives readable grouped risk cards.

- [ ] **Step 5: Verify the production gate without sending**

While still on `feature/requirement-monitor`, reuse the test Webhook value only as a syntactically valid production configuration input. The branch gate must reject the command before any Webhook client is constructed:

```bash
test -n "${REQUIREMENT_MONITOR_TEST_WEBHOOK_URL:-}" || {
  echo 'Set REQUIREMENT_MONITOR_TEST_WEBHOOK_URL in the current shell first.' >&2
  exit 1
}
REQUIREMENT_MONITOR_PROD_WEBHOOK_URL="$REQUIREMENT_MONITOR_TEST_WEBHOOK_URL" \
.venv/bin/requirement-monitor run-once --env prod --dry-run
```

Expected: exit code `2`, a sanitized `main` branch safety error, no notification record, no system-field write, and no Webhook request.

- [ ] **Step 6: Audit completion against the design spec**

Confirm every requirement in `docs/superpowers/specs/2026-07-27-requirement-monitor-v3-design.md` has direct evidence from code, tests, dry-run output, or the test Webhook result. Record any unrelated pre-existing failures separately instead of changing unrelated behavior.
