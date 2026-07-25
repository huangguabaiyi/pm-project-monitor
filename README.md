# 需求进展提醒机器人

本工具运行在本机 Mac 上，从飞书多维表格读取需求、项目配置、进展节点和阻塞项，按固定业务规则计算风险，并通过飞书 Webhook 发送日报卡片。第一版不提供对外 Web 服务，也不启动 HTTP 上行接口；飞书表格读写使用本机已经认证的 `feishu` CLI。

## 安装

要求：

- macOS（`start`、`stop`、`restart` 和 `scheduled-run` 使用 `launchd`）。
- Python `3.9` 或更高版本。
- 已安装并可执行的飞书 CLI，且当前用户已完成认证。
- 本机可以访问飞书多维表格 API 和目标 Webhook。

在项目根目录创建虚拟环境并安装：

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
```

后续命令可以直接使用 `.venv/bin/requirement-monitor`，不依赖当前 shell 是否激活虚拟环境。

## 飞书 CLI 认证

先按照组织内飞书 CLI 的认证流程登录，再确认认证状态：

```bash
feishu auth status
```

工具不会代替飞书 CLI 保存或刷新认证信息。认证过期时，`run-once`、`scheduled-run` 或 `init-table` 返回退出码 `3`，本次操作停止。`run-once` 和 `scheduled-run` 进入运行器后可以通过已配置的 Webhook 记录或报告系统异常；`init-table` 不创建运行器，也不发送 Webhook 异常通知，只在终端返回错误并退出。修复方式是重新执行飞书 CLI 的认证流程，再重试命令；不要把 CLI token 写入项目文件或提交到 Git。

## 本地配置

复制示例配置，创建仅存在于本机的配置文件：

```bash
cp config.example.json config.local.json
export REQUIREMENT_MONITOR_CONFIG="$PWD/config.local.json"
```

默认配置文件是当前目录的 `config.local.json`；也可以通过 `REQUIREMENT_MONITOR_CONFIG` 指定其他路径，或通过各命令的 `--config PATH` 指定。`config.local.json` 不提交到仓库，示例文件只包含表格地址和非敏感默认值。

配置至少应确认以下字段：

- `bitable_url`：目标飞书多维表格或知识库表格 URL。
- `bot_keyword`：可选的飞书自定义机器人安全关键词；未启用关键词校验时保持 `null`。
- `fixed_rules_path`：固定业务规则文件，默认是项目根目录的 `固定业务规则`。
- `timezone`：默认 `Asia/Shanghai`。
- `send_hour` / `send_minute`：默认工作日 `20:00`。
- `state_dir` / `log_dir`：本地状态和日志目录。
- `llm.enabled`：是否启用可选 LLM 补充判断，默认关闭。

Webhook URL、LLM API key 以及其他 token/API key 不属于业务配置。推荐并优先从环境变量读取，不要把真实值写入仓库、测试、日志或多维表格：

```bash
export REQUIREMENT_MONITOR_WEBHOOK_URL='https://open.feishu.cn/open-apis/bot/v2/hook/...'

# 机器人启用了“关键词”安全设置时，必须与飞书后台配置完全一致：
export REQUIREMENT_MONITOR_BOT_KEYWORD='需求机器人'

