# Pulse 需求交付管理

Pulse 是一个数据库驱动的需求交付管理工具。它用可视化 DAG 模板描述串行与并行流程；需求创建后会获得一份节点快照，团队直接在需求详情维护每个节点的预期时间、状态和负责人。

## 核心设计

- 人员配置是独立基础数据，包含所属交付领域、角色和飞书 Open ID；需求只选择已有人员。
- 通用交付配置分为“交付领域”“通用节点”“可视化模板”。
- 模板画布支持拖动排版与连线；分叉表示并行，后端拒绝循环依赖。
- 交付节点属于需求，不提供脱离需求的顶层节点台账。
- 需求可选关联 Meego、需求文档和 Figma 地址，详情页集中提供快捷入口。
- 不使用项目字段和独立阻塞项，也不依赖飞书多维表格。
- 风险只根据节点预期开始/结束、实际状态和依赖关系确定，不设置 buffer 或默认环节耗时。
- 可选 AI 综合分析会读取需求和节点备注、人员、时间、状态与依赖，按固定 JSON Schema 返回结论；最终风险不会低于规则风险。

## 技术架构

- React + TypeScript + React Flow 管理台
- FastAPI + SQLAlchemy API
- SQLite 本地体验，PostgreSQL 正式部署
- Worker 定时扫描风险，通过 Outbox 可靠投递可选的飞书机器人通知

管理台提供独立的“AI 分析”页面。默认关闭，可选择实验性的 ChatGPT Plus 设备授权，或填写任意 OpenAI-compatible API 的 Base URL、API Key 和模型。凭证与配置均由页面维护；AI 不可用时只暂停智能分析，不影响规则风险和需求管理。

管理台提供独立的“Webhook 配置”页面，可分别维护测试和正式环境地址、切换当前投递环境、设置安全关键词并启停通知。接口只返回脱敏地址，Worker 优先读取页面保存的配置；未配置时仍兼容部署配置文件和环境变量。

## 本地启动

需要 Python 3.9+、Node.js 20+，推荐使用 `uv`：

```bash
uv sync --extra test
cd frontend && npm install && npm run build && cd ..
uv run requirement-monitor db-init
uv run requirement-monitor seed-demo
uv run requirement-monitor api --host 0.0.0.0 --port 8010
```

浏览器打开 <http://127.0.0.1:8010>。默认数据库为 `.state/pulse.db`。

## 测试

```bash
uv run pytest -q
cd frontend && npm run build
```

测试覆盖风险时间规则、跨层前置节点缺失时间、并行汇合、模板环路拒绝、需求节点快照和已移除的旧 API。

## 后台任务与通知

```bash
cp config.example.json config.local.json
uv run requirement-monitor worker --config config.local.json
```

Worker 首次启动会自动准备“风险扫描”和“通知投递”任务。没有配置 Webhook 时可以正常使用管理台；只有通知投递任务需要 Webhook。

## Docker Compose

```bash
docker compose up -d --build
```

不需要创建 `.env` 或修改配置文件。Compose 会启动 PostgreSQL、API 和 Worker，默认监听 `8000`，并使用命名卷持久化数据库和 ChatGPT 登录凭证。打开管理台后再按需配置 Webhook 与 AI；Plus 模式首次使用需要在“AI 分析”页面完成一次设备授权。正式部署前请在反向代理配置 HTTPS 和身份认证。更多说明见 [deploy/README.md](deploy/README.md)。
