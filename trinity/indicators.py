"""Trinity OS v2.1 - 指标引擎

纯函数实现 MACD / MA / Bollinger Bands，无副作用，便于测试。
所有函数接收 List[float] 或 List[OHLCV]，返回等长序列（前序不足处填 None）。
"""
from __future__ import annotations

import math
from typing import Optional

from trinity.context import OHLCV


# ========== 移动平均 ==========

def sma(values: list[float], period: int) -> list[Optional[float]]:
    """简单移动平均 (SMA)

    返回等长序列，前 period-1 个位置为 None。
    """
    if period <= 0:
        raise ValueError(f"period 必须 > 0, got {period}")
    result: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return result
    window_sum = 0.0
    for i in range(len(values)):
        window_sum += values[i]
        if i >= period:
            window_sum -= values[i - period]
        if i >= period - 1:
            result[i] = window_sum / period
    return result


def ema(values: list[float], period: int) -> list[Optional[float]]:
    """指数移动平均 (EMA)

    种子值使用前 period 个值的 SMA，之后递推。
    """
    if period <= 0:
        raise ValueError(f"period 必须 > 0, got {period}")
    result: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return result
    # 种子: 前 period 个值的 SMA
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    multiplier = 2.0 / (period + 1)
    for i in range(period, len(values)):
        result[i] = values[i] * multiplier + result[i - 1] * (1 - multiplier)
    return result


# ========== MACD ==========

def calc_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    """计算 MACD 三线

    返回 (dif, dea, hist):
      - dif:  快线 = EMA(fast) - EMA(slow)
      - dea:  信号线 = EMA(dif, signal)
      - hist: 柱状 = (dif - dea) * 2  (国内通用的 *2 约定)

    定义参考: 《三位一体交易系统集合》2.2-2.4
    """
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)

    dif: list[Optional[float]] = [None] * len(closes)
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            dif[i] = ema_fast[i] - ema_slow[i]

    # DEA = EMA(dif, signal), 但 dif 前 slow-1 个为 None
    # 取 dif 中非 None 部分
    dif_valid_start = None
    for i in range(len(dif)):
        if dif[i] is not None:
            dif_valid_start = i
            break
    if dif_valid_start is None:
        return dif, [None] * len(closes), [None] * len(closes)

    dif_series = [dif[i] for i in range(dif_valid_start, len(closes))]
    dea_valid = ema(dif_series, signal)

    dea: list[Optional[float]] = [None] * len(closes)
    hist: list[Optional[float]] = [None] * len(closes)
    for j, val in enumerate(dea_valid):
        idx = dif_valid_start + j
        dea[idx] = val
        if val is not None and dif[idx] is not None:
            hist[idx] = (dif[idx] - val) * 2

    return dif, dea, hist


# ========== 布林带 ==========

def calc_bollinger(
    closes: list[float],
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    """计算布林带

    返回 (upper, mid, lower):
      - mid:   SMA(period)
      - upper: mid + num_std * std
      - lower: mid - num_std * std
    """
    mid = sma(closes, period)
    upper: list[Optional[float]] = [None] * len(closes)
    lower: list[Optional[float]] = [None] * len(closes)

    if len(closes) < period:
        return upper, mid, lower

    for i in range(period - 1, len(closes)):
        if mid[i] is None:
            continue
        window = closes[i - period + 1 : i + 1]
        mean = mid[i]
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std

    return upper, mid, lower


# ========== 批量计算辅助 ==========

def compute_indicators(
    ohlcv: list[OHLCV],
    ma_periods: tuple[int, ...] = (55, 233),
    macd_params: tuple[int, int, int] = (12, 26, 9),
    boll_params: tuple[int, float] = (20, 2.0),
) -> dict:
    """一次性计算全部指标，返回字典

    供 StateMachine / StructureParser / SpacetimeEngine 共享使用。
    """
    closes = [bar.close for bar in ohlcv]

    result: dict = {"closes": closes}

    # 均线
    for p in ma_periods:
        result[f"ma{p}"] = sma(closes, p)

    # MACD
    dif, dea, hist = calc_macd(closes, *macd_params)
    result["dif"] = dif
    result["dea"] = dea
    result["macd_hist"] = hist

    # 布林带
    upper, mid, lower = calc_bollinger(closes, *boll_params)
    result["boll_upper"] = upper
    result["boll_mid"] = mid
    result["boll_lower"] = lower

    return result


def last_valid(series: list[Optional[float]]) -> Optional[float]:
    """取序列中最后一个非 None 值"""
    for v in reversed(series):
        if v is not None:
            return v
    return None


def value_at(series: list[Optional[float]], index: int) -> Optional[float]:
    """安全取指定索引值，越界返回 None"""
    if 0 <= index < len(series):
        return series[index]
    return None
