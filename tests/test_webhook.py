import json

import httpx
import pytest

from requirement_monitor.webhook import MAX_PAYLOAD_BYTES, WebhookSender


WEBHOOK_TOKEN = "top-secret-webhook-token"


@pytest.fixture
def webhook_url():
    return "https://open.feishu.cn/open-apis/bot/v2/hook/{}".format(
        WEBHOOK_TOKEN
    )


@pytest.fixture
def card_payload():
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "需求提醒"}
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": "第一条需求"},
                }
            ],
        },
    }


def test_sender_posts_valid_interactive_card(
    httpx_mock, webhook_url, card_payload
):
    httpx_mock.add_response(json={"code": 0, "msg": "success"})

    result = WebhookSender(webhook_url, sleep=lambda seconds: None).send(
        card_payload
    )

    assert result.success is True
    assert result.attempts == 1
    assert result.status_code == 200
    assert result.feishu_code == 0
    assert result.format_used == "card"
    assert result.error is None
    assert json.loads(httpx_mock.get_request().content) == card_payload


def test_sender_retries_only_transient_http_failures(
    httpx_mock, webhook_url, card_payload
):
    sleeps = []
    httpx_mock.add_response(status_code=500, json={"code": 500})
    httpx_mock.add_response(status_code=502, json={"code": 502})
    httpx_mock.add_response(status_code=429, json={"code": 99991402})
    httpx_mock.add_response(json={"code": 0})

    result = WebhookSender(webhook_url, sleep=sleeps.append).send(card_payload)

    assert result.success is True
    assert result.attempts == 4
    assert sleeps == [10, 30, 120]
    assert len(httpx_mock.get_requests()) == 4


def test_sender_retries_timeout_without_exposing_request(
    httpx_mock, webhook_url, card_payload
):
    sleeps = []
    for _ in range(4):
        httpx_mock.add_exception(httpx.ReadTimeout("timed out at {}".format(webhook_url)))

    result = WebhookSender(webhook_url, sleep=sleeps.append).send(card_payload)

    assert result.success is False
    assert result.attempts == 4
    assert result.status_code is None
    assert result.feishu_code is None
    assert result.error == "timeout"
    assert sleeps == [10, 30, 120]
    assert WEBHOOK_TOKEN not in repr(result)
    assert webhook_url not in repr(result)


def test_nonzero_feishu_code_never_retries(
    httpx_mock, webhook_url, card_payload
):
    sleeps = []
    httpx_mock.add_response(
        json={
            "code": 99991402,
            "msg": "rate limited for {} at {}".format(
                WEBHOOK_TOKEN, webhook_url
            ),
        }
    )

    result = WebhookSender(webhook_url, sleep=sleeps.append).send(card_payload)

    assert result.success is False
    assert result.attempts == 1
    assert result.status_code == 200
    assert result.feishu_code == 99991402
    assert result.format_used == "card"
    assert result.error == "feishu_error_99991402"
    assert sleeps == []
    assert len(httpx_mock.get_requests()) == 1
    assert WEBHOOK_TOKEN not in repr(result)
    assert webhook_url not in repr(result)


def test_nonzero_feishu_code_is_an_immediate_failure(
    httpx_mock, webhook_url, card_payload
):
    httpx_mock.add_response(
        json={"code": 190001, "msg": "invalid card: {}".format(WEBHOOK_TOKEN)}
    )

    result = WebhookSender(webhook_url, sleep=lambda seconds: None).send(
        card_payload
    )

    assert result.success is False
    assert result.attempts == 1
    assert result.status_code == 200
    assert result.feishu_code == 190001
    assert result.format_used == "card"
    assert result.error == "feishu_error_190001"
    assert WEBHOOK_TOKEN not in repr(result)


