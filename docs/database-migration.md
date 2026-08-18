# 独立数据库迁移

当前迁移阶段已经加入 PostgreSQL 兼容的数据模型，同时支持 SQLite 作为本地验证数据库。迁移阶段不会修改现有风险计算规则，也不会改变前置/后置流程逻辑。

## 数据表范围

- `people`：人员目录，保存 `feishu_open_id`、可选的 `feishu_user_id`、姓名、描述、邮箱和启用状态。
- `projects`：项目目录。
- `requirements`：需求主数据和系统风险字段。
- `requirement_nodes`：需求交付节点。
- `requirement_node_people`：节点与人员的多对多关系。
- `blockers`：阻塞项。
- `project_configs`：项目级排期配置。
- `base_configs`：现有基础配置。
- `migration_runs`：每次导入的数量和校验问题。

人员表是必要的。飞书 `open_id` 不应散落在需求、节点和阻塞项字段里，业务表只保存人员外键；发送飞书 @ 时，再通过人员目录取得 `feishu_open_id`。人员表中的描述、邮箱和启用状态先作为维护字段，后续可扩展部门、角色和负责人替代关系。

## 创建数据库

本地验证可以使用 SQLite：

```bash
.venv/bin/requirement-monitor db-init --database-url sqlite+pysqlite:///./.state/requirement-monitor.db
```

Linux 正式环境建议使用 PostgreSQL：

```bash
.venv/bin/requirement-monitor db-init \\
  --database-url 'postgresql+psycopg://monitor:密码@127.0.0.1:5432/requirement_monitor'
```

## 导入飞书数据

先只读检查：

```bash
.venv/bin/requirement-monitor db-import-feishu \\
  --config config.local.json \\
  --database-url sqlite+pysqlite:///./.state/requirement-monitor.db \\
  --dry-run
```

确认数量和校验问题后执行导入：

```bash
.venv/bin/requirement-monitor db-import-feishu \\
  --config config.local.json \\
  --database-url sqlite+pysqlite:///./.state/requirement-monitor.db
```

## 切换 Runner 到数据库

迁移完成并检查数据后，在独立的运行配置中设置：

```json
{
  "data_source": "database",
  "database_url": "postgresql+psycopg://monitor:密码@127.0.0.1:5432/requirement_monitor"
}
```

`run-once --dry-run`、风险计算和现有卡片渲染会直接从数据库读取，不再调用飞书认证或多维表格。原 `data_source: "bitable"` 配置仍然保留，便于迁移期间进行新旧结果对照。

导入是 upsert 模式，使用飞书记录 ID 作为源记录标识，重复执行不会产生重复需求、节点、阻塞项或人员。当前阶段不会删除数据库中已经存在但本次快照缺失的数据，避免误删；正式切换前再增加显式的归档/删除策略。

## 当前暂不处理

- 不把 Hermes 或其他 Agent 作为数据库写入方。
- 不重写现有风险计算规则。
- 不实现流程前置/后置关系计算。
- 不从飞书通讯录自动补齐人员描述和邮箱；迁移时先保存已有 `open_id` 和姓名，后续再接入通讯录同步。
- 不把飞书通知记录直接当作新系统的通知 Outbox；通知发送队列将在后续调度迁移阶段建立。
