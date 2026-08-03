# Requirement Progress Monitor V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不依赖 LLM 的情况下读取飞书多维表格，按当前环节和测试门禁计算需求风险，并通过结构化飞书卡片稳定推送多人负责人、DDL、链接和风险信息。

**Architecture:** 保留现有 `repository → risk → runner → cards → webhook` 管线，采用兼容读取和局部替换方式完成数据模型升级。确定性规则负责筛选、日期计算、AT/PV/线上回归门禁和严重等级；LLM 只负责可选的摘要与行动建议，失败时继续使用确定性结果发送卡片。所有日期在内部按 `Asia/Shanghai` 自然日处理，卡片只展示当前环节及其并行交付域。

**Tech Stack:** Python 3.9、Pydantic 2、HTTPX、pytest、pytest-httpx、现有 `feishu` CLI、飞书 Bitable Webhook、macOS `launchd`。

---

## File Structure

按职责修改以下文件，不创建新的服务端或浏览器自动化组件：

- `src/requirement_monitor/models.py` — 需求链接、多人负责人、可空跳过节点、五个关键时长覆盖字段。
- `src/requirement_monitor/schema.py` — 表结构、字段迁移、基础配置环节种子、多人用户字段配置。
- `src/requirement_monitor/repository.py` — 兼容旧字段读取、多人用户解析、空行 warning、日期和 URL 校验、系统字段回写。
- `src/requirement_monitor/fixed_rules.py` — 只读“固定业务规则”文件，并按 AT1/AT2/PV1/PV2/线上回归分别解析默认天数。
- `src/requirement_monitor/risk.py` — 当前环节校验、测试域识别、测试门禁、自然日 DDL 和风险等级。
- `src/requirement_monitor/cards.py` — 带表头的结构化卡片、真实 @、链接按钮、当前环节矩阵和精简严重风险卡片。
- `src/requirement_monitor/runner.py` — 通知对象、需求过滤、风险写回、LLM 降级和单需求隔离编排。
- `src/requirement_monitor/webhook.py` — 保持卡片校验、重试和纯文本降级，并补充结构化卡片兼容验证。
- `README.md`、`使用说明.md` — 更新字段填写、配置继承、跳过节点、手动测试和失败排查说明。
- `tests/test_models.py`、`tests/test_schema.py`、`tests/test_repository.py`、`tests/test_fixed_rules.py`、`tests/test_risk.py`、`tests/test_cards.py`、`tests/test_runner.py`、`tests/test_webhook.py` — 逐层补充回归测试。

执行期间不回滚工作树中已有的业务改动，不修改真实 Webhook、不打开浏览器、不提交安全词或 Token。

## Task 1: Upgrade Domain Models and Schema Manifest

**Files:**
- Modify: `src/requirement_monitor/models.py:59-155`
- Modify: `src/requirement_monitor/schema.py:1-215`
- Test: `tests/test_models.py:1-340`
- Test: `tests/test_schema.py:1-510`

- [ ] **Step 1: Write failing model tests**

新增测试覆盖以下契约：

```python
def test_requirement_accepts_optional_links_and_duplicate_display_inputs():
    requirement = make_requirement(
        requirement_links={
            "需求文档链接": "https://docs.example/req",
            "Meego链接": "https://meego.example/123",
            "多语言翻译链接": "https://translate.example/123",
        }
    )
    assert requirement.okr_target == "2026年Q2 KR3"
    assert requirement.requirement_doc_url.startswith("https://")


def test_skipped_delivery_node_may_omit_planned_end():
    node = make_node(status=NodeStatus.SKIPPED, planned_end=None)
    assert node.planned_end is None


def test_active_delivery_node_still_requires_planned_end():
    with pytest.raises(ValidationError, match="计划完成时间"):
        make_node(status=NodeStatus.IN_PROGRESS, planned_end=None)


def test_delivery_node_preserves_all_people():
    node = make_node(owners=[Person(open_id="ou-a", name="甲"), Person(open_id="ou-b", name="乙")])
    assert [person.open_id for person in node.owners] == ["ou-a", "ou-b"]
```

