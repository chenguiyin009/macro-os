"""Macro OS — Feishu notification adapter.

Sends structured decision cards to Feishu webhook.
Minimal mock implementation for local development.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from core.schemas import Decision

logger = logging.getLogger(__name__)


class FeishuAdapter:
    """Adapter for sending notifications to Feishu."""

    def __init__(self, webhook_url: Optional[str] = None) -> None:
        self.webhook_url = webhook_url

    def send_decision(self, decision: Decision, features_summary: str = "") -> bool:
        """Post a decision card to Feishu.

        Args:
            decision: Decision object to notify about.
            features_summary: Optional human-readable feature context.

        Returns:
            True if sent successfully (or mock), False on failure.
        """
        if not self.webhook_url:
            logger.info(
                "[Feishu Mock] Decision: %s (conf=%.3f, risk=%.3f, reason=%s)",
                decision.action.value,
                decision.confidence,
                decision.risk_score,
                decision.reason,
            )
            return True

        payload = self._build_card(decision, features_summary)
        return self._post(payload)


    def send_message(self, title: str, text: str) -> bool:
        if not self.webhook_url:
            logger.info('[Feishu Mock] %s: %s', title, text)
            return True

        payload = {
            'msg_type': 'interactive',
            'card': {
                'header': {
                    'title': {'tag': 'plain_text', 'content': title},
                    'template': 'blue',
                },
                'elements': [
                    {'tag': 'div', 'text': {'tag': 'lark_md', 'content': text}},
                ],
            },
        }
        return self._post(payload)

    def send_alert(self, message: str) -> bool:
        """Send a critical-alert notification from a single string.

        Thin wrapper over :meth:`send_message` so callers (the scheduler's
        crash alert in ``runtime/scheduler.py`` and the error path in
        ``runtime/main.py``) can raise an alert with one argument.

        NOTE: this method was previously *called* (e.g. ``main.py`` error
        path) but never *implemented* — a latent ``AttributeError`` in the
        very error path it was meant to guard.  Now implemented.
        """
        return self.send_message(title="[CRITICAL_ALERT]", text=message)

    def _build_card(self, decision: Decision, features_summary: str) -> Dict[str, Any]:
        """Build a Feishu interactive card payload."""
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"Macro OS — {decision.action.value}"},
                    "template": self._color_for_action(decision.action.value),
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**Regime:** {decision.regime.value}"}},
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**Risk Score:** {decision.risk_score:.3f}"}},
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**Confidence:** {decision.confidence:.3f}"}},
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**Reason:** {decision.reason}"}},
                    *([{"tag": "div", "text": {"tag": "lark_md", "content": features_summary}}] if features_summary else []),
                ],
            },
        }

    def _color_for_action(self, action: str) -> str:
        return {"LONG": "green", "SHORT": "red", "NO_TRADE": "grey", "REDUCE": "orange"}.get(
            action, "blue"
        )

    def _post(self, payload: Dict[str, Any]) -> bool:
        """HTTP POST to Feishu webhook."""
        import urllib.request
        import urllib.error

        if not self.webhook_url:
            logger.info("[Feishu Mock] would post: %s", json.dumps(payload, indent=2)[:200])
            return True

        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            logger.error("Feishu post failed: %s", e)
            return False

    def health(self) -> Dict[str, Any]:
        return {"webhook_configured": bool(self.webhook_url)}
