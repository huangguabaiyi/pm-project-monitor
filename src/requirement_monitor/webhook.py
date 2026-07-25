import copy
import json
import math
import time
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

import httpx

from .models import SendResult
from .webhook_url import is_allowed_webhook_url


MAX_PAYLOAD_BYTES = 20 * 1024
_RETRY_DELAYS = (10, 30, 120)
_CARD_FORMAT_CODES = {9499, 190001}
_CARD_FORMAT_EXACT_MESSAGES = {"bad request"}
_CARD_FORMAT_MESSAGES = (
    "invalid card",
    "card invalid",
    "card schema",
    "malformed card",
    "failed to create card content",
    "卡片格式",
    "卡片结构",
    "卡片校验",
)


class WebhookSender:
    def __init__(
        self,
        webhook_url: str,
        sleep: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 10.0,
        client: Optional[httpx.Client] = None,
        allow_loopback_http: bool = False,
        bot_keyword: Optional[str] = None,
    ) -> None:
        if not self._is_allowed_webhook_url(
            webhook_url, allow_loopback_http=allow_loopback_http
        ):
            raise ValueError("webhook URL must use an official endpoint")
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise ValueError("timeout_seconds must be positive")
        normalized_timeout = float(timeout_seconds)
        if not math.isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._webhook_url = webhook_url
        self._sleep = sleep
        self._timeout_seconds = normalized_timeout
        self._client = client if client is not None else httpx.Client()
        normalized_keyword = (
            bot_keyword.strip() if isinstance(bot_keyword, str) else ""
        )
        self._bot_keyword = normalized_keyword or None
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._client.close()
        except MemoryError:
            raise
        except Exception:
            pass
        finally:
            self._closed = True

    def send(self, payload: Mapping[str, Any]) -> SendResult:
        format_used = "card"
        try:
            if self._closed:
                raise RuntimeError("WebhookSender is closed")
            prepared_payload = self._with_bot_keyword(payload)
            format_used = self._format_used(prepared_payload)
            serialized_payload, validation_error = self._validated_payload(
                prepared_payload
            )
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

            fallback_payload = self._with_bot_keyword(
                self._plain_text_fallback(prepared_payload)
            )
            fallback_body, fallback_error = self._validated_payload(
                fallback_payload
            )
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
        except MemoryError:
            raise
        except Exception:
            return SendResult(
                success=False,
                attempts=0,
                format_used=format_used,
                error="client_error",
            )

    def _with_bot_keyword(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        keyword = self._bot_keyword
        if keyword is None or not isinstance(payload, Mapping):
            return payload

        prepared = copy.deepcopy(dict(payload))
        if self._keyword_is_visible(prepared, keyword):
            return prepared

        if prepared.get("msg_type") == "text":
            content = prepared.get("content")
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                text = content["text"]
                content["text"] = self._truncate_text_payload(
                    "{} {}".format(keyword, text).rstrip()
                )
            return prepared

        if prepared.get("msg_type") != "interactive":
            return prepared
        card = prepared.get("card")
        if not isinstance(card, dict):
            return prepared
        header = card.get("header")
        if not isinstance(header, dict):
            return prepared
        title = header.get("title")
        if isinstance(title, dict) and isinstance(title.get("content"), str):
            title["content"] = "{} {}".format(
                keyword, title["content"]
            ).rstrip()
            return prepared
        header["title"] = {"tag": "plain_text", "content": keyword}
        return prepared

    @staticmethod
    def _keyword_is_visible(payload: Mapping[str, Any], keyword: str) -> bool:
        if payload.get("msg_type") == "text":
            content = payload.get("content")
            return (
                isinstance(content, Mapping)
                and isinstance(content.get("text"), str)
                and keyword in content["text"]
            )
        if payload.get("msg_type") != "interactive":
            return False
        card = payload.get("card")
        if not isinstance(card, Mapping):
            return False
        lines = []
        WebhookSender._collect_text(card.get("header"), lines)
        WebhookSender._collect_text(card.get("elements"), lines)
        return keyword in "\n".join(lines)

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
                response = self._client.post(
                    self._webhook_url,
                    content=serialized_payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self._timeout_seconds,
                )
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
            except MemoryError:
                raise
            except Exception:
                return SendResult(
                    success=False,
                    attempts=attempts,
                    format_used=format_used,
                    error="client_error",
                )

            try:
                status_code = response.status_code
                feishu_code, feishu_message = self._feishu_response(response)

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

                if (
                    format_used == "card"
                    and self._is_card_format_rejection(
                        status_code, feishu_code, feishu_message
                    )
                ):
                    return SendResult(
                        success=False,
                        attempts=attempts,
                        format_used=format_used,
                        status_code=status_code,
                        feishu_code=feishu_code,
                        error="card_format_rejected",
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
            except MemoryError:
                raise
            except Exception:
                return SendResult(
                    success=False,
                    attempts=attempts,
                    format_used=format_used,
                    error="client_error",
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
            serialized = WebhookSender._serialize_payload(payload)
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
        if len(WebhookSender._serialized_text_payload(text)) <= MAX_PAYLOAD_BYTES:
            return text

        lower_bound = 0
        upper_bound = len(text)
        while lower_bound < upper_bound:
            midpoint = (lower_bound + upper_bound + 1) // 2
            candidate = text[:midpoint]
            if (
                len(WebhookSender._serialized_text_payload(candidate))
                <= MAX_PAYLOAD_BYTES
            ):
                lower_bound = midpoint
            else:
                upper_bound = midpoint - 1
        return text[:lower_bound]

    @staticmethod
    def _serialize_payload(payload: Mapping[str, Any]) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _serialized_text_payload(text: str) -> bytes:
        return WebhookSender._serialize_payload(
            {"msg_type": "text", "content": {"text": text}}
        )

    @staticmethod
    def _feishu_response(
        response: httpx.Response,
    ) -> Tuple[Optional[int], str]:
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError, TypeError):
            return None, ""
        if not isinstance(payload, dict):
            return None, ""

        feishu_code = None
        for key in ("code", "StatusCode", "status_code"):
            value = payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                feishu_code = value
                break

        feishu_message = ""
        for key in ("msg", "StatusMessage", "message"):
            value = payload.get(key)
            if isinstance(value, str):
                feishu_message = value
                break
        return feishu_code, feishu_message

    @staticmethod
    def _is_card_format_rejection(
        status_code: int,
        feishu_code: Optional[int],
        feishu_message: str,
    ) -> bool:
        if status_code not in {200, 400} or feishu_code == 0:
            return False
        if feishu_code is not None:
            return feishu_code in _CARD_FORMAT_CODES
        normalized_message = feishu_message.strip().casefold()
        if normalized_message in _CARD_FORMAT_EXACT_MESSAGES:
            return True
        return any(
            marker in normalized_message for marker in _CARD_FORMAT_MESSAGES
        )

    @staticmethod
    def _is_allowed_webhook_url(
        webhook_url: object,
        allow_loopback_http: bool,
    ) -> bool:
        return is_allowed_webhook_url(
            webhook_url,
            allow_loopback_http=allow_loopback_http,
        )