保留单人旧构造参数的兼容入口，最终领域模型统一以 `owners: List[Person]` 工作；`owner_id`、`owner_name` 只作为兼容读取或显示第一个负责人的过渡属性。

- [ ] **Step 2: Run focused model tests and observe failures**

Run: `cd /Users/mi/Documents/VScode项目/工作小工具/需求进展提醒机器人/.worktrees/requirement-monitor && python3 -m pytest tests/test_models.py -q`

Expected: 新增链接、多人负责人和跳过节点测试失败，旧测试仍能定位到当前单人字段依赖。

- [ ] **Step 3: Implement model changes**

在 `Requirement` 中增加以下字段，空值合法：

```python
okr_target: NonEmptyStr
requirement_doc_url: Optional[NonEmptyStr] = None
meego_url: Optional[NonEmptyStr] = None
translation_url: Optional[NonEmptyStr] = None
```

将 `DeliveryNode` 改为：

```python
owners: List[Person] = Field(min_length=1)
planned_end: Optional[AwareDatetime] = None
```

模型校验规则为：只有 `status != NodeStatus.SKIPPED` 且不是兼容的已完成系统节点时，才要求 `planned_end`；若 `planned_start` 和 `planned_end` 都存在，仍要求开始不晚于结束。新增 `ProjectConfig` 字段 `at1_days`、`at2_days`、`pv1_days`、`pv2_days`、`regression_days`，旧汇总和专项字段暂时保留读取属性，但不再参与新版计算。

- [ ] **Step 4: Update schema manifest and migration operations**

在 `需求主表` 中将用户字段名改为 `OKR目标`，保留 `目标版本`，新增三个可选文本 URL 字段；读取器仍识别旧物理字段 `项目名称`。扩展 `FieldSpec` 增加可选 `property`，并让 `build_schema_plan` 将其传入 `create_field`；进展节点表的 `负责人` 使用用户字段属性 `{ "multiple": true }`，已有单用户字段不强制破坏性重建，读取层先兼容单人，用户可在飞书中手动调整为多选。在 `进展节点表` 允许计划完成时间由业务校验决定是否必填。将项目配置表的有效字段收敛为五个可空关键时长字段；基础配置种子顺序固定为：

```python
DEFAULT_PROCESS_NODES = (
    "需求撰写", "内部评审", "产品需求评审", "设计稿输出", "设计宣讲",
    "需求宣讲", "工作量评估排期", "各端开发", "联调", "提测",
    "AT 测试第一轮", "AT 测试第二轮", "PV 测试第一轮", "PV 测试第二轮",
    "服务端上线", "线上回归", "多语言翻译", "版本合入",
)
```

迁移时把旧“上线 Checklist”识别为“服务端上线”的兼容别名，避免重复节点和重复风险。

- [ ] **Step 5: Run schema and model regression tests**

Run: `python3 -m pytest tests/test_models.py tests/test_schema.py -q`

Expected: PASS；同时确认旧单人负责人、旧“项目名称”和旧配置记录仍能被兼容读取。

## Task 2: Normalize Repository Parsing and Natural-Day Semantics

**Files:**
- Modify: `src/requirement_monitor/repository.py:58-245,598-1075`
- Modify: `src/requirement_monitor/calendar.py:1-180`
- Test: `tests/test_repository.py:122-1100`
- Test: `tests/test_calendar.py:1-220`

- [ ] **Step 1: Add failing parser tests for new table values**

覆盖：空白行只产生 `WARNING` 日志并跳过；URL 空值合法；非法 URL 只隔离当前需求；单人和多人负责人均可读取；跳过 AT/PV 节点无计划完成时间；非跳过节点无计划完成时间仍生成字段级错误；日期只填写日期时转换为 `Asia/Shanghai` 当日 `00:00:00`。

```python
def test_blank_rows_are_skipped_with_warning(raw_tables, caplog):
    snapshot, issues = parse_snapshot(raw_tables)
    assert not any(issue.record_id is None for issue in issues)
    assert "blank" in caplog.text.lower()


def test_multiple_node_owners_are_preserved(raw_tables):
    snapshot, issues = parse_snapshot(raw_tables_with_two_owners(raw_tables))
    assert not issues
    assert [person.name for person in snapshot.nodes[0].owners] == ["甲", "乙"]


def test_skipped_node_without_dates_is_valid(raw_tables):
    snapshot, issues = parse_snapshot(raw_tables_with_skipped_node_without_dates(raw_tables))
    assert not issues
    assert snapshot.nodes[0].planned_end is None
```

