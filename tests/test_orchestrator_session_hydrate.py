"""Orchestrator hydrates previous_risk_budget from vault when enabled."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.schemas import Event
from runtime.orchestrator import Orchestrator


class FakeVault:
    def __init__(self, events):
        self._events = events

    def read_all(self):
        return self._events

    def count_events(self):
        return len(self._events)

    def append(self, event):
        return True


def test_orchestrator_hydrates_previous_risk_from_vault() -> None:
    events = [
        Event(
            source="MACRO_OS_V5",
            symbol="MACRO",
            event_type="RESEARCH_REPORT",
            ts="2026-07-15T12:00:00Z",
            payload={"kernel_decision": {"risk_budget": 0.35, "defense_budget": 0.65}},
        )
    ]
    orch = Orchestrator(
        tradingview=MagicMock(),
        vault=FakeVault(events),
        feishu=MagicMock(),
        futu=MagicMock(),
        config={"ENABLE_RISK_GATEWAY": False, "HYDRATE_SESSION_FROM_VAULT": True},
    )
    assert orch.state["previous_risk_budget"] == 0.35


def test_orchestrator_skips_hydrate_when_disabled() -> None:
    events = [
        Event(
            source="MACRO_OS_V5",
            symbol="MACRO",
            event_type="RESEARCH_REPORT",
            ts="2026-07-15T12:00:00Z",
            payload={"kernel_decision": {"risk_budget": 0.35}},
        )
    ]
    orch = Orchestrator(
        tradingview=MagicMock(),
        vault=FakeVault(events),
        feishu=MagicMock(),
        futu=MagicMock(),
        config={"ENABLE_RISK_GATEWAY": False, "HYDRATE_SESSION_FROM_VAULT": False},
    )
    assert orch.state["previous_risk_budget"] == 0.0
