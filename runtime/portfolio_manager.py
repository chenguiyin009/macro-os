"""Macro OS v4.7 - Portfolio Construction Pipeline.

Three-phase portfolio construction:
Phase 1: Macro Risk Budget (glen lines + divergence phase)
Phase 2: Micro Fracture Surgery (earnings + strike matrix)
Phase 3: Hard Constraints (position limits)
"""

from __future__ import annotations

from typing import Any, Dict, List

from config.settings import settings
from core.earnings_verifier import EarningsVerifier
from core.fracture_aware_sizer import FractureAwareSizer, STRIKE_MATRIX
from core.glen_red_lines import GlenRedLinesEvaluator, RedLineStatus


PHASE_BUDGETS = {"NONE": 1.0, "EARLY": 0.95, "MID": 0.70, "LATE": 0.40, "CRISIS": 0.0}


class PortfolioConstructionPipeline:
    def __init__(self, watchlist_config: dict | None = None) -> None:
        asset_config = watchlist_config
        if asset_config is None:
            asset_config = settings.watchlist.get("assets", {})
        elif "assets" in watchlist_config:
            asset_config = watchlist_config.get("assets", {})

        self.red_lines = GlenRedLinesEvaluator()
        self.earnings = EarningsVerifier()
        self.sizer = FractureAwareSizer(asset_config)
        self.hard_limits: Dict[str, float] = {"default": 0.30}
        for ticker, target in self.sizer.targets.items():
            if target.max_portfolio_weight is not None:
                self.hard_limits[ticker] = target.max_portfolio_weight

    def set_hard_limits(self, limits: Dict[str, float]) -> None:
        self.hard_limits.update(limits)

    def compute_risk_budget(self, div_phase: str, red: RedLineStatus) -> float:
        base = PHASE_BUDGETS.get(div_phase, 1.0)
        budget = base * red.severity_multiplier
        return max(0.0, min(1.0, budget))

    def run_pipeline(
        self,
        div_phase: str,
        fractures: List[str],
        features: Dict[str, Any],
        base_weights: Dict[str, float],
        current_date: str = "2026-07-01",
    ) -> Dict[str, float]:
        # Phase 1: Macro risk budget
        red = self.red_lines.evaluate(features)
        budget = self.compute_risk_budget(div_phase, red)
        if budget == 0.0:
            return {t: 0.0 for t in base_weights}

        # Phase 2a: Earnings filter
        is_ew = "2026-07-01" <= current_date <= "2026-08-15"
        temp = base_weights.copy()
        if is_ew:
            for t in list(temp.keys()):
                if not self.earnings.verify(t):
                    temp[t] *= 0.5

        # Phase 2b: Fracture strike matrix
        adj = self.sizer.adjust_weights(temp, fractures, div_phase, STRIKE_MATRIX)
        total = sum(adj.values())
        if total > 0:
            scaled = {k: (v / total) * budget for k, v in adj.items()}
        else:
            scaled = adj

        # Phase 3: Hard constraints
        final: Dict[str, float] = {}
        excess = 0.0
        for ticker, weight in scaled.items():
            limit = self.hard_limits.get(ticker, self.hard_limits["default"])
            if weight > limit:
                excess += weight - limit
                final[ticker] = limit
            else:
                final[ticker] = weight

        # Recycle excess to defensive anchor
        if excess > 0 and "SH_GOLD" in final:
            gold_limit = self.hard_limits.get("SH_GOLD", 0.50)
            space = gold_limit - final["SH_GOLD"]
            if space > 0:
                final["SH_GOLD"] += min(excess, space)

        return final
