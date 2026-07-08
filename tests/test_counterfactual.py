"""Tests for counterfactual decision engine."""

from __future__ import annotations

from core.counterfactual import (
    simulate_all_decisions, find_optimal_decision,
    compute_decision_opportunity_cost, counterfactual_summary,
)


class TestSimulateAllDecisions:
    def test_returns_four_decisions(self) -> None:
        results = simulate_all_decisions(0.01)
        assert len(results) == 8

    def test_sorted_by_pnl(self) -> None:
        results = simulate_all_decisions(0.01)
        for i in range(len(results) - 1):
            assert results[i].predicted_pnl >= results[i + 1].predicted_pnl

    def test_long_best_on_up_market(self) -> None:
        results = simulate_all_decisions(0.02, current_confidence=0.8)
        best = find_optimal_decision(results)
        assert best.decision == "LONG"

    def test_short_best_on_down_market(self) -> None:
        results = simulate_all_decisions(-0.02, current_confidence=0.8)
        best = find_optimal_decision(results)
        assert best.decision == "SHORT"

    def test_no_trade_best_on_zero_return_high_conf(self) -> None:
        results = simulate_all_decisions(0.0, current_confidence=0.8)
        best = find_optimal_decision(results)
        assert best.decision in ("NO_TRADE", "NEUTRAL")


class TestOpportunityCost:
    def test_optimal_choice_zero_cost(self) -> None:
        results = simulate_all_decisions(0.01)
        optimal = find_optimal_decision(results)
        cost = compute_decision_opportunity_cost(optimal.decision, results)
        assert abs(cost) < 0.001

    def test_suboptimal_positive_cost(self) -> None:
        results = simulate_all_decisions(0.01, current_confidence=0.8)
        cost = compute_decision_opportunity_cost("NO_TRADE", results)
        assert cost >= 0.0


class TestCounterfactualSummary:
    def test_single_step(self) -> None:
        cf = simulate_all_decisions(0.01, current_action="NO_TRADE")
        s = counterfactual_summary([cf], ["NO_TRADE"])
        assert "total_opportunity_cost" in s
        assert "avg_opportunity_cost" in s
        assert "optimal_alignment_rate" in s

    def test_multiple_steps(self) -> None:
        cfs = [
            simulate_all_decisions(0.02, current_confidence=0.8),
            simulate_all_decisions(-0.01, current_confidence=0.7),
        ]
        actions = ["LONG", "NO_TRADE"]
        s = counterfactual_summary(cfs, actions)
        assert s["optimal_alignment_rate"] >= 0.0
        assert s["optimal_alignment_rate"] <= 100.0