- [ ] **Step 2: Implement tolerant field helpers**

在 `repository.py` 增加 `_people(value, field_name, required=True)`，接受飞书多用户字段列表，返回去重后的 `List[Person]`；旧单人值也包装为长度为 1 的列表。缺少负责人或人员没有可解析身份时抛出 `_FieldParseError`，由现有需求/记录隔离逻辑处理。

增加 `_optional_url(value, field_name)`：空值返回 `None`，非空值仅接受 `http://` 或 `https://`，否则抛出字段级解析错误。需求解析同时读取 `OKR目标` 和旧字段 `项目名称`，同一条记录优先使用非空的新字段。

- [ ] **Step 3: Implement node-specific date validation**

`_parse_node` 先解析状态，再允许已跳过的 AT/PV 节点不填开始/完成日期；普通节点和服务端上线节点继续要求计划完成日期。服务端上线节点无手工日期时使用需求主表 `计划上线时间`，手工日期优先。旧 Checklist 只作为规范服务端上线节点解析，不再次创建系统节点。

- [ ] **Step 4: Normalize date-only values**

在 `_date_time` 和 `_optional_date_time` 中对日期字符串、毫秒时间戳统一构造成带 `ZoneInfo("Asia/Shanghai")` 的午夜；保留带时区输入的绝对时间但在业务计算前转换到配置时区。更新 `calendar.py` 的自然日加减函数，确保单日节点按完整自然日计算而不是因 `00:00` 被缩短。

- [ ] **Step 5: Run repository and calendar tests**

Run: `python3 -m pytest tests/test_repository.py tests/test_calendar.py -q`

Expected: PASS；现有空行 warning 行为、链接目标校验、分页和系统字段写回测试不回归。

## Task 3: Replace Aggregate Duration Rules with Five Key-Stage Rules

**Files:**
- Modify: `src/requirement_monitor/fixed_rules.py:1-390`
- Modify: `src/requirement_monitor/risk.py:1-1160`
- Modify: `src/requirement_monitor/models.py:136-203`
- Test: `tests/test_fixed_rules.py:1-355`
- Test: `tests/test_risk.py:1-1200`

- [ ] **Step 1: Add failing fixed-rule tests**

要求固定业务规则文件提供 `AT 测试第一轮`、`AT 测试第二轮`、`PV 测试第一轮`、`PV 测试第二轮`、`线上回归` 五个明确默认周期；缺少或歧义时拒绝加载并指出具体名称。项目配置字段为空时继承固定值，有值时覆盖固定值。旧聚合 AT/PV 和角色专项规则不得参与新版关键周期计算。

```python
def test_parse_fixed_rules_exposes_each_key_stage_duration():
    rules = parse_fixed_rules(FIVE_STAGE_RULES)
    assert rules.at1_days > 0
    assert rules.at2_days > 0
    assert rules.pv1_days > 0
    assert rules.pv2_days > 0
    assert rules.regression_days > 0


def test_project_override_is_resolved_per_stage():
    effective = resolve_effective_rules(fixed_rules, config_with(at1_days=4, pv2_days=None))
    assert effective.stage_days["AT 测试第一轮"] == 4
    assert effective.stage_days["PV 测试第二轮"] == fixed_rules.pv2_days
```

- [ ] **Step 2: Implement `FixedRules` and `EffectiveRules` fields**

为 `FixedRules` 和 `EffectiveRules` 增加 `at1_days`、`at2_days`、`pv1_days`、`pv2_days`，并将新版有效时长集中为：

```python
stage_days = {
    "AT 测试第一轮": project_config.at1_days or fixed.at1_days,
    "AT 测试第二轮": project_config.at2_days or fixed.at2_days,
    "PV 测试第一轮": project_config.pv1_days or fixed.pv1_days,
    "PV 测试第二轮": project_config.pv2_days or fixed.pv2_days,
    "线上回归": project_config.regression_days or fixed.regression_days,
}
```