# 只有启用 LLM 时才设置：
export REQUIREMENT_MONITOR_LLM_API_KEY='...'
export REQUIREMENT_MONITOR_LLM_BASE_URL='https://api.example.com/v1'
export REQUIREMENT_MONITOR_LLM_MODEL='model-name'
```

为兼容旧的本地配置，`webhook_url` 仍可出现在未提交的 `config.local.json` 中；如果同时设置 `REQUIREMENT_MONITOR_WEBHOOK_URL`，环境变量覆盖文件值。`bot_keyword` 也可写入本地配置，并由 `REQUIREMENT_MONITOR_BOT_KEYWORD` 覆盖。新配置的 Webhook 和 LLM key 应只使用环境变量，`config.local.json` 必须保持本地且不得提交，`config.example.json` 不包含任何 secret。`init-table` 的 schema 初始化不要求 Webhook；`stop` 和 `version` 也不加载 Webhook 配置。`run-once`、`start`、`status`、`scheduled-run` 和 `logs` 仍按默认配置要求可用 Webhook。若使用一次性 shell，可先在当前终端设置变量；不要把真实值写进 shell 历史、脚本或 `.env` 并提交。`feishu` CLI 的认证凭据由 CLI 自己管理，机器人不会接收其 token。

飞书 Webhook 返回 HTTP `200` 但业务码 `19024` 时，表示机器人启用了关键词安全校验，而消息中没有找到必需关键词。设置 `bot_keyword` 或 `REQUIREMENT_MONITOR_BOT_KEYWORD` 后，发送器会在文本、互动卡片、降级文本和系统异常卡片中保证该关键词可见，并避免重复注入；未配置关键词的机器人保持原 payload 不变。

## 固定业务规则

项目根目录的 `固定业务规则` 是只读业务基线，机器人只加载它，不通过配置文件、LLM 或多维表格修改它。当前规则包括：

- 服务端上线固定为每周二、周四，前一天完成上线 Checklist，`17:30` 后禁止上线。
- AT1 加 AT2 的测试周期通常不少于一周半。
- PV 测试通常约三天，加两天解 Bug，合计约五天。
- 线上回归通常约三天。

项目级差异应维护在 `项目配置表`，不要修改固定规则文件来适配单个项目。LLM 只能在已有规则和数据基础上补充可解释的风险判断，只能升级风险等级，不能降低风险、修改排期、改写固定规则或阻断基础通知。

## 六表初始化

成员可以直接在多维表格中维护业务数据；程序初始化只负责创建/补齐经过批准的结构，不会替代日常业务编辑。六张固定表名为：

1. `需求主表`
2. `进展节点表`
3. `阻塞项表`
4. `项目配置表`
5. `基础配置表`
6. `通知记录表`

复制 `config.example.json` 后，不设置任何 Webhook secret 也可以完成 schema 初始化。初始化前先确认 `feishu auth status` 和配置路径，然后只读预览：

```bash
.venv/bin/requirement-monitor init-table --dry-run
```

完整的三步复核流程如下；三条命令都只依赖已认证的飞书 CLI 和 `bitable_url`：

```bash
cp config.example.json config.local.json
export REQUIREMENT_MONITOR_CONFIG="$PWD/config.local.json"
.venv/bin/requirement-monitor init-table --dry-run
.venv/bin/requirement-monitor init-table --apply
.venv/bin/requirement-monitor init-table --dry-run
```

预览会列出表重命名、缺失表和缺失字段等操作，不修改线上表。确认输出符合设计后，才允许执行：

```bash
.venv/bin/requirement-monitor init-table --apply
```

执行后再次复核，确保没有剩余操作：

```bash
.venv/bin/requirement-monitor init-table --dry-run
```

正常结果是 `Schema is up to date.` 或没有待执行操作。初始化操作是幂等的；除非已经完成评审并确认目标表，否则不要在生产表上执行 `--apply`。Task13 的 live 测试不会 seed 记录，也不会修改表结构。

## 多维表格维护规则

业务成员可以直接编辑以下内容：

- `需求主表`：需求、项目、负责人、目标版本、合板/上线时间、宣讲完成、通知开关和归档状态。
- `进展节点表`：公共流程、研发、联调、提测、测试和发布节点，以及计划/实际时间、状态和说明。
- `阻塞项表`：阻塞事项、责任人、解决计划、实际解决时间和是否影响合板。
- `项目配置表`：工作日/自然日、AT/PV/回归/专项测试天数、上线日期/截止时间和 LLM 开关。
- `基础配置表`：环节、交付域、工作类型和测试角色等低频配置。

参与方和流程节点是动态数据，不通过新增主表字段扩展。新增服务端、客户端、车辆等交付域，或增加额外测试轮次/测试角色时，在 `基础配置表` 增加并启用对应配置记录，再在 `进展节点表` 使用相同的交付域建立研发与测试配套关系。系统预置交付域包含 `客户端`、`服务端`、`车辆`，也支持 `公共流程`、`中枢平台`、`嵌入式`、`插件`、`助手` 和 `其他`。

服务端需求应配套 `服务端 / 发布 / 上线 Checklist` 节点；其计划完成时间由系统按上线日期计算为前一天，成员维护负责人、状态和实际完成时间。客户端和车辆研发/测试同样通过交付域配套展示，不要求为每个端增加固定字段。

只有同时满足“需求宣讲已完成”“允许通知为是”“未归档”的需求才具备通知资格。系统字段、风险等级、预计完成时间、DDL、检查时间、通知时间和通知记录由程序维护，不应手工覆盖。

## 手动运行

先执行不写表、不发 Webhook 的预览：

```bash
.venv/bin/requirement-monitor run-once --dry-run
```

`--dry-run` 计算并打印待发送 payload，不写系统字段、不写通知记录、不发送 Webhook。确认数据和卡片内容后执行一次真实人工检查：

```bash
.venv/bin/requirement-monitor run-once
```

`run-once` 不改变后台调度状态。有效且符合通知条件的需求会发送；归档、未完成宣讲或关闭通知的需求不会发送。单条坏数据会记录异常并跳过，关键表/字段或飞书认证失败会停止本次计算。

## 自动调度

使用 macOS `launchd` 管理工作日定时任务：

```bash
.venv/bin/requirement-monitor start
.venv/bin/requirement-monitor status
.venv/bin/requirement-monitor restart
.venv/bin/requirement-monitor stop
```

常用运维命令：

```bash
.venv/bin/requirement-monitor logs
```

默认调度为 `Asia/Shanghai` 每个工作日 `20:00`。周六、周日不自动发送；如果 Mac 在 `20:00` 关机或休眠，恢复后也不补发错过的日报。调度进程只接受计划时间后五分钟内的触发，避免休眠恢复导致误补跑。

后台模式必须通过 `start` 或 `restart` 生成配置，不能手工让 LaunchAgent 直接指向仓库中的源配置。macOS LaunchAgent 不继承执行 `start` 时的 shell 环境，因此 `start` 会先解析源配置和当前环境变量，再将完整运行设置写入 `state_dir/runtime-config.json`；该文件使用原子写入、`0600` 权限，包含 Webhook、可选 LLM key 和机器人关键词，plist 的 `scheduled-run --config` 只指向这份私密快照。源配置不需要、也不应保存 Webhook 或 LLM key。`stop` 会卸载并禁用任务，但保留 runtime snapshot；它位于本地状态目录，不得提交或共享。

`start`、`stop` 和 `restart` 使用 LaunchAgent plist 同目录下基于固定 label 的 `.com.mi.requirement-monitor.lifecycle.lock` 非阻塞跨进程文件锁，串行化状态查询、launchctl 操作和文件事务；锁不依赖可变的源配置或 `state_dir`，因此不同配置发起的生命周期命令也不会互相覆盖。并发命令会明确返回 locked 错误，锁文件权限为 `0600`，异常退出也会释放锁。只有 `llm.enabled=true` 时 runtime snapshot 才保存 LLM API key；关闭 LLM 时即使环境中仍有旧 key，也会强制写入 `null`。

Task12 已处理夏令时到系统时区的调度转换。修改 Webhook、LLM key、机器人关键词、表格地址、规则路径、状态/日志目录、时区、发送时间、虚拟环境路径或配置路径后，必须在新环境变量已生效的终端执行 `restart`，以重写 runtime snapshot 并重新加载 LaunchAgent；发生系统时区或夏令时切换后也建议重启服务，以确保 plist 中的下一组触发时间已刷新。

`restart` 是完整事务：停止旧服务前会保存原 loaded/disabled 状态以及旧 plist、runtime snapshot。新配置写入或 LaunchAgent 加载任一步失败时，程序会恢复旧文件；原服务此前已加载时会重新 enable 并 bootstrap 旧 plist，此前 stopped/disabled 时不会意外加载。若恢复本身也失败，命令会同时报告新启动错误和恢复错误，但不会回显 Webhook 或 LLM 密钥。

## LLM 可选降级

LLM 只用于补充风险理由，基础规则和基础通知不依赖 LLM。未设置 LLM 环境变量、LLM token 过期、额度不足、超时、限流、空响应或格式错误时，系统自动降级到无 LLM 模式：继续计算基础风险、继续发送基础卡片，并在日志和通知记录中记录降级原因。关闭 LLM 不应阻断日报。

## 错误退出码

- `0`：成功，或没有需要处理的业务数据。
- `2`：配置错误，例如配置文件缺失、字段无效或 Webhook 未设置。
- `3`：飞书认证、表格访问或表结构错误。
- `4`：Webhook 发送出现失败，包括部分卡片失败或全部卡片失败。
- `5`：未预期的内部错误或本地调度错误。

部分 Webhook 失败但已有卡片发送成功时，会保留已成功/失败的运行结果并返回退出码 `4`；请结合 `status` 和 `logs` 检查失败记录。Webhook 重试和纯文本降级遵循程序内规则；如果唯一 Webhook 本身不可用，依靠本地日志和 `logs` 排查，不能指望机器人通过同一个坏掉的 Webhook 报告自身故障。

## 测试与安全验证

默认测试不访问网络，live 集成测试必须显式 opt-in：

```bash
.venv/bin/pytest -v
.venv/bin/python -m compileall -q src tests
git diff --check
```

默认 `pytest` 会跳过 `tests/integration/test_live_bitable.py` 和 `tests/integration/test_live_webhook.py`。live Bitable 测试使用当前已认证的 `feishu` CLI，并从 `REQUIREMENT_MONITOR_CONFIG` 或默认 `config.local.json` 读取表格配置；针对 wiki URL，它验证真实 metadata 形态中的 `app_token`、`table_id`、`name` 和 `url_type: wiki`。live Webhook 测试只在同时设置 `REQUIREMENT_MONITOR_LIVE_TEST=1` 与 `REQUIREMENT_MONITOR_WEBHOOK_URL` 时发送一次明确标记的文本；如果机器人配置了关键词，同时设置 `REQUIREMENT_MONITOR_BOT_KEYWORD`：

```bash
export REQUIREMENT_MONITOR_LIVE_TEST=1
export REQUIREMENT_MONITOR_WEBHOOK_URL='https://open.feishu.cn/open-apis/bot/v2/hook/...'
# 仅当机器人后台启用了关键词校验时设置：
export REQUIREMENT_MONITOR_BOT_KEYWORD='需求机器人'
.venv/bin/pytest tests/integration -v
```

不要在 CI、提交钩子或默认测试命令中设置 `REQUIREMENT_MONITOR_LIVE_TEST=1`。不要在未经确认的线上表格执行 `init-table --apply`，不要运行会发送真实 Webhook 的 `run-once` 来代替 dry-run 验证。
