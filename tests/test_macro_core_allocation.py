"""Regression tests for runtime allocation handling."""

from __future__ import annotations

import pytest

from runtime.macro_core import AllocationItem, LLMProposal, RegimeName, allocation_engine


def test_allocation_engine_preserves_cash_when_underallocated() -> None:
    proposal = LLMProposal(
        regime_identified=RegimeName.NARROW_LEADERSHIP,
        macro_narrative="underallocated",
        allocations=[AllocationItem(asset="QQQ", target_weight=0.1, confidence=0.7, rebalance_days=3)],
    )
    result, violations = allocation_engine(
        proposal,
        {"weights": {"CASH": 1.0}},
        {"nq_macro_layer": {"danger_score": 0}, "fragility_layer": {"fragility_score": 0}, "timestamp": "2026-07-03T00:00:00Z"},
    )

    target = result["target_allocation"]
    assert target["QQQ"] == pytest.approx(0.1)
    assert target["CASH"] == pytest.approx(0.9)
    assert sum(target.values()) == pytest.approx(1.0)
    assert violations == 0


def test_allocation_engine_caps_equity_group_across_spy_and_qqq() -> None:
    proposal = LLMProposal(
        regime_identified=RegimeName.NARROW_LEADERSHIP,
        macro_narrative="equity cap",
        allocations=[
            AllocationItem(asset="QQQ", target_weight=0.2, confidence=0.7, rebalance_days=3),
            AllocationItem(asset="SPY", target_weight=0.7, confidence=0.7, rebalance_days=3),
            AllocationItem(asset="CASH", target_weight=0.1, confidence=0.7, rebalance_days=3),
        ],
    )
    result, violations = allocation_engine(
        proposal,
        {"weights": {"QQQ": 0.2, "SPY": 0.7, "CASH": 0.1}},
        {"nq_macro_layer": {"danger_score": 0}, "fragility_layer": {"fragility_score": 0}, "timestamp": "2026-07-03T00:00:00Z"},
    )

    target = result["target_allocation"]
    assert target["QQQ"] + target["SPY"] == pytest.approx(0.8)
    assert target["CASH"] == pytest.approx(0.2)
    assert sum(target.values()) == pytest.approx(1.0)
    assert violations == 0
