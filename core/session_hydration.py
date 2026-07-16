"""Hydrate orchestrator session state from the event vault.

Restores previous_risk_budget / days_in_recovery / red_line_day_lock when possible.
Conservative defaults remain 0.0 / 0 when ledger has no usable RESEARCH_REPORT.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.schemas import Event

logger = logging.getLogger(__name__)


def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _extract_risk_budget(payload: Dict[str, Any]) -> Optional[float]:
    kd = payload.get("kernel_decision")
    if isinstance(kd, dict) and "risk_budget" in kd:
        try:
            return float(kd["risk_budget"])
        except (TypeError, ValueError):
            return None
    if "risk_budget" in payload:
        try:
            return float(payload["risk_budget"])
        except (TypeError, ValueError):
            return None
    return None


def _extract_recovery_flag(payload: Dict[str, Any]) -> bool:
    feats = payload.get("features") or {}
    if isinstance(feats, dict):
        return bool(feats.get("recovery_signal", False))
    return False


def _extract_phase(payload: Dict[str, Any]) -> str:
    phase = payload.get("divergence_phase") or ""
    if not phase and isinstance(payload.get("red_line"), dict):
        phase = payload["red_line"].get("phase_raw") or ""
    return str(phase or "")


def hydrate_session_state_from_events(
    events: List[Event],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Derive session state dict from chronological events.

    Returns keys compatible with Orchestrator.state:
      previous_risk_budget, days_in_recovery, red_line_day_lock (optional)
    """
    now = now or datetime.now(timezone.utc)
    state: Dict[str, Any] = {
        "previous_risk_budget": 0.0,
        "days_in_recovery": 0,
        "red_line_day_lock": None,
        "hydrated_from_event_id": None,
        "hydrated": False,
    }
    if not events:
        return state

    # Walk newest-first for last budget
    last_budget: Optional[float] = None
    last_event_id: Optional[str] = None
    for ev in reversed(events):
        if ev.event_type not in {"RESEARCH_REPORT", "DECISION"}:
            continue
        payload = ev.payload or {}
        budget = _extract_risk_budget(payload)
        if budget is None:
            continue
        last_budget = budget
        last_event_id = ev.event_id
        break

    if last_budget is not None:
        state["previous_risk_budget"] = max(0.0, min(1.0, float(last_budget)))
        state["hydrated_from_event_id"] = last_event_id
        state["hydrated"] = True

    # Approximate days_in_recovery: count trailing calendar days with recovery_signal
    # and phase in LATE/MID from newest backward until break.
    days = 0
    seen_dates = set()
    for ev in reversed(events):
        if ev.event_type not in {"RESEARCH_REPORT", "DECISION"}:
            continue
        payload = ev.payload or {}
        phase = _extract_phase(payload)
        recovering = _extract_recovery_flag(payload) and phase in {"LATE", "MID"}
        ts = _parse_ts(ev.ts)
        day_key = ts.date().isoformat() if ts else ev.ts[:10]
        if day_key in seen_dates:
            continue
        if not recovering:
            break
        seen_dates.add(day_key)
        days += 1
        if days >= 30:
            break
    state["days_in_recovery"] = days

    # Restore same-UTC-day red line lock if last red_line absolute_override today
    today = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    for ev in reversed(events):
        if ev.event_type != "RESEARCH_REPORT":
            continue
        payload = ev.payload or {}
        red = payload.get("red_line")
        if not isinstance(red, dict):
            continue
        ts = _parse_ts(ev.ts)
        if ts is None:
            continue
        day = ts.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if day != today:
            break
        if red.get("absolute_override") or red.get("triggered"):
            state["red_line_day_lock"] = {
                "day": day,
                "reason_code": red.get("reason_code") or "PHYSICAL_RED_LINE_DAY_LOCK",
                "forced_hard_regime": red.get("forced_hard_regime") or red.get("hard_regime_folded"),
                "triggered_lines": list(red.get("triggered_lines") or []),
            }
        break

    return state
