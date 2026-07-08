from __future__ import annotations

from adapters.feishu import FeishuAdapter


def test_send_message_builds_interactive_card(monkeypatch) -> None:
    adapter = FeishuAdapter(webhook_url="https://example.com/webhook")
    captured: dict[str, object] = {}

    def fake_post(payload):
        captured["payload"] = payload
        return True

    monkeypatch.setattr(adapter, "_post", fake_post)

    assert adapter.send_message("Macro OS v5.0 Daily Report", "daily-plan") is True
    payload = captured["payload"]
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["title"]["content"] == "Macro OS v5.0 Daily Report"
    assert payload["card"]["elements"][0]["text"]["content"] == "daily-plan"