保留旧解析字段仅用于兼容已有快照，不把 `at_days`、`pv_days`、`bugfix_days`、客户端/车辆/服务端专项天数混入新版 `stage_days`。

- [ ] **Step 3: Add failing risk-gate tests**

覆盖以下确定性结果：

- 只检查 `需求主表.当前环节` 的通用节点缺失，历史/未来缺失不报整流程错误。
- 当前环节无节点只产生 Warning。
- 任何交付域存在测试节点即纳入该需求 AT/PV 链路；服务端、客户端、车辆测试排期分别计算。
- AT1/AT2 未完成或未跳过，进入 PV 时为 Severe。
- PV1/PV2 未完成或未跳过，进入服务端上线前或无服务端时进入线上回归前为 Severe。
- 线上回归未完成或未跳过，进入版本合入时为 Severe。
- 多语言翻译未完成只为 Warning，不减少合板可用缓冲，也不阻塞版本合入。
- `已完成`、`已跳过`通过，`未开始`、`进行中`、`已取消`不通过；跳过节点不参与最低时长。

- [ ] **Step 4: Implement stage order and gate evaluation**

更新 `_PROCESS_ORDER` 和 `_configured_stage_names`，规范顺序为：服务端上线在 PV2 后、线上回归前，多语言翻译在线上回归后、版本合入前。将 `_COMPLETED_NODE_STATUSES` 拆为“门禁通过状态”和“已取消状态”，避免取消被误判为通过。

新增纯函数接口：

```python
def evaluate_test_gates(
    requirement: Requirement,
    nodes: Sequence[DeliveryNode],
) -> List[NodeRisk]:
    """Return deterministic AT/PV/regression gate failures for the current stage."""


def current_stage_issues(
    requirement: Requirement,
    nodes: Sequence[DeliveryNode],
) -> List[str]:
    """Return warning-only missing-node messages for current_stage."""
```

对每个参与测试的交付域分别查找 AT1、AT2、PV1、PV2；允许同一轮节点被显式标为 `已跳过`。服务端上线只要求服务端发布节点，其他域不创建该节点。多语言翻译单独生成 Warning，不进入 Severe 聚合。

- [ ] **Step 5: Run fixed-rule and risk tests**

Run: `python3 -m pytest tests/test_fixed_rules.py tests/test_risk.py -q`

Expected: PASS；旧的正常风险、阻塞项、合板缓冲和服务端日期测试继续通过，新增门禁测试验证每个测试域的具体缺口。

## Task 4: Render Compact Structured Feishu Cards

**Files:**
- Modify: `src/requirement_monitor/cards.py:1-680`
- Modify: `src/requirement_monitor/models.py:260-330`
- Test: `tests/test_cards.py:1-760`

- [ ] **Step 1: Add failing card-shape tests**

断言卡片仍为 `msg_type=interactive`，且当前环节矩阵用结构化 `column_set`/`column` 元素而不是竖线拼接；第一行明确包含“环节、交付域、负责人、计划开始、计划完成、最晚安全DDL、状态”。并行交付域每行一组列，负责人全部通过 `<at id=...>name</at>` 的真实标签输出；姓名不得只出现在普通文本节点中。

```python
def test_daily_card_has_header_and_structured_current_stage_rows():
    payload = build_daily_card(report_with_parallel_nodes())
    body = json.dumps(payload, ensure_ascii=False)
    assert "环节" in body and "交付域" in body and "最晚安全DDL" in body
    assert "column_set" in body
    assert "｜" not in body


def test_card_links_only_include_non_empty_urls():
    payload = build_daily_card(report_with_links(translation_url=None))
    assert "需求文档" in payload_text(payload)
    assert "Meego" in payload_text(payload)
    assert "多语言翻译" not in payload_text(payload)


def test_severe_card_mentions_project_and_all_node_owners():
    payload = build_severe_card(report_with_two_node_owners())
    assert payload.count('tag="at"') >= 3
```

- [ ] **Step 2: Implement reusable card primitives**

