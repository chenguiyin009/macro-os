"""Macro OS - Pure-function regime classifier.

This module is STRICTLY pure: no IO, no global state, no caching.
Given features and a config object, it returns a regime label.
"""

from __future__ import annotations

from typing import Any, Dict

from core.schemas import RegimeType


def compute_regime(features: Dict[str, Any], config: Dict[str, Any]) -> str:
    """Classify macro regime from feature vector."""
    regime_cfg = config.get("regime", {})

    dxy = features.get("dxy")
    vix = features.get("vix")
    tips_yield = features.get("tips_yield")
    hy_credit_spread = features.get("hy_credit_spread")

    # LIQUIDITY_SQUEEZE takes highest priority (safety-first)
    if vix is not None and vix >= regime_cfg.get("liquidity_squeeze", {}).get("vix_min", 25.0):
        return RegimeType.LIQUIDITY_SQUEEZE.value

    if hy_credit_spread is not None:
        hy_min = regime_cfg.get("liquidity_squeeze", {}).get("hy_credit_spread_min", 400)
        if hy_credit_spread >= hy_min:
            return RegimeType.LIQUIDITY_SQUEEZE.value

    # RISK_ON prefers relative momentum / divergence, with absolute fallback
    risk_on_cfg = regime_cfg.get("risk_on", {})
    tips_roc = features.get("tips_yield_roc_60d")
    dxy_zscore = features.get("dxy_zscore_60d")

    tips_roc_threshold = risk_on_cfg.get("tips_yield_roc_60d_max")
    dxy_zscore_threshold = risk_on_cfg.get("dxy_zscore_60d_max")
    has_relative_inputs = (
        tips_roc is not None
        and tips_roc_threshold is not None
        and dxy_zscore is not None
        and dxy_zscore_threshold is not None
    )

    if has_relative_inputs:
        if tips_roc <= tips_roc_threshold and dxy_zscore <= dxy_zscore_threshold:
            return RegimeType.RISK_ON.value

    # TIGHT_LIQUIDITY
    tips_ok = tips_yield is not None and tips_yield >= regime_cfg.get("tight_liquidity", {}).get("tips_yield_min", 0.8)
    dxy_ok = dxy is not None and dxy >= regime_cfg.get("tight_liquidity", {}).get("dxy_min", 103.0)
    if tips_ok and dxy_ok:
        return RegimeType.TIGHT_LIQUIDITY.value

    if not has_relative_inputs:
        tips_low = tips_yield is not None and tips_yield <= risk_on_cfg.get("tips_yield_max", 0.5)
        dxy_low = dxy is not None and dxy <= risk_on_cfg.get("dxy_max", 100.0)
        if tips_low and dxy_low:
            return RegimeType.RISK_ON.value

    return RegimeType.TRANSITION.value
