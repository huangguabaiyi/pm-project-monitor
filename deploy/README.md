# Linux / Docker 部署

## Docker Compose

无需创建 `.env` 或配置运行环境，直接启动：

```bash
docker compose up -d --build
docker compose exec api requirement-monitor seed-demo
docker compose ps
docker compose logs -f worker
```

管理页面：`http://Linux主机IP:8000/`。Worker 会自动创建风险扫描和通知投递任务。

Webhook、AI 开关、第三方 API 凭证和模型都在管理页面配置。ChatGPT Plus 首次启用需在“AI 分析”页面完成一次设备授权，登录凭证保存在 `monitor-codex` 命名卷中。容器升级不会清除该卷。

生产环境仍建议通过 `.env` 覆盖默认的 `POSTGRES_PASSWORD` 和 `MONITOR_PORT`，并在反向代理增加 HTTPS 与访问认证；这些不是首次启动的必填项。

## 备份与升级

升级前备份 PostgreSQL：

```bash
docker compose exec db pg_dump -U monitor requirement_monitor > backup.sql
```

然后重新构建：

```bash
docker compose up -d --build
```

数据库初始化使用安全的 `create_all`，已有表和数据不会在启动时删除。当前版本尚未内置复杂迁移系统，大版本结构升级前必须保留备份。
