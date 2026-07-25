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


def test_http_200_invalid_card_code_degrades_to_plain_text(
    httpx_mock, webhook_url, card_payload
):
    httpx_mock.add_response(
        json={"code": 190001, "msg": "invalid card: {}".format(WEBHOOK_TOKEN)}
    )
    httpx_mock.add_response(json={"code": 0})

    result = WebhookSender(webhook_url, sleep=lambda seconds: None).send(
        card_payload
    )

    assert result.success is True
    assert result.attempts == 2
    assert result.status_code == 200
    assert result.feishu_code == 0
    assert result.format_used == "text"
    assert result.error is None
    assert WEBHOOK_TOKEN not in repr(result)


def test_http_400_invalid_card_degrades_once_to_plain_text(
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


def test_http_400_card_schema_message_degrades_to_plain_text(
    httpx_mock, webhook_url, card_payload
):
    httpx_mock.add_response(
        status_code=400,
        json={"code": 190099, "msg": "card schema validation failed"},
    )
    httpx_mock.add_response(json={"code": 0})

    result = WebhookSender(webhook_url, sleep=lambda seconds: None).send(
        card_payload
    )

    assert result.success is True
    assert result.attempts == 2
    assert result.format_used == "text"
    assert len(httpx_mock.get_requests()) == 2


def test_plain_text_fallback_is_not_replayed_when_it_fails(
    httpx_mock, webhook_url, card_payload
):
    sleeps = []
    httpx_mock.add_response(
        status_code=400,
        json={"code": 190001, "msg": "invalid card"},
    )
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


def test_http_400_authentication_error_does_not_degrade(
    httpx_mock, webhook_url, card_payload
):
    httpx_mock.add_response(
        status_code=400,
        json={"code": 99991663, "msg": "invalid webhook token"},
    )

    result = WebhookSender(webhook_url, sleep=lambda seconds: None).send(
        card_payload
    )

    assert result.success is False
    assert result.attempts == 1
    assert result.status_code == 400
    assert result.feishu_code == 99991663
    assert result.format_used == "card"
    assert result.error == "http_400"
    assert len(httpx_mock.get_requests()) == 1


def test_http_400_non_card_schema_error_does_not_degrade(
    httpx_mock, webhook_url, card_payload
):
    httpx_mock.add_response(
        status_code=400,
        json={"code": 190099, "msg": "request schema validation failed"},
    )

    result = WebhookSender(webhook_url, sleep=lambda seconds: None).send(
        card_payload
    )

    assert result.success is False
    assert result.attempts == 1
    assert result.status_code == 400
    assert result.feishu_code == 190099
    assert result.format_used == "card"
    assert result.error == "http_400"
    assert len(httpx_mock.get_requests()) == 1


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
        "https://open.feishu.cn/open-apis/bot/v2/hook/token",
        "https://open.larksuite.com/open-apis/bot/v2/hook/token",
    ),
)
def test_official_production_webhook_urls_are_allowed(url):
    WebhookSender(url, sleep=lambda seconds: None)


@pytest.mark.parametrize(
    "url",
    (
        "http://localhost:8080/hook/token",
        "http://127.0.0.2:8080/hook/token",
        "http://[::1]:8080/hook/token",
    ),
)
def test_loopback_http_requires_explicit_test_flag(url):
    with pytest.raises(ValueError):
        WebhookSender(url, sleep=lambda seconds: None)

    WebhookSender(
        url,
        sleep=lambda seconds: None,
        allow_loopback_http=True,
    )


@pytest.mark.parametrize(
    "url",
    (
        "http://open.feishu.cn/open-apis/bot/v2/hook/token",
        "http://localhost.example.com/hook/token",
        "https://example.com/open-apis/bot/v2/hook/token",
        "https://open.feishu.cn/open-apis/bot/v1/hook/token",
        "https://evil.open.feishu.cn/open-apis/bot/v2/hook/token",
        "https://open.larksuite.com/open-apis/bot/v2/hook/",
        "https://open.feishu.cn/open-apis/bot/v2/hook/token?secret=1",
        "ftp://localhost/hook/token",
        "not-a-url",
    ),
)
def test_non_official_webhook_urls_are_rejected_without_echoing_secret(url):
    with pytest.raises(ValueError) as exc_info:
        WebhookSender(url, sleep=lambda seconds: None)

    assert str(exc_info.value) == "webhook URL must use an official endpoint"
    assert "token" not in str(exc_info.value)


