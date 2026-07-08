"""Macro OS - Shadow Engine (Counterfactual Portfolio Simulator)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PortfolioSnapshot:
    name: str
    nav: float = 1.0
    daily_returns: List[float] = field(default_factory=list)
    max_nav: float = 1.0
    max_drawdown: float = 0.0

    def apply_return(self, daily_return: float) -> None:
        self.nav *= 1.0 + daily_return
        self.daily_returns.append(daily_return)
        if self.nav > self.max_nav:
            self.max_nav = self.nav
        dd = (self.nav - self.max_nav) / self.max_nav if self.max_nav > 0 else 0.0
        if dd < self.max_drawdown:
            self.max_drawdown = dd

    @property
    def total_return(self) -> float:
        return self.nav - 1.0


class ShadowEngine:
    def __init__(self) -> None:
        self.portfolios = {
            "baseline": PortfolioSnapshot(name="Baseline (Expected Risk)"),
            "aggressive": PortfolioSnapshot(name="Aggressive (80% QQQ)"),
            "conservative": PortfolioSnapshot(name="Conservative (10% QQQ)"),
        }
        self.days_run = 0
        self.previous_close: Optional[float] = None

    def update_daily(self, market_data, baseline_weights):
        qqq_close = market_data.get("qqq_close")
        qqq_return = 0.0
        if qqq_close is not None and self.previous_close not in (None, 0.0):
            qqq_return = (qqq_close / self.previous_close) - 1.0
        if qqq_close is not None:
            self.previous_close = qqq_close

        tech = baseline_weights.get(
            "QQQ",
            sum(v for k, v in baseline_weights.items() if k.startswith("TECH_")),
        )
        self.portfolios["baseline"].apply_return(tech * qqq_return)
        self.portfolios["aggressive"].apply_return(0.80 * qqq_return)
        self.portfolios["conservative"].apply_return(0.10 * qqq_return)
        self.days_run += 1

    def generate_counterfactual_report(self) -> str:
        if self.days_run == 0:
            return "> Engine collecting..."
        a = self.portfolios["aggressive"]
        b = self.portfolios["baseline"]
        c = self.portfolios["conservative"]
        avoided_dd = max(0.0, b.max_drawdown - a.max_drawdown)
        return f"""### Shadow Counterfactuals
Running days: {self.days_run}

| Portfolio | Return | Max DD |
|---|---|---|
| {a.name} | {a.total_return:+.2%} | {a.max_drawdown:.2%} |
| **{b.name}** | **{b.total_return:+.2%}** | **{b.max_drawdown:.2%}** |
| {c.name} | {c.total_return:+.2%} | {c.max_drawdown:.2%} |

> Avoided **{avoided_dd:.2%}** extreme DD.
"""
