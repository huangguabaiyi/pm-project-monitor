import json
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    ValidationError,
)

from .config import LLMSettings
from .models import LLMEnrichment, RequirementRisk, RiskLevel


NonEmptyStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]

_SYSTEM_PROMPT = """你是需求交付风险增强助手，必须严格遵守以下规则：
1. 固定规则只读，不得修改、补写、覆盖或重新解释。
2. 只读取传入的固定规则文本，不得读取、引用或推断其他规则文档。
3. 不得修改日期，也不得建议移动、替换或重写输入中的任何日期。
4. 确定性规则风险是下限，风险只能升级，不能降级。
5. 仅返回一个 JSON 对象，不得包含 Markdown 或其他文字。
6. JSON 必须且只能包含 risk_level、summary、reasons、actions 四个字段。
7. risk_level 只能是“普通”“预警”“严重”；summary 是字符串；
   reasons 和 actions 是字符串数组。
"""

_LEVELS = {
    "普通": RiskLevel.NORMAL,
    "预警": RiskLevel.WARNING,
    "严重": RiskLevel.SEVERE,
}


class _LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_level: Literal["普通", "预警", "严重"]
    summary: NonEmptyStr
    reasons: List[NonEmptyStr]
    actions: List[NonEmptyStr]


class LLMClient:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    def enrich(
        self,
        risk: RequirementRisk,
        fixed_rules: str,
        project_description: str,
    ) -> LLMEnrichment:
        if not self.settings.enabled:
            return self._unavailable(risk.level, "disabled")
        if self.settings.api_key is None:
            return self._unavailable(risk.level, "missing_api_key")
        if self.settings.base_url is None or self.settings.model is None:
            return self._unavailable(risk.level, "invalid_configuration")

        try:
            response = httpx.post(
                self._endpoint(),
                headers={
                    "Authorization": "Bearer {}".format(
                        self.settings.api_key.get_secret_value()
                    ),
                    "Content-Type": "application/json",
                },
                json=self._request_body(risk, fixed_rules, project_description),
                timeout=self.settings.timeout_seconds,
            )
        except httpx.TimeoutException:
            return self._unavailable(risk.level, "timeout")
        except httpx.HTTPError:
            return self._unavailable(risk.level, "request_error")
        except Exception:
            return self._unavailable(risk.level, "request_error")

        if response.status_code in (401, 403):
            return self._unavailable(risk.level, "authentication_error")
        if response.status_code == 429:
            return self._unavailable(risk.level, "rate_limit_error")
        if response.is_error:
            return self._unavailable(risk.level, "service_error")

        content, failure_reason = self._response_content(response)
        if failure_reason is not None:
            return self._unavailable(risk.level, failure_reason)

        try:
            raw_result = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return self._unavailable(risk.level, "invalid_json")

        try:
            result = _LLMResponse.model_validate(raw_result)
        except ValidationError:
            return self._unavailable(risk.level, "schema_error")

        llm_level = _LEVELS[result.risk_level]
        return LLMEnrichment(
            available=True,
            rule_level=risk.level,
            llm_level=llm_level,
            effective_level=max(risk.level, llm_level),
            summary=result.summary,
            reasons=result.reasons,
            actions=result.actions,
        )

    def _endpoint(self) -> str:
        return "{}/chat/completions".format(self.settings.base_url.rstrip("/"))

    def _request_body(
        self,
        risk: RequirementRisk,
        fixed_rules: str,
        project_description: str,
    ) -> Dict[str, Any]:
        user_input = {
            "risk": risk.model_dump(mode="json"),
            "fixed_rules": fixed_rules,
            "project_description": project_description,
        }
        return {
            "model": self.settings.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(user_input, ensure_ascii=False),
                },
            ],
        }

    @staticmethod
    def _response_content(
        response: httpx.Response,
    ) -> Tuple[Optional[str], Optional[str]]:
        if not response.content or not response.content.strip():
            return None, "empty_response"
        try:
            payload = response.json()
        except Exception:
            return None, "invalid_json"

        if not isinstance(payload, dict):
            return None, "empty_response"
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None, "empty_response"
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return None, "empty_response"
        message = first_choice.get("message")
        if not isinstance(message, dict):
            return None, "empty_response"
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return None, "empty_response"
        return content, None

    @staticmethod
    def _unavailable(
        rule_level: RiskLevel, failure_reason: str
    ) -> LLMEnrichment:
        return LLMEnrichment(
            available=False,
            rule_level=rule_level,
            effective_level=rule_level,
            failure_reason=failure_reason,
        )
