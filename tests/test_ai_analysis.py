import json

import httpx

from requirement_monitor.ai_analysis import analyze_with_compatible_api


def test_openai_compatible_analysis_returns_fixed_structure():
    result = {
        "risk_level": "warning",
        "summary": "联调时间存在重叠风险",
        "confidence": 0.88,
        "delivery_forecast": {"status": "at_risk", "reason": "前置开发尚未完成"},
        "signals": [{"node_id": "node-1", "node_name": "联调", "risk_level": "warning", "reason": "依赖未完成", "evidence": ["节点备注：接口仍在调整"]}],
        "actions": [{"priority": "high", "action": "确认接口冻结时间", "owner_hint": "服务端负责人"}],
        "missing_information": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert body["response_format"]["type"] == "json_schema"
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        actual = analyze_with_compatible_api(base_url="https://ai.example.com/v1", api_key="secret", model="model-a", prompt="分析风险", payload={"requirement": {"name": "测试"}}, client=client)
    assert actual == result


def test_openai_compatible_analysis_falls_back_to_json_object():
    calls = []
    result = {"risk_level": "normal", "summary": "暂无明显风险", "confidence": 0.7, "delivery_forecast": {"status": "on_track", "reason": "节点按计划推进"}, "signals": [], "actions": [], "missing_information": []}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["response_format"]["type"])
        if len(calls) == 1:
            return httpx.Response(400, json={"error": "json_schema unsupported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        actual = analyze_with_compatible_api(base_url="https://ai.example.com/v1", api_key="secret", model="model-a", prompt="分析风险", payload={}, client=client)
    assert calls == ["json_schema", "json_object"]
    assert actual["risk_level"] == "normal"


def test_requirement_ai_input_uses_explicit_shanghai_times():
    from requirement_monitor.ai_analysis import requirement_ai_input

    payload = requirement_ai_input(
        {
            "id": "req-1",
            "name": "时区测试",
            "nodes": [
                {
                    "id": "node-1",
                    "name": "开发",
                    "planned_start": "2026-08-22T01:00:00+00:00",
                    "planned_end": "2026-08-22T10:00:00+00:00",
                    "owners": [],
                }
            ],
        }
    )
    assert payload["nodes"][0]["planned_start"] == "2026-08-22T09:00:00+08:00"
    assert payload["nodes"][0]["planned_end"] == "2026-08-22T18:00:00+08:00"