@pytest.mark.parametrize(
    "timeout_seconds",
    (0, -1, float("nan"), float("inf"), "10", None),
)
def test_timeout_must_be_a_finite_positive_number(webhook_url, timeout_seconds):
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        WebhookSender(webhook_url, timeout_seconds=timeout_seconds)


def test_injected_client_is_reused_for_retries_and_closed_by_context(
    webhook_url, card_payload
):
    requests = []
    responses = iter(
        (
            httpx.Response(500, json={"code": 500}),
            httpx.Response(200, json={"code": 0}),
        )
    )

    def handler(request):
        requests.append(request)
        return next(responses)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sleeps = []
    with WebhookSender(
        webhook_url,
        client=client,
        sleep=sleeps.append,
    ) as sender:
        result = sender.send(card_payload)

    assert result.success is True
    assert result.attempts == 2
    assert sleeps == [10]
    assert len(requests) == 2
    assert client.is_closed is True


def test_close_closes_injected_client(webhook_url):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: None))
    sender = WebhookSender(webhook_url, client=client)

    sender.close()

    assert client.is_closed is True


def test_unexpected_client_error_propagates(webhook_url, card_payload):
    class ExplodingClient:
        def post(self, *args, **kwargs):
            raise RuntimeError("programming error")

        def close(self):
            return None

    sender = WebhookSender(webhook_url, client=ExplodingClient())

    with pytest.raises(RuntimeError, match="programming error"):
        sender.send(card_payload)


def test_invalid_json_response_is_sanitized_failure(webhook_url, card_payload):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"{invalid")
        )
    )
    with WebhookSender(webhook_url, client=client) as sender:
        result = sender.send(card_payload)

    assert result.success is False
    assert result.attempts == 1
    assert result.error == "invalid_response"


def test_json_value_error_is_sanitized_failure(webhook_url, card_payload):
    class InvalidJsonResponse:
        status_code = 200

        def json(self):
            raise ValueError("decoder rejected response")

    class StaticClient:
        def post(self, *args, **kwargs):
            return InvalidJsonResponse()

        def close(self):
            return None

    sender = WebhookSender(webhook_url, client=StaticClient())

    result = sender.send(card_payload)

    assert result.success is False
    assert result.attempts == 1
    assert result.error == "invalid_response"


def test_memory_error_from_response_json_propagates(webhook_url, card_payload):
    class MemoryResponse:
        status_code = 200

        def json(self):
            raise MemoryError

    class StaticClient:
        def post(self, *args, **kwargs):
            return MemoryResponse()

        def close(self):
            return None

    sender = WebhookSender(webhook_url, client=StaticClient())

    with pytest.raises(MemoryError):
        sender.send(card_payload)


def test_unexpected_json_error_propagates(webhook_url, card_payload):
    class ExplodingResponse:
        status_code = 200

        def json(self):
            raise RuntimeError("unexpected decoder error")

    class StaticClient:
        def post(self, *args, **kwargs):
            return ExplodingResponse()

        def close(self):
            return None

    sender = WebhookSender(webhook_url, client=StaticClient())

    with pytest.raises(RuntimeError, match="unexpected decoder error"):
        sender.send(card_payload)


def test_fallback_truncates_by_final_serialized_json_bytes():
    source = '\\"' * MAX_PAYLOAD_BYTES

    truncated = WebhookSender._truncate_text_payload(source)
    serialized = json.dumps(
        {"msg_type": "text", "content": {"text": truncated}},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")

    assert truncated != source
    assert len(serialized) <= MAX_PAYLOAD_BYTES
    next_character = source[len(truncated)]
    expanded = json.dumps(
        {
            "msg_type": "text",
            "content": {"text": truncated + next_character},
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(expanded) > MAX_PAYLOAD_BYTES
