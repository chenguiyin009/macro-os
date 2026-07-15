"""Macro OS - Policy Engine (LEGACY / not on research critical path).

WARNING: Explicit legacy module under Decision Authority Map v5 (section 7/Q4).
Macro budget choke point is core.decision_kernel.decide only.
Do not wire this module as a second constitution in runtime.orchestrator.
Useful allocation knobs should migrate to thresholds SSOT or L4 sizers;
this file remains for tests/compat until removed.

Original purpose: expected-risk allocation helper (not the L3 decide kernel).
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from core.schemas import RegimeName, AuthorityLevel, KernelDecision
from config.config_loader import constraints

logger = logging.getLogger(__name__)


def compute_expected_risk_budget(
    regime_probs: Dict[RegimeName, float],
    features: Any,
) -> Dict[str, float]:
    """
    L3 core validation logic.
    Computes expected risk and outputs target YAML dict allocation.
    """
    danger_score = getattr(features, "danger_score", 0.0)

    # 1. Read YAML physical red-line caps
    caps = {
        RegimeName.AI_EXPANSION: constraints.portfolio_limits.get("max_equity_exposure", 0.80),
        RegimeName.NARROW_LEADERSHIP: constraints.portfolio_limits.get("max_single_stock_weight", 0.60),
        RegimeName.FAST_LIQUIDITY_SHOCK: constraints.portfolio_limits.get("min_cash_buffer", 0.20),
        RegimeName.CASH_LIQUIDATION: constraints.constitution.get("qqq_hard_cap_crisis", 0.10),
    }

    # 2. Core math: Expected Risk Budget
    expected_budget = sum(regime_probs.get(regime, 0.0) * cap for regime, cap in caps.items())

    auth_level = AuthorityLevel.SOFT_POLICY
    reason = "Expected risk allocation from probabilistic matrix"

    # 3. Absolute physical red-line circuit breaker (Hard Veto)
    crisis_threshold = constraints.constitution.get("danger_crisis_threshold", 75)
    if danger_score >= crisis_threshold:
        logger.critical("HARD VETO: Danger %s >= %s", danger_score, crisis_threshold)
        expected_budget = caps[RegimeName.CASH_LIQUIDATION]
        auth_level = AuthorityLevel.HARD_VETO
        reason = f"Danger Score {danger_score} triggered forced de-leveraging"

    # Clamp to global absolute cap
    final_equity = min(expected_budget, caps[RegimeName.AI_EXPANSION])
    final_cash = 1.0 - final_equity

    logger.info("Expected Risk: EV=%.2f, Auth=%s", final_equity, auth_level.value)

    return {
        "QQQ": round(final_equity, 4),
        "CASH": round(final_cash, 4),
    }

class PolicyEngine:
    def __init__(self, constitution_config=None) -> None:
        self.constitution_config = constitution_config or {}

    def execute_constitution(
        self,
        current_allocation: Dict[str, float],
        market_snapshot: Dict[str, Any],
        target_allocation: Dict[str, float],
    ) -> Dict[str, Any]:
        red_lines = self.constitution_config.get("red_lines", {})
        execution = self.constitution_config.get("execution", {})

        max_daily_turnover = float(execution.get("max_daily_turnover", 0.10))
        min_cash_buffer = float(execution.get("min_cash_buffer", 0.20))
        core_pce_max = float(red_lines.get("core_pce_max", 4.0))
        vix_escape_hatch = float(red_lines.get("vix_escape_hatch", 35.0))

        vix = float(market_snapshot.get("VIX", market_snapshot.get("vix", 0.0)) or 0.0)
        core_pce = float(market_snapshot.get("core_pce", 0.0) or 0.0)

        final_target = dict(target_allocation)
        risky_keys = [key for key in final_target if key.upper() != "CASH"]

        for key in risky_keys:
            final_target[key] = min(float(final_target.get(key, 0.0)), max_daily_turnover)

        if core_pce > core_pce_max or vix >= vix_escape_hatch:
            for key in risky_keys:
                final_target[key] = min(final_target[key], max_daily_turnover)

        risky_total = sum(v for k, v in final_target.items() if k.upper() != "CASH")
        final_target["CASH"] = round(max(min_cash_buffer, 1.0 - risky_total), 4)

        total = sum(final_target.values())
        if total > 1.0:
            scale = 1.0 / total
            for key in list(final_target.keys()):
                final_target[key] = round(final_target[key] * scale, 4)

        return {
            "target_allocation": final_target,
            "market_snapshot": dict(market_snapshot),
            "current_allocation": dict(current_allocation),
        }
