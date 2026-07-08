"""Macro OS ? Probabilistic Regime Classifier (Softmax Engine).

Based on heuristics anchored to 2023-2026 historical extreme points.
"""

from __future__ import annotations

import math
import logging
from typing import Dict

from core.schemas import FeatureSchema, RegimeName

logger = logging.getLogger(__name__)

# Historical anchor points
VIX_CENTER, DXY_CENTER, OVX_CENTER = 20.0, 102.0, 30.0
# Expert weights
W_VIX, W_DXY, W_OVX = 0.50, 0.30, 0.20
# Sigmoid mapping params
SIGMOID_MIDPOINT, SIGMOID_STEEPNESS = 0.50, 0.25

def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    else:
        exp_x = math.exp(x)
        return exp_x / (1.0 + exp_x)

def compute_crisis_stress_index(vix: float, dxy: float, ovx: float) -> float:
    """Compute Crisis Stress Index (CSI)."""
    vix_term = W_VIX * (vix - VIX_CENTER) / 10.0
    dxy_term = W_DXY * (dxy - DXY_CENTER) / 5.0
    ovx_term = W_OVX * (ovx - OVX_CENTER) / 10.0
    return vix_term + dxy_term + ovx_term

def compute_regime_probabilities(features: FeatureSchema) -> Dict[RegimeName, float]:
    """Map macro features to 4-regime probability tensor."""
    vix = features.vix if features.vix is not None else VIX_CENTER
    dxy = features.dxy if features.dxy is not None else DXY_CENTER
    ovx = features.ovx if features.ovx is not None else OVX_CENTER
    csi = compute_crisis_stress_index(vix, dxy, ovx)
    p_crisis_total = _sigmoid((csi - SIGMOID_MIDPOINT) / SIGMOID_STEEPNESS)
    p_cash_liq = p_crisis_total * 0.60
    p_fast_shock = p_crisis_total * 0.40
    remaining = 1.0 - p_crisis_total
    dxy_signal = (dxy - DXY_CENTER) / 5.0
    p_narrow = remaining * _sigmoid(dxy_signal) * 0.70
    p_expansion = remaining - p_narrow
    probs = {
        RegimeName.AI_EXPANSION: max(0.0, p_expansion),
        RegimeName.NARROW_LEADERSHIP: max(0.0, p_narrow),
        RegimeName.FAST_LIQUIDITY_SHOCK: max(0.0, p_fast_shock),
        RegimeName.CASH_LIQUIDATION: max(0.0, p_cash_liq),
    }
    total = sum(probs.values())
    probs = {k: round(v / total, 4) for k, v in probs.items()}
    logger.info("Softmax Matrix: CSI=%.2f | Probs=%s", csi, {k.value: v for k, v in probs.items()})
    return probs
