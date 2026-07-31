"""Sentiment data providers (Phase 0: mock / manual JSON)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Protocol

from core.sentiment.models import SentimentRawInput


class SentimentDataProvider(Protocol):
    def fetch(self, as_of: str, session: str) -> SentimentRawInput: ...


def _raw_from_dict(d: Dict[str, Any]) -> SentimentRawInput:
    keys = SentimentRawInput.__dataclass_fields__.keys()
    payload = {k: d.get(k) for k in keys if k in d}
    return SentimentRawInput(**payload)


class ManualJsonProvider:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch(self, as_of: str, session: str) -> SentimentRawInput:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            # pick matching as_of/session or first
            chosen = None
            for row in data:
                if row.get("as_of") == as_of and row.get("session", session) == session:
                    chosen = row
                    break
            if chosen is None:
                for row in data:
                    if row.get("as_of") == as_of:
                        chosen = row
                        break
            if chosen is None:
                chosen = data[0]
            data = chosen
        data.setdefault("as_of", as_of)
        data.setdefault("session", session)
        data.setdefault("source", "MANUAL")
        return _raw_from_dict(data)


class MockScenarioProvider:
    """Built-in 07-17 / 07-21 golden scenarios."""

    SCENARIOS: Dict[str, Dict[str, Any]] = {
        "2026-07-17": {
            "as_of": "2026-07-17",
            "session": "AFTERNOON",
            "source": "MOCK",
            "sse_last": 3742.0,
            "volume_thrust": -0.25,
            "advance_decline_tech": -0.55,
            "limit_stress": 0.72,
            "limit_down_count": 80,
            "gap_open_ret": -0.01,
            "gap_hold_score": 0.4,
            "intraday_path": "CHOP",
            "margin_top100_limit_down_n": 18,
            "margin_top100_open_board_n": 4,
            "margin_top100_new_limit_down_n": 12,
            "margin_list_as_of": "2026-07-17",
            "forced_selling_proxy": 0.8,
            "gjd_proxy_score": 0.55,
            "star_chinext_etf_bid": 0.35,
            "csi1000_liquidity_injection": 0.15,
            "intervention_hint": "DRAG_NOT_LIFT",
            "transmission_hs300": 0.7,
            "transmission_chinext": 0.25,
            "transmission_csi1000": 0.1,
            "transmission_lag_days": 0.0,
            "cross_section_corr_tech": 0.85,
            "theme_dispersion": 0.01,
            "sector_index_resonance": 0.15,
            "kr_semi_ret": -0.03,
            "us_mega_tech_path": "TREND_DOWN",
            "kr_a_divergence": "KR_WEAK_A_WEAKER",
            "drawdown_soxx": -0.30,
            "drawdown_kr_semi": -0.33,
            "drawdown_a_semi": -0.36,
            "t0_date": "2026-06-01",
            "no_mainline_hint": False,
        },
        "2026-07-21": {
            "as_of": "2026-07-21",
            "session": "MORNING",
            "source": "MOCK",
            "sse_last": 3755.0,
            "volume_thrust": -0.12,
            "advance_decline_tech": -0.25,
            "limit_stress": 0.35,
            "limit_down_count": 25,
            "gap_open_ret": 0.008,
            "gap_hold_score": 0.25,
            "intraday_path": "GAP_UP_FADE",
            "margin_top100_limit_down_n": 21,
            "margin_top100_open_board_n": 20,
            "margin_top100_new_limit_down_n": 3,
            "margin_list_as_of": "2026-07-21",
            "forced_selling_proxy": 0.45,
            "gjd_proxy_score": 0.5,
            "star_chinext_etf_bid": 0.7,
            "csi1000_liquidity_injection": 0.6,
            "intervention_hint": "DRAG_NOT_LIFT",
            "transmission_hs300": 0.8,
            "transmission_chinext": 0.75,
            "transmission_csi1000": 0.7,
            "transmission_lag_days": 2.0,
            "cross_section_corr_tech": 0.6,
            "theme_dispersion": 0.025,
            "sector_index_resonance": 0.3,
            "kr_semi_ret": 0.005,
            "us_mega_tech_path": "RALLY_FADE",
            "kr_a_divergence": "KR_STRONG_A_WEAK",
            "drawdown_soxx": -0.30,
            "drawdown_kr_semi": -0.32,
            "drawdown_a_semi": -0.35,
            "t0_date": "2026-06-01",
            "growth_etf_flow": 0.2,
            "no_mainline_hint": True,
        },
    }

    def fetch(self, as_of: str, session: str) -> SentimentRawInput:
        key = as_of[:10]
        if key not in self.SCENARIOS:
            # generic neutral mock
            d = {
                "as_of": as_of,
                "session": session,
                "source": "MOCK",
                "sse_last": 3800.0,
                "volume_thrust": 0.0,
                "advance_decline_tech": 0.0,
                "margin_top100_limit_down_n": 2,
                "margin_top100_open_board_n": 2,
                "margin_top100_new_limit_down_n": 0,
                "transmission_hs300": 0.4,
            }
        else:
            d = dict(self.SCENARIOS[key])
            d["session"] = session or d.get("session", "MANUAL")
        return _raw_from_dict(d)
