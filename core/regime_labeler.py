"""Macro OS — Ground truth regime labeler.

Labels are derived from forward-looking market data only.
Used exclusively for evaluation — never for live decisions.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from core.schemas import RegimeType


def compute_max_drawdown(prices: List[float]) -> float:
    """Compute maximum drawdown from a price series.

    Args:
        prices: List of prices in chronological order.

    Returns:
        Maximum drawdown as a negative percentage (e.g. -0.05 for -5%).
    """
    if len(prices) < 2:
        return 0.0

    peak = prices[0]
    max_dd = 0.0

    for p in prices[1:]:
        if p > peak:
            peak = p
        dd = (p - peak) / peak
        if dd < max_dd:
            max_dd = dd

    return max_dd


def compute_realized_volatility(prices: List[float], annualize: bool = False) -> float:
    """Compute realized volatility from daily log returns.

    Args:
        prices: List of prices in chronological order.
        annualize: If True, multiply by sqrt(252).

    Returns:
        Realized volatility as a decimal (e.g. 0.25 for 25%).
    """
    if len(prices) < 2:
        return 0.0

    log_returns: List[float] = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0 and prices[i] > 0:
            log_returns.append(math.log(prices[i] / prices[i - 1]))

    if not log_returns:
        return 0.0

    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / len(log_returns)
    vol = math.sqrt(variance)

    if annualize:
        vol *= math.sqrt(252)

    return vol


def label_regime(
    future_prices: List[float],
    drawdown_threshold: float = -0.05,
    volatility_threshold: float = 0.03,
) -> Dict[str, Any]:
    """Label the ground-truth regime from forward-looking price data.

    Args:
        future_prices: Forward prices (minimum ~21 for 20d lookback).
        drawdown_threshold: Max drawdown threshold for RISK_OFF (e.g. -0.05).
        volatility_threshold: Daily volatility threshold for TIGHT_LIQUIDITY.

    Returns:
        Dict with keys: 'regime' (str), 'max_drawdown' (float),
        'realized_vol' (float), 'horizon_days' (int).
    """
    max_dd = compute_max_drawdown(future_prices)
    realized_vol = compute_realized_volatility(future_prices)

    if max_dd < drawdown_threshold:
        regime = RegimeType.LIQUIDITY_SQUEEZE
    elif realized_vol > volatility_threshold:
        regime = RegimeType.TIGHT_LIQUIDITY
    elif max_dd >= -0.02 and realized_vol <= volatility_threshold * 0.6:
        regime = RegimeType.RISK_ON
    else:
        regime = RegimeType.TRANSITION

    return {
        "regime": regime.value,
        "max_drawdown": round(max_dd, 6),
        "realized_vol": round(realized_vol, 6),
        "horizon_days": len(future_prices) - 1,
    }