在 `cards.py` 提取 `_column(text, width)`、`_stage_header()`、`_stage_row(node, requirement)`、`_mention(person)`、`_link_button(label, url)`，并让日报和严重卡片共享同一行结构。`_mention` 的输入必须是 `Person` 或 `(open_id, name)`，输出飞书真实 at 标签；不得用 `escape_value` 包装人名后代替 mention。

- [ ] **Step 3: Implement compact daily and severe layouts**

日报只展示当前环节的节点和并行交付域，摘要合并重复的需求名称/OKR目标，目标版本有值才展示，三个链接只显示已填写项。严重卡片只展示风险相关行、风险原因、处理动作、项目负责人和节点负责人，不展开历史节点。进展说明只取精简摘要，关键字段不截断。

- [ ] **Step 4: Add card-size fallback tests and implementation**

复用 Webhook 发送器的三层降级：完整结构化卡片 → 删除非关键进展说明的精简卡片 → 纯文本。保证降级仍保留需求名称、当前环节、负责人、DDL、状态和风险原因；卡片超长不能导致整批需求停止。

- [ ] **Step 5: Run card tests**

Run: `python3 -m pytest tests/test_cards.py tests/test_webhook.py -q`

Expected: PASS；确认 JSON 中没有伪表格竖线对齐逻辑，所有人员均为真实 @。

## Task 5: Update Runner, Notifications, and LLM Independence

**Files:**
- Modify: `src/requirement_monitor/runner.py:100-915`
- Modify: `src/requirement_monitor/llm.py:134-420`
- Modify: `src/requirement_monitor/webhook.py:78-500`
- Test: `tests/test_runner.py:1-1150`
- Test: `tests/test_llm.py:434-820`
- Test: `tests/test_webhook.py:1-500`

- [ ] **Step 1: Add failing notification-recipient tests**

普通进度通知的收件人集合为当前节点全部负责人；严重风险通知同时包含项目负责人和风险相关节点全部负责人，按 open_id 去重。需求宣讲未完成或已归档时不生成任何通知；当前环节缺失只允许 Warning 继续运行，单条坏数据只隔离该需求。

- [ ] **Step 2: Update runner fingerprint and notification records**

在 `_build_notifications` 中使用结构化负责人列表生成 mention 元数据和稳定指纹；更新 `NotificationRecord` 写回时允许多个通知对象，保留现有去重和最近通知时间语义。严重卡片的标题和摘要使用项目负责人视角，但节点负责人仍出现在卡片中。

- [ ] **Step 3: Keep deterministic fallback independent of LLM**

让 `_enrich_risk` 在 Token 过期、超时、无响应或返回结构错误时返回 `LLMEnrichment(status="unavailable")`，但不抛出运行级异常；`_build_notifications` 仍使用确定性 `RequirementRisk` 构建并发送卡片。LLM 不得修改门禁结果或把 Warning 降级为 Normal。

- [ ] **Step 4: Verify webhook validation and fallback**

Webhook 发送前依次验证完整卡片、精简卡片和纯文本 payload；记录 HTTP 错误、卡片格式错误和重试结果。任何单条卡片失败都写入通知记录并继续发送其他需求，运行级故障只在读取表格、鉴权或 Webhook 全局不可用时结束。

- [ ] **Step 5: Run runner and transport tests**

Run: `python3 -m pytest tests/test_runner.py tests/test_llm.py tests/test_webhook.py -q`

Expected: PASS；重点确认已有“严重通知只通知项目负责人”的旧测试按新版契约更新为“项目负责人 + 节点负责人”，并确认 LLM 失败仍能收到确定性卡片。

## Task 6: Update Documentation and Migration Instructions

**Files:**
- Modify: `README.md:70-160`
- Modify: `使用说明.md:1-end`
- Modify: `config.example.json:1-end`
- Test: `tests/test_cli.py:1-500`

- [ ] **Step 1: Document the editable Bitable fields**

说明用户手动填写 `需求名称`、`OKR目标`、目标版本、合板日期、计划上线日期、需求宣讲完成、通知开关、归档状态和三个可选链接；进展节点按“交付域 + 工作类型 + 节点名称”分别建立研发和测试配套，负责人可多选。

- [ ] **Step 2: Document stage and gate rules**

