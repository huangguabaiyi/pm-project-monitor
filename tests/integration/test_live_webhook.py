import os

import pytest

from requirement_monitor.webhook import WebhookSender


pytestmark = pytest.mark.skipif(
    os.getenv("REQUIREMENT_MONITOR_LIVE_TEST") != "1",
    reason="live Feishu tests are opt-in",
)


def test_live_webhook_connectivity():
    webhook_url = os.getenv("REQUIREMENT_MONITOR_WEBHOOK_URL")
    if not webhook_url:
        pytest.skip("REQUIREMENT_MONITOR_WEBHOOK_URL is not set")

    payload = {
        "msg_type": "text",
        "content": {"text": "【测试】需求进展机器人连通性验证"},
    }
    with WebhookSender(webhook_url) as sender:
        result = sender.send(payload)

    assert result.success is True
    assert result.feishu_code == 0
    assert result.status_code is not None
    assert 200 <= result.status_code < 300
