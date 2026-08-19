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

服务器上建议先克隆仓库，然后用内置脚本检查 GitHub 是否有新提交：

```bash
./deploy/update-from-github.sh --check
```

确认要升级时执行：

```bash
./deploy/update-from-github.sh --apply
```

脚本会执行这些操作：

- `git fetch origin <当前分支>` 并列出远端新增提交。
- 检查本地分支是否可快进更新，避免覆盖本地提交。
- 默认先备份 PostgreSQL 到当前目录的 `backup-YYYYmmdd-HHMMSS.sql`。
- 执行 `git pull --ff-only`。
- 执行 `docker compose up -d --build` 重新构建并重启服务。

如果数据库还没启动，或你已经手动完成备份，可以跳过备份：

```bash
./deploy/update-from-github.sh --apply --skip-backup
```

也可以手动备份 PostgreSQL：

```bash
docker compose exec -T db pg_dump -U monitor requirement_monitor > backup.sql
```

管理台“自动化”页面也提供部署更新入口。为了避免公开页面误触发服务器命令，后端默认禁用这个入口；确认服务运行环境具备仓库、`git`、Docker Compose 和数据库备份权限后，在 API 环境变量中启用：

```bash
REQUIREMENT_MONITOR_DEPLOY_UPDATE_ENABLED=true
```

注意：如果 API 运行在当前 Docker 镜像内部，容器内通常没有宿主机 `.git` 仓库和 Docker Compose 控制权限，此时页面只能显示未启用或执行失败。最稳妥的方式仍是在服务器仓库目录执行 `./deploy/update-from-github.sh --apply`。

数据库初始化使用安全的 `create_all`，已有表和数据不会在启动时删除。当前版本尚未内置复杂迁移系统，大版本结构升级前必须保留备份。
