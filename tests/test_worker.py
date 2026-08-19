from requirement_monitor.worker import _risk_card


def test_risk_card_mentions_risky_node_owners_and_adds_link_buttons():
    payload = _risk_card(
        {
            "sequence_id": 7,
            "name": "支付链路改造",
            "risk_level": 2,
            "risk_reasons": ["开发节点逾期", "测试节点前置未完成"],
            "current_nodes": ["开发", "测试"],
            "meego_url": "https://project.meego.cn/story/7",
            "figma_url": "https://www.figma.com/design/example",
            "requirement_url": "https://docs.example.com/requirement/7",
            "nodes": [
                {
                    "name": "开发",
                    "risk_level": 2,
                    "owners": [
                        {"display_name": "沈言", "feishu_open_id": "ou_backend"}
                    ],
                },
                {
                    "name": "测试",
                    "risk_level": 1,
                    "owners": [{"display_name": "陈默", "feishu_open_id": ""}],
                },
                {
                    "name": "视觉",
                    "risk_level": 0,
                    "owners": [
                        {"display_name": "周屿", "feishu_open_id": "ou_client"}
                    ],
                },
            ],
        }
    )

    assert payload["msg_type"] == "interactive"
    content = payload["card"]["elements"][0]["content"]
    assert "<at id=ou_backend>沈言</at>" in content
    assert "@陈默" in content
    assert "周屿" not in content
    actions = payload["card"]["elements"][1]["actions"]
    assert [item["text"]["content"] for item in actions] == [
        "Meego",
        "Figma",
        "需求文档",
    ]
    assert [item["url"] for item in actions] == [
        "https://project.meego.cn/story/7",
        "https://www.figma.com/design/example",
        "https://docs.example.com/requirement/7",
    ]
