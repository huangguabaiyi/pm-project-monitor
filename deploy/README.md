# Linux / Docker 部署

## Docker Compose

在项目根目录创建 `.env`：

```env
POSTGRES_PASSWORD=替换为强密码
MONITOR_PORT=8000
REQUIREMENT_MONITOR_TEST_WEBHOOK_URL=测试机器人Webhook
REQUIREMENT_MONITOR_BOT_KEYWORD=需求进展推送
```

启动：

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f worker
```

管理页面：`http://Linux主机IP:8000/`

首次启动后，通过页面或 CLI 创建人员、项目、需求和节点。Compose Worker 会自动创建“风险扫描”和“通知投递”两个任务。

停止：

```bash
docker compose down
```

删除数据库卷前请先备份：

```bash
docker compose exec db pg_dump -U monitor requirement_monitor > backup.sql
```

## systemd

适用于直接在 Linux 主机的 Python 虚拟环境运行：

```bash
sudo mkdir -p /opt/requirement-monitor
sudo cp -a . /opt/requirement-monitor/
cd /opt/requirement-monitor
uv sync
```

创建 `/etc/requirement-monitor.env`：

```env
REQUIREMENT_MONITOR_DATABASE_URL=postgresql+psycopg://monitor:密码@127.0.0.1:5432/requirement_monitor
REQUIREMENT_MONITOR_TEST_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/测试地址
REQUIREMENT_MONITOR_BOT_KEYWORD=需求进展推送
```

把 `deploy/config.database.json` 复制为 `/opt/requirement-monitor/config.database.json`，并将其中路径改成 Linux 实际路径。

安装服务：

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now requirement-monitor-api.service
sudo systemctl enable --now requirement-monitor-worker.service
sudo systemctl status requirement-monitor-api.service
sudo systemctl status requirement-monitor-worker.service
```

Worker 是常驻进程，每 30 秒扫描到期任务和待发送 Outbox；systemd 会在进程崩溃、主机重启后自动恢复。