def test_invalid_card_http_4xx_degrades_once_to_plain_text(
    httpx_mock, webhook_url, card_payload
):
    sleeps = []
    httpx_mock.add_response(
        status_code=400,
        json={"code": 190001, "msg": "invalid card"},
    )
    httpx_mock.add_response(json={"code": 0})

    result = WebhookSender(webhook_url, sleep=sleeps.append).send(card_payload)

    assert result.success is True
    assert result.attempts == 2
    assert result.status_code == 200
    assert result.feishu_code == 0
    assert result.format_used == "text"
    assert sleeps == []

    requests = httpx_mock.get_requests()
    assert [json.loads(request.content)["msg_type"] for request in requests] == [
        "interactive",
        "text",
    ]
    fallback = json.loads(requests[1].content)
    assert "需求提醒" in fallback["content"]["text"]
    assert "第一条需求" in fallback["content"]["text"]


def test_plain_text_fallback_is_not_replayed_when_it_fails(
    httpx_mock, webhook_url, card_payload
):
    sleeps = []
    httpx_mock.add_response(status_code=422, json={"code": 190001})
    httpx_mock.add_response(
        status_code=503,
        json={"code": 503, "msg": "sensitive response body"},
    )

    result = WebhookSender(webhook_url, sleep=sleeps.append).send(card_payload)

    assert result.success is False
    assert result.attempts == 2
    assert result.status_code == 503
    assert result.format_used == "text"
    assert result.error == "http_503"
    assert sleeps == []
    assert len(httpx_mock.get_requests()) == 2


def test_authentication_4xx_does_not_degrade_or_retry(
    httpx_mock, webhook_url, card_payload
):
    httpx_mock.add_response(
        status_code=401,
        json={
            "code": 99991663,
            "msg": "bad token {} at {}".format(WEBHOOK_TOKEN, webhook_url),
        },
    )

    result = WebhookSender(webhook_url, sleep=lambda seconds: None).send(
        card_payload
    )

    assert result.success is False
    assert result.attempts == 1
    assert result.status_code == 401
    assert result.feishu_code == 99991663
    assert result.error == "http_401"
    assert len(httpx_mock.get_requests()) == 1
    assert WEBHOOK_TOKEN not in repr(result)
    assert webhook_url not in repr(result)


@pytest.mark.parametrize(
    "payload",
    (
        {"msg_type": "interactive"},
        {"msg_type": "interactive", "card": {"elements": []}},
        {"msg_type": "interactive", "card": {"header": {}}},
        {"msg_type": "interactive", "card": {"header": {}, "elements": {}}},
    ),
)
def test_invalid_interactive_structure_is_rejected_before_network(
    httpx_mock, webhook_url, payload
):
    result = WebhookSender(webhook_url, sleep=lambda seconds: None).send(payload)

    assert result.success is False
    assert result.attempts == 0
    assert result.format_used == "card"
    assert result.error == "invalid_interactive_payload"
    assert httpx_mock.get_requests() == []


def test_oversized_payload_is_rejected_before_network(
    httpx_mock, webhook_url, card_payload
):
    card_payload["card"]["elements"][0]["text"]["content"] = (
        "密" * MAX_PAYLOAD_BYTES
    )

    result = WebhookSender(webhook_url, sleep=lambda seconds: None).send(
        card_payload
    )

    assert result.success is False
    assert result.attempts == 0
    assert result.error == "payload_too_large"
    assert httpx_mock.get_requests() == []


@pytest.mark.parametrize(
    "url",
    (
        "http://localhost:8080/hook/token",
        "http://127.0.0.2:8080/hook/token",
        "http://[::1]:8080/hook/token",
        "https://open.feishu.cn/open-apis/bot/v2/hook/token",
    ),
)
def test_https_and_loopback_http_webhook_urls_are_allowed(url):
    WebhookSender(url, sleep=lambda seconds: None)


@pytest.mark.parametrize(
    "url",
    (
        "http://open.feishu.cn/open-apis/bot/v2/hook/token",
        "http://localhost.example.com/hook/token",
        "ftp://localhost/hook/token",
        "not-a-url",
    ),
)
def test_insecure_webhook_urls_are_rejected_without_echoing_secret(url):
    with pytest.raises(ValueError) as exc_info:
        WebhookSender(url, sleep=lambda seconds: None)

    assert str(exc_info.value) == "webhook URL must use HTTPS or loopback HTTP"
    assert "token" not in str(exc_info.value)
