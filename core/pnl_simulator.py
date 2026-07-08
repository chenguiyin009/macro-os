"""Macro OS — PnL simulator with transaction cost model.

MUST include:
- spread cost (per-trade entry/exit)
- slippage cost (liquidity-dependent)
- regime switching penalty (prevents over-trading)
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from core.schemas import DecisionAction


class PnLSimulator:
    """Simulate PnL from decision stream with realistic costs."""

    def __init__(
        self,
        spread_bps: float = 1.0,
        slippage_bps: float = 2.0,
        regime_switch_penalty_bps: float = 5.0,
    ) -> None:
        self.spread_bps = spread_bps
        self.slippage_bps = slippage_bps
        self.regime_switch_penalty_bps = regime_switch_penalty_bps

    def simulate(
        self,
        decisions: List[Dict[str, Any]],
        market_returns: List[float],
    ) -> Dict[str, Any]:
        """Run PnL simulation over paired decision/return streams.

        Args:
            decisions: List of dicts with 'action' (DecisionAction str)
                       and 'confidence' (float).
            market_returns: List of period returns matching decision count.

        Returns:
            Dict with keys: gross_pnl, net_pnl, sharpe, total_costs_bps,
            trade_count, trade_log.
        """
        if len(decisions) != len(market_returns):
            raise ValueError(
                f"Decision count ({len(decisions)}) != "
                f"return count ({len(market_returns)})"
            )

        gross_pnl = 0.0
        total_costs_bps = 0.0
        trade_log: List[Dict[str, Any]] = []
        prev_action: Optional[str] = None
        position = 0.0  # 0 = neutral, 1 = long, -1 = short

        for i, (dec, ret) in enumerate(zip(decisions, market_returns)):
            action = dec.get("action", "NO_TRADE")
            confidence = dec.get("confidence", 0.0)

            # Determine target position from action
            target = self._action_to_position(action, confidence)

            # Transaction cost on position change
            trade_cost_bps = 0.0
            if target != position:
                trade_cost_bps = self.spread_bps + self.slippage_bps
                total_costs_bps += trade_cost_bps

            # Regime switching penalty
            if action != prev_action and prev_action is not None:
                total_costs_bps += self.regime_switch_penalty_bps
                trade_cost_bps += self.regime_switch_penalty_bps

            # PnL contribution
            period_pnl = position * ret if position != 0 else 0.0
            gross_pnl += period_pnl

            trade_log.append({
                "period": i,
                "action": action,
                "position": position,
                "target": target,
                "market_return": round(ret, 6),
                "period_pnl": round(period_pnl, 6),
                "costs_bps": round(trade_cost_bps, 4),
            })

            position = target
            prev_action = action

        # Net PnL: convert costs from bps to fraction
        net_pnl = gross_pnl - total_costs_bps / 10000.0

        # Compute Sharpe ratio
        returns = [t["period_pnl"] for t in trade_log]
        sharpe = self._compute_sharpe(returns)

        return {
            "gross_pnl": round(gross_pnl, 6),
            "net_pnl": round(net_pnl, 6),
            "sharpe": round(sharpe, 4),
            "total_costs_bps": round(total_costs_bps, 2),
            "trade_count": sum(1 for t in trade_log if t["costs_bps"] > 0),
            "trade_log": trade_log,
        }

    def _action_to_position(self, action: str, confidence: float) -> float:
        if action == DecisionAction.LONG.value:
            return 1.0 * min(confidence * 1.5, 1.0)
        elif action == DecisionAction.SHORT.value:
            return -1.0 * min(confidence * 1.5, 1.0)
        elif action == DecisionAction.REDUCE.value:
            return 0.5 if confidence > 0.3 else 0.0
        else:
            return 0.0

    @staticmethod
    def _compute_sharpe(returns: List[float], risk_free: float = 0.0) -> float:
        if len(returns) < 2:
            return 0.0
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        if variance <= 0:
            return 0.0
        std = math.sqrt(variance)
        return (mean_ret - risk_free) / std * math.sqrt(252)

    def cost_breakdown(self) -> Dict[str, float]:
        """Return per-component cost configuration."""
        return {
            "spread_bps": self.spread_bps,
            "slippage_bps": self.slippage_bps,
            "regime_switch_penalty_bps": self.regime_switch_penalty_bps,
        }
