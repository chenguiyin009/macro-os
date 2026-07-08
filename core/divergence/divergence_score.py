"""Macro OS v4.5 - Divergence score computation.

Measures structural divergence across macro axes.
Score: 0.0 (no divergence) to 1.0 (full crisis).
"""

from __future__ import annotations
from typing import Any, Dict


def compute_divergence_score(macro_state: Any, features: Dict[str, Any], lag_window_days: int = 30, macro_crack_age: int = 0, pine_data_available: bool = True, pine_degradation_factor: float = 0.85) -> float:
    """Compute divergence score from MacroState + features.
    
    Components:
    - Rate vs Credit conflict: +0.4
    - Gold vs Credit decoupling: +0.3
    - Dollar regime mismatch: +0.2
    - Volatility false panic filter: -0.1
    """
    score = 0.0
    fracture = "NONE"

    tips_t = macro_state.tips_trend
    dxy_t = macro_state.dxy_trend
    gold_t = macro_state.gold_trend
    credit_t = macro_state.credit_trend
    cc = getattr(macro_state, 'credit_confidence', 1.0)
    vix = features.get("vix", 20.0)

    # Rate vs Credit conflict (scaled by credit confidence)
    if tips_t > 0 and credit_t < 0:
        score += 0.4 * cc
        fracture = "CREDIT_LED"

    # Gold vs Credit decoupling (scaled by credit confidence)
    if gold_t > 0 and credit_t < 0:
        score += 0.3 * cc

    # Dollar regime mismatch (dxy up + gold up = unusual)
    if dxy_t > 0 and gold_t > 0:
        score += 0.2

    # Volatility false panic filter
    if vix < 15 and score > 0.5:
        score -= 0.1

    # Volume-Price Divergence (Lag Confirmation)
    volume = features.get("volume")
    vol_20d_avg = features.get("volume_20d_avg") 
    price_change = features.get("price_change_5d_pct", 0)
    volume_div_active = False
    if volume is not None and vol_20d_avg is not None and vol_20d_avg > 0:
        vol_ratio = volume / vol_20d_avg
        if 0 < vol_ratio < 0.85 and price_change > 0 and score > 0.50:
            score = min(1.0, score + min(0.15, score * 0.15))
            volume_div_active = True
    
    # Proxy Degradation Factor: when Pine indicator data is unavailable,
    # scale score by degradation factor to prevent false CRISIS from missing dimensions
    if not pine_data_available and pine_degradation_factor < 1.0:
        score = score * pine_degradation_factor
    
    return max(0.0, min(1.0, score))
