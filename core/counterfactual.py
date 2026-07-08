"""Macro OS v4.2 — Counterfactual decision engine.

Simulates all possible decisions at each step to answer:
"What would have happened if we chose differently?"
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from core.schemas import CounterfactualResult, DecisionAction


def simulate_pnl_for_decision(
    decision: str,
    market_return: float,
    confidence: float = 1.0,
    spread_bps: float = 1.0,
    slippage_bps: float = 2.0,
) -> float:
    """Simulate PnL for a single decision given market outcome.

    Args:
        decision: One of "LONG", "SHORT", "NO_TRADE", "REDUCE".
        market_return: Period return of the underlying asset.
        confidence: Signal confidence [0, 1].
        spread_bps: Entry/exit spread cost.
        slippage_bps: Slippage cost.

    Returns:
        Net PnL for this decision after costs.
    """
    position = _action_to_position(decision, confidence)
    gross_pnl = position * market_return

    # Cost: only if position != 0
    cost = 0.0
    if position != 0:
        cost = (spread_bps + slippage_bps) / 10000.0

    return gross_pnl - cost


def _action_to_position(action: str, confidence: float) -> float:
    if action in (DecisionAction.LONG.value, DecisionAction.AGGRESSIVE.value):
        return min(confidence * 1.5, 1.0)
    elif action in (DecisionAction.SHORT.value,):
        return -min(confidence * 1.5, 1.0)
    elif action in (DecisionAction.REDUCE.value, DecisionAction.DEFENSIVE.value, DecisionAction.RISK_REDUCE.value):
        return 0.5 if confidence > 0.3 else 0.0
    else:
        return 0.0


ALL_DECISIONS = [
    DecisionAction.LONG.value,
    DecisionAction.SHORT.value,
    DecisionAction.NO_TRADE.value,
    DecisionAction.REDUCE.value,
    DecisionAction.AGGRESSIVE.value,
    DecisionAction.DEFENSIVE.value,
    DecisionAction.NEUTRAL.value,
    DecisionAction.RISK_REDUCE.value,
]


def simulate_all_decisions(
    market_return: float,
    current_action: str = "NO_TRADE",
    current_confidence: float = 0.0,
    current_risk_score: float = 0.5,
    spread_bps: float = 1.0,
    slippage_bps: float = 2.0,
) -> List[CounterfactualResult]:
    """Simulate all possible decisions and compare outcomes.

    Args:
        market_return: Actual market return for this period.
        current_action: The decision actually taken.
        current_confidence: Confidence of actual decision.
        current_risk_score: Risk score at decision time.
        spread_bps: Spread cost in bps.
        slippage_bps: Slippage cost in bps.

    Returns:
        List of CounterfactualResult, sorted by predicted_pnl descending.
    """
    results = []
    for decision in ALL_DECISIONS:
        pnl = simulate_pnl_for_decision(
            decision, market_return,
            confidence=current_confidence,
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
        )
        results.append(CounterfactualResult(
            decision=decision,
            predicted_pnl=round(pnl, 6),
            confidence=current_confidence,
            risk_score=current_risk_score,
        ))

    results.sort(key=lambda r: r.predicted_pnl, reverse=True)
    return results


def find_optimal_decision(results: List[CounterfactualResult]) -> CounterfactualResult:
    """Return the decision with highest predicted PnL.

    Args:
        results: List from simulate_all_decisions().

    Returns:
        CounterfactualResult with highest PnL.
    """
    return max(results, key=lambda r: r.predicted_pnl)


def compute_decision_opportunity_cost(
    actual_action: str,
    results: List[CounterfactualResult],
) -> float:
    """Compute cost of not choosing the optimal decision.

    Args:
        actual_action: The action that was actually taken.
        results: List from simulate_all_decisions().

    Returns:
        Opportunity cost as a positive float (higher = worse decision).
    """
    optimal = find_optimal_decision(results)
    actual_pnl = next(
        (r.predicted_pnl for r in results if r.decision == actual_action),
        0.0,
    )
    return optimal.predicted_pnl - actual_pnl


def counterfactual_summary(
    counterfactuals: List[List[CounterfactualResult]],
    actual_actions: List[str],
) -> Dict[str, Any]:
    """Aggregate counterfactual analysis over multiple steps.

    Args:
        counterfactuals: List of per-step counterfactual results.
        actual_actions: List of actions actually taken.

    Returns:
        Dict with aggregated metrics.
    """
    total_opp_cost = 0.0
    optimal_alignment = 0

    for cf_list, actual in zip(counterfactuals, actual_actions):
        optimal = find_optimal_decision(cf_list)
        opp_cost = compute_decision_opportunity_cost(actual, cf_list)
        total_opp_cost += opp_cost
        if optimal.decision == actual:
            optimal_alignment += 1

    n = len(counterfactuals) or 1

    return {
        "total_opportunity_cost": round(total_opp_cost, 4),
        "avg_opportunity_cost": round(total_opp_cost / n, 4),
        "optimal_alignment_rate": round(optimal_alignment / n * 100, 1),
        "best_decision_frequency": _best_decision_frequency(counterfactuals),
    }


def _best_decision_frequency(
    counterfactuals: List[List[CounterfactualResult]],
) -> Dict[str, float]:
    """Count how often each decision was optimal."""
    counts: Dict[str, int] = {}
    for cf_list in counterfactuals:
        best = find_optimal_decision(cf_list)
        counts[best.decision] = counts.get(best.decision, 0) + 1

    total = len(counterfactuals) or 1
    return {
        k: round(v / total * 100, 1)
        for k, v in sorted(counts.items(), key=lambda x: -x[1])
    }
