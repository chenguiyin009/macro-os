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


def test_send_alert_delegates_to_send_message(monkeypatch) -> None:
    """Regression guard: ``send_alert`` was previously CALLED (main.py error
    path) but never IMPLEMENTED — a latent AttributeError.  Lock it."""
    adapter = FeishuAdapter(webhook_url="https://example.com/webhook")
    captured: dict[str, object] = {}

    def fake_post(payload):
        captured["payload"] = payload
        return True

    monkeypatch.setattr(adapter, "_post", fake_post)

    assert adapter.send_alert("pipeline boom") is True
    payload = captured["payload"]
    assert payload["card"]["header"]["title"]["content"] == "[CRITICAL_ALERT]"
    assert payload["card"]["elements"][0]["text"]["content"] == "pipeline boom"
