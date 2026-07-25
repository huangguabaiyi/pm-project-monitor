import ipaddress
import json
import time
from typing import Any, Callable, Dict, Mapping, Optional, Tuple
from urllib.parse import urlparse

import httpx

from .models import SendResult


MAX_PAYLOAD_BYTES = 20 * 1024
_RETRY_DELAYS = (10, 30, 120)
_CARD_FORMAT_HTTP_STATUSES = {400, 413, 422}


class WebhookSender:
    def __init__(
        self,
        webhook_url: str,
        sleep: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not self._is_secure_webhook_url(webhook_url):
            raise ValueError(
                "webhook URL must use HTTPS or loopback HTTP"
            )
        self._webhook_url = webhook_url
        self._sleep = sleep
        self._timeout_seconds = timeout_seconds

    def send(self, payload: Mapping[str, Any]) -> SendResult:
        format_used = self._format_used(payload)
        serialized_payload, validation_error = self._validated_payload(payload)
        if validation_error is not None:
            return SendResult(
                success=False,
                attempts=0,
                format_used=format_used,
                error=validation_error,
            )

        result = self._deliver(
            serialized_payload,
            format_used=format_used,
            retry=True,
        )
        if result.error != "card_format_rejected":
            return result

        fallback_payload = self._plain_text_fallback(payload)
        fallback_body, fallback_error = self._validated_payload(fallback_payload)
        if fallback_error is not None:
            return SendResult(
                success=False,
                attempts=result.attempts,
                format_used="text",
                status_code=result.status_code,
                feishu_code=result.feishu_code,
                error=fallback_error,
            )

        fallback_result = self._deliver(
            fallback_body,
            format_used="text",
            retry=False,
        )
        return fallback_result.model_copy(
            update={"attempts": result.attempts + fallback_result.attempts}
        )

    def _deliver(
        self,
        serialized_payload: bytes,
        format_used: str,
        retry: bool,
    ) -> SendResult:
        max_attempts = len(_RETRY_DELAYS) + 1 if retry else 1
        attempts = 0
        last_error = "request_error"

        while attempts < max_attempts:
            attempts += 1
            try:
                response = httpx.post(
                    self._webhook_url,
                    content=serialized_payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self._timeout_seconds,
                )
            except MemoryError:
                raise
            except httpx.TimeoutException:
                last_error = "timeout"
                if self._retry(attempts, max_attempts):
                    continue
                return SendResult(
                    success=False,
                    attempts=attempts,
                    format_used=format_used,
                    error=last_error,
                )
            except httpx.RequestError:
                last_error = "network_error"
                if self._retry(attempts, max_attempts):
                    continue
                return SendResult(
                    success=False,
                    attempts=attempts,
                    format_used=format_used,
                    error=last_error,
                )
            except Exception:
                return SendResult(
                    success=False,
                    attempts=attempts,
                    format_used=format_used,
                    error="request_error",
                )

            status_code = response.status_code
            feishu_code = self._feishu_code(response)

            if (
                format_used == "card"
                and status_code in _CARD_FORMAT_HTTP_STATUSES
            ):
                return SendResult(
                    success=False,
                    attempts=attempts,
                    format_used=format_used,
                    status_code=status_code,
                    feishu_code=feishu_code,
                    error="card_format_rejected",
                )

            if status_code == 429 or 500 <= status_code < 600:
                last_error = "http_{}".format(status_code)
                if self._retry(attempts, max_attempts):
                    continue
                return SendResult(
                    success=False,
                    attempts=attempts,
                    format_used=format_used,
                    status_code=status_code,
                    feishu_code=feishu_code,
                    error=last_error,
                )

            if not 200 <= status_code < 300:
                return SendResult(
                    success=False,
                    attempts=attempts,
                    format_used=format_used,
                    status_code=status_code,
                    feishu_code=feishu_code,
                    error="http_{}".format(status_code),
                )

            if feishu_code is None:
                return SendResult(
                    success=False,
                    attempts=attempts,
                    format_used=format_used,
                    status_code=status_code,
                    error="invalid_response",
                )

            if feishu_code == 0:
                return SendResult(
                    success=True,
                    attempts=attempts,
                    format_used=format_used,
                    status_code=status_code,
                    feishu_code=feishu_code,
                )

            last_error = "feishu_error_{}".format(feishu_code)
            return SendResult(
                success=False,
                attempts=attempts,
                format_used=format_used,
                status_code=status_code,
                feishu_code=feishu_code,
                error=last_error,
            )

        return SendResult(
            success=False,
            attempts=attempts,
            format_used=format_used,
            error=last_error,
        )

    def _retry(self, attempts: int, max_attempts: int) -> bool:
        if attempts >= max_attempts:
            return False
        self._sleep(_RETRY_DELAYS[attempts - 1])
        return True

    @staticmethod
    def _validated_payload(
        payload: Mapping[str, Any],
    ) -> Tuple[Optional[bytes], Optional[str]]:
        if not isinstance(payload, Mapping):
            return None, "invalid_payload"

        msg_type = payload.get("msg_type")
        if msg_type == "interactive":
            card = payload.get("card")
            if not isinstance(card, Mapping):
                return None, "invalid_interactive_payload"
            if not isinstance(card.get("header"), Mapping):
                return None, "invalid_interactive_payload"
            if not isinstance(card.get("elements"), list):
                return None, "invalid_interactive_payload"
        elif msg_type == "text":
            content = payload.get("content")
            if not isinstance(content, Mapping) or not isinstance(
                content.get("text"), str
            ):
                return None, "invalid_text_payload"
        else:
            return None, "invalid_payload"

        try:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except MemoryError:
            raise
        except (TypeError, ValueError, OverflowError):
            return None, "invalid_payload"

        if len(serialized) > MAX_PAYLOAD_BYTES:
            return None, "payload_too_large"
        return serialized, None

    @staticmethod
    def _format_used(payload: object) -> str:
        if isinstance(payload, Mapping) and payload.get("msg_type") == "text":
            return "text"
        return "card"

    @staticmethod
    def _plain_text_fallback(payload: Mapping[str, Any]) -> Dict[str, object]:
        lines = []
        card = payload.get("card")
        if isinstance(card, Mapping):
            WebhookSender._collect_text(card.get("header"), lines)
            WebhookSender._collect_text(card.get("elements"), lines)
        text = "\n".join(line for line in lines if line.strip()) or "通知"
        text = WebhookSender._truncate_text_payload(text)
        return {"msg_type": "text", "content": {"text": text}}

    @staticmethod
    def _collect_text(value: object, lines: list) -> None:
        if isinstance(value, str):
            lines.append(value)
            return
        if isinstance(value, Mapping):
            for key, nested_value in value.items():
                if key in {"content", "text", "title"}:
                    WebhookSender._collect_text(nested_value, lines)
            return
        if isinstance(value, list):
            for item in value:
                WebhookSender._collect_text(item, lines)

    @staticmethod
    def _truncate_text_payload(text: str) -> str:
        payload_overhead = len(
            json.dumps(
                {"msg_type": "text", "content": {"text": ""}},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        byte_limit = MAX_PAYLOAD_BYTES - payload_overhead
        encoded = text.encode("utf-8")
        if len(encoded) <= byte_limit:
            return text
        return encoded[:byte_limit].decode("utf-8", errors="ignore")

    @staticmethod
    def _feishu_code(response: httpx.Response) -> Optional[int]:
        try:
            payload = response.json()
        except MemoryError:
            raise
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        for key in ("code", "StatusCode", "status_code"):
            value = payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    @staticmethod
    def _is_secure_webhook_url(webhook_url: object) -> bool:
        if not isinstance(webhook_url, str):
            return False
        try:
            parsed = urlparse(webhook_url)
            hostname = parsed.hostname
            parsed.port
        except (TypeError, ValueError):
            return False
        if hostname is None or parsed.username is not None:
            return False
        if parsed.scheme == "https":
            return True
        if parsed.scheme != "http":
            return False
        if hostname.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            return False
