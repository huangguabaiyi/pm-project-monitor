from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Literal, Mapping, Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_AI_PROMPT = """你是资深项目交付风险分析助手。请根据需求基本信息、交付节点、节点依赖、负责人、计划时间、实际状态、节点备注、受阻原因和规则风险进行综合判断。

分析原则：
1. 已经逾期、依赖冲突等确定性规则风险不能被 AI 降级。
2. 重点识别备注中的范围变化、资源不足、方案未确认、跨团队依赖、质量隐患和进度不确定性。
3. 后置节点已设置时间而前置节点缺失时间时，必须说明排期完整性风险。
4. 不猜测未提供的事实；信息不足时写入 missing_information。
5. 建议必须具体、可执行、按优先级排序。
6. 只返回符合给定 JSON Schema 的对象，不输出 Markdown 或额外说明。"""

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _ai_datetime(value: object) -> object:
    if value is None or not isinstance(value, (datetime, str)):
        return value
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(LOCAL_TIMEZONE).isoformat()


RiskName = Literal["normal", "warning", "severe"]


class StrictResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeliveryForecast(StrictResultModel):
    status: Literal["on_track", "at_risk", "delayed", "unknown"]
    reason: str


class RiskSignal(StrictResultModel):
    node_id: Optional[str]
    node_name: str
    risk_level: RiskName
    reason: str
    evidence: List[str]


class SuggestedAction(StrictResultModel):
    priority: Literal["high", "medium", "low"]
    action: str
    owner_hint: str


class AIAnalysis(StrictResultModel):
    risk_level: RiskName
    summary: str
    confidence: float = Field(ge=0, le=1)
    delivery_forecast: DeliveryForecast
    signals: List[RiskSignal]
    actions: List[SuggestedAction]
    missing_information: List[str]


AI_ANALYSIS_SCHEMA = AIAnalysis.model_json_schema()


def requirement_ai_input(requirement: Mapping[str, object]) -> Dict[str, object]:
    nodes = []
    for node in requirement.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        nodes.append(
            {
                "id": node.get("id"),
                "name": node.get("name"),
                "domain": node.get("domain_name"),
                "planned_start": _ai_datetime(node.get("planned_start")),
                "planned_end": _ai_datetime(node.get("planned_end")),
                "actual_start": _ai_datetime(node.get("actual_start")),
                "actual_end": _ai_datetime(node.get("actual_end")),
                "status": node.get("status"),
                "owners": [person.get("display_name") for person in node.get("owners") or [] if isinstance(person, Mapping)],
                "notes": node.get("notes"),
                "blocked_reason": node.get("blocked_reason"),
                "rule_risk_level": node.get("risk_level"),
                "rule_risk_reasons": node.get("risk_reasons") or [],
            }
        )
    return {
        "requirement": {
            "id": requirement.get("id"),
            "sequence_id": requirement.get("sequence_id"),
            "name": requirement.get("name"),
            "owner": (requirement.get("owner") or {}).get("display_name") if isinstance(requirement.get("owner"), Mapping) else None,
            "target_version": requirement.get("target_version"),
            "notes": requirement.get("notes"),
            "links": {
                "meego": requirement.get("meego_url"),
                "requirement": requirement.get("requirement_url"),
                "figma": requirement.get("figma_url"),
            },
            "rule_risk_level": requirement.get("schedule_risk_level", requirement.get("risk_level")),
            "rule_risk_reasons": requirement.get("risk_reasons") or [],
        },
        "nodes": nodes,
        "edges": requirement.get("edges") or [],
    }