明确只校验当前环节；AT/PV 可提前以 `已跳过` 声明不适用；`已完成/已跳过`都通过；服务端上线在 PV2 后、线上回归前；多语言翻译在线上回归后、只告警、不阻塞合版；五个项目配置字段空值继承固定业务规则。

- [ ] **Step 3: Document manual test commands**

保留现有命令格式并加入：

```bash
python3 -m requirement_monitor.cli start --dry-run
python3 -m requirement_monitor.cli run --dry-run
python3 -m requirement_monitor.cli start
python3 -m requirement_monitor.cli stop
```

说明 Webhook URL 只从本地配置或环境变量读取，不把密钥写进仓库；LLM 不可用时仍应发送底线卡片。

- [ ] **Step 4: Update example configuration and CLI help**

在 `config.example.json` 展示 `Asia/Shanghai`、20:00、固定规则文件路径、Webhook 占位符、状态/日志目录和 LLM 可选配置；CLI 帮助说明 `start` 是常驻定时任务、可用 `stop` 终止，不打开浏览器。

- [ ] **Step 5: Run documentation-facing CLI tests**

Run: `python3 -m pytest tests/test_cli.py -q`

Expected: PASS；命令帮助、配置路径和 dry-run 行为不回归。

## Task 7: Full Regression and Acceptance Verification

**Files:**
- Modify only tests that fail because of the approved V2 contract.
- Test: `tests/` 全部测试。

- [ ] **Step 1: Run focused suite in dependency order**

Run:

```bash
cd /Users/mi/Documents/VScode项目/工作小工具/需求进展提醒机器人/.worktrees/requirement-monitor
python3 -m pytest tests/test_models.py tests/test_schema.py tests/test_repository.py -q
python3 -m pytest tests/test_fixed_rules.py tests/test_risk.py -q
python3 -m pytest tests/test_cards.py tests/test_runner.py tests/test_webhook.py tests/test_llm.py -q
```

Expected: 三组均 PASS；失败只允许来自已批准的字段/门禁契约变化，不修复无关历史问题。

- [ ] **Step 2: Run complete test suite**

Run: `python3 -m pytest -q`

Expected: 全量通过，保留已有集成测试的显式外部凭据跳过语义；不得调用真实 Webhook 或浏览器。

- [ ] **Step 3: Run static consistency checks**

Run:

```bash
rg -n "TODO|TBD|待定|未决" docs/superpowers/plans/2026-07-27-requirement-progress-monitor-v2.md
python3 -m compileall src
```

Expected: 计划无占位词，Python 源码编译成功。

- [ ] **Step 4: Verify acceptance scenarios with deterministic fixtures**

至少验证以下场景：

1. 需求名称和 OKR目标相同，卡片只展示一次。
2. 三个链接分别为空/合法/非法时，空值隐藏、合法可点击、非法只隔离当前需求。
3. 服务端、客户端、车辆各有独立研发和测试节点，测试日期不同但合板日期一致。
4. AT2/PV2 设为已跳过且无计划日期，门禁通过且不计时。
5. 进入 PV 前 AT 未通过、服务端上线前 PV 未通过、版本合入前线上回归未通过，均生成 Severe 并 @ 正确人员。
6. 多语言翻译未完成，只生成 Warning，不阻塞版本合入。
7. LLM Token 过期，仍发送确定性卡片。
8. 卡片包含结构化表头且不依赖竖线对齐。

- [ ] **Step 5: Preserve worktree for user review**

不创建 Git commit、不创建新分支、不调用真实 Webhook；汇报修改文件、测试命令和任何与本需求无关的既有失败。

---

## Execution Order

按以下依赖顺序执行：

1. Task 1 完成数据模型和 schema 契约。
2. Task 2 完成 Bitable 兼容解析和日期语义。
3. Task 3 完成确定性规则和门禁。
4. Task 4 可在 Task 3 后实现卡片结构。
5. Task 5 接入通知编排和 LLM/Webhook 降级。
6. Task 6 同步用户说明。
7. Task 7 做全量验收。

每个任务都先写失败测试，再实现最小改动，再运行该任务的测试。子代理只修改任务中列出的文件；主代理在任务之间检查 diff、测试结果和兼容性后再继续。
