"""Macro OS v4.6 - FractureAwareSizer + STRIKE_MATRIX."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml


STRIKE_MATRIX = {
    "CREDIT_LED": ["CREDIT"],
    "HYPER_HAWKISH_SHOCK": ["RATES", "HIGH_BETA"],
    "RECESSION_PRICING": ["RATES", "CREDIT"],
    "MULTI_FRONT_RESONANCE": ["CREDIT", "RATES", "HIGH_BETA"],
}


@dataclass
class TargetDimension:
    ticker: str
    macro_sensitivity: List[str]
    moat_score: float = 0.5
    logic_stability: float = 0.5
    has_active_catalyst: bool = False
    atr_percent_20d: float = 0.02
    beta_to_spy: float = 1.0
    max_portfolio_weight: Optional[float] = None


class FractureAwareSizer:
    def __init__(self, watchlist_config: Optional[Dict[str, dict]] = None) -> None:
        self.targets: Dict[str, TargetDimension] = {}
        if watchlist_config:
            for ticker, meta in watchlist_config.items():
                self.targets[ticker] = TargetDimension(ticker=ticker, **meta)

    @classmethod
    def from_yaml(cls, path: Path) -> "FractureAwareSizer":
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(watchlist_config=data.get("assets", {}))

    def adjust_weights(self, base_weights, fractures, phase, strike_matrix=None):
        sm = strike_matrix or STRIKE_MATRIX
        is_multi = "MULTI_FRONT_RESONANCE" in fractures
        adj = base_weights.copy()

        for ticker, weight in base_weights.items():
            if ticker not in self.targets:
                continue
            t = self.targets[ticker]
            penalty = 1.0
            for fracture in fractures:
                affected = sm.get(fracture, [])
                overlap = set(t.macro_sensitivity) & set(affected)
                if "CREDIT" in overlap:
                    penalty *= 0.5 + 0.5 * t.moat_score
                if "RATES" in overlap:
                    penalty *= 0.4 + 0.6 * t.logic_stability
                if "HIGH_BETA" in overlap and not t.has_active_catalyst:
                    penalty *= 0.7
            if is_multi and not t.has_active_catalyst:
                penalty *= 0.8
            if t.atr_percent_20d > 0:
                penalty *= 1.0 / (1.0 + (t.atr_percent_20d * 20.0))
            if t.beta_to_spy > 1.0:
                penalty *= 1.0 / (1.0 + ((t.beta_to_spy - 1.0) * 0.75))
            adj[ticker] = weight * penalty

        total = sum(adj.values())
        if total <= 0:
            return adj

        adj = {ticker: weight / total for ticker, weight in adj.items()}

        caps = {
            ticker: target.max_portfolio_weight
            for ticker, target in self.targets.items()
            if target.max_portfolio_weight is not None
        }
        if caps:
            for _ in range(len(adj)):
                overflow = 0.0
                capped_tickers = set()
                for ticker, cap in caps.items():
                    if ticker in adj and adj[ticker] > cap:
                        overflow += adj[ticker] - cap
                        adj[ticker] = cap
                        capped_tickers.add(ticker)
                if overflow <= 1e-12:
                    break
                room_tickers = [ticker for ticker in adj if ticker not in capped_tickers]
                if not room_tickers:
                    break
                room_total = sum(adj[ticker] for ticker in room_tickers)
                if room_total <= 0:
                    break
                for ticker in room_tickers:
                    adj[ticker] += overflow * (adj[ticker] / room_total)

        return {ticker: round(weight, 4) for ticker, weight in adj.items()}