def input_fingerprint(payload: Mapping[str, object]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _analysis_prompt(system_prompt: str, payload: Mapping[str, object]) -> str:
    return "{}\n\n以下是待分析数据：\n{}".format(
        system_prompt.strip() or DEFAULT_AI_PROMPT,
        json.dumps(payload, ensure_ascii=False, default=str, indent=2),
    )


def _extract_json(text: str) -> Dict[str, object]:
    content = text.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        content = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("AI 未返回有效 JSON")
        value = json.loads(content[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("AI 返回结果必须是 JSON 对象")
    return value


def _validate_analysis(value: Mapping[str, object]) -> Dict[str, object]:
    return AIAnalysis.model_validate(value).model_dump(mode="json")


def analyze_with_compatible_api(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    payload: Mapping[str, object],
    timeout_seconds: float = 90,
    client: Optional[httpx.Client] = None,
) -> Dict[str, object]:
    if not api_key.strip():
        raise ValueError("请先配置 API Key")
    endpoint = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt.strip() or DEFAULT_AI_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "requirement_risk_analysis", "strict": True, "schema": AI_ANALYSIS_SCHEMA},
        },
    }
    owned_client = client is None
    http = client or httpx.Client(timeout=timeout_seconds)
    try:
        response = http.post(endpoint, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=body)
        if response.status_code in {400, 404, 422}:
            # A number of OpenAI-compatible providers support JSON mode but not json_schema.
            fallback = dict(body)
            fallback["response_format"] = {"type": "json_object"}
            response = http.post(endpoint, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=fallback)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(part.get("text") or "") for part in content if isinstance(part, Mapping))
        return _validate_analysis(_extract_json(str(content)))
    except httpx.HTTPStatusError as error:
        raise RuntimeError(f"AI 服务返回 HTTP {error.response.status_code}") from error
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("AI 服务返回格式不受支持") from error
    finally:
        if owned_client:
            http.close()


def analyze_with_chatgpt_plus(
    *,
    model: str,
    prompt: str,
    payload: Mapping[str, object],
    timeout_seconds: float = 180,
) -> Dict[str, object]:
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("当前镜像未安装 Codex CLI")
    with tempfile.TemporaryDirectory(prefix="pulse-ai-") as directory:
        root = Path(directory)
        schema_path = root / "schema.json"
        output_path = root / "result.json"
        schema_path.write_text(json.dumps(AI_ANALYSIS_SCHEMA, ensure_ascii=False), encoding="utf-8")
        command = [
            executable,
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        if model.strip():
            command.extend(["--model", model.strip()])
        command.append("-")
        try:
            completed = subprocess.run(
                command,
                input=_analysis_prompt(prompt, payload),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=directory,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("ChatGPT Plus 分析超时") from error
        if completed.returncode != 0 or not output_path.exists():
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            reason = detail[-1][:300] if detail else "请检查 Plus 登录状态"
            raise RuntimeError(f"ChatGPT Plus 分析失败：{reason}")
        return _validate_analysis(_extract_json(output_path.read_text(encoding="utf-8")))


class PlusLoginManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen[str]] = None
        self._output = ""

    def _consume(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(1)
            if not chunk:
                break
            with self._lock:
                self._output = (self._output + chunk)[-12000:]

    def start(self) -> Dict[str, object]:
        executable = shutil.which("codex")
        if not executable:
            raise RuntimeError("当前镜像未安装 Codex CLI")
        with self._lock:
            already_running = bool(self._process and self._process.poll() is None)
        if already_running:
            return self.status()
        with self._lock:
            self._output = ""
            self._process = subprocess.Popen(
                [executable, "login", "--device-auth"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=0,
            )
            process = self._process
            threading.Thread(target=self._consume, args=(process,), daemon=True).start()
        time.sleep(0.5)
        return self.status()

    def status(self) -> Dict[str, object]:
        executable = shutil.which("codex")
        if not executable:
            return {"installed": False, "authenticated": False, "running": False, "output": "Codex CLI 未安装"}
        with self._lock:
            process = self._process
            running = bool(process and process.poll() is None)
            output = self._output
        checked = subprocess.run([executable, "login", "status"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10, check=False)
        return {
            "installed": True,
            "authenticated": checked.returncode == 0,
            "running": running,
            "output": output,
            "status_text": checked.stdout.strip(),
        }


plus_login_manager = PlusLoginManager()
