"""Session hydration from vault events."""

from __future__ import annotations

from datetime import datetime, timezone

from core.schemas import Event
from core.session_hydration import hydrate_session_state_from_events


def test_hydrate_previous_risk_budget_from_research_report() -> None:
    events = [
        Event(
            source="MACRO_OS_V5",
            symbol="MACRO",
            event_type="RESEARCH_REPORT",
            ts="2026-07-15T12:00:00Z",
            payload={
                "kernel_decision": {"risk_budget": 0.2, "defense_budget": 0.8},
                "features": {"recovery_signal": False},
                "divergence_phase": "EARLY",
            },
        )
    ]
    state = hydrate_session_state_from_events(events)
    assert state["hydrated"] is True
    assert state["previous_risk_budget"] == 0.2
    assert state["days_in_recovery"] == 0


def test_hydrate_defaults_when_empty() -> None:
    state = hydrate_session_state_from_events([])
    assert state["previous_risk_budget"] == 0.0
    assert state["hydrated"] is False


def test_hydrate_red_line_day_lock_same_utc_day() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    events = [
        Event(
            source="MACRO_OS_V5",
            symbol="MACRO",
            event_type="RESEARCH_REPORT",
            ts=f"{today}T01:00:00Z",
            payload={
                "kernel_decision": {"risk_budget": 0.0},
                "red_line": {
                    "triggered": True,
                    "absolute_override": True,
                    "reason_code": "PHYSICAL_RED_LINE_VIX_ESCAPE_HATCH",
                    "forced_hard_regime": "LIQUIDITY_SQUEEZE",
                    "triggered_lines": ["vix_escape_hatch"],
                },
            },
        )
    ]
    state = hydrate_session_state_from_events(events)
    assert state["red_line_day_lock"] is not None
    assert state["red_line_day_lock"]["day"] == today
    assert state["previous_risk_budget"] == 0.0
