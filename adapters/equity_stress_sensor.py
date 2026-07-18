"""Macro OS — Equity Stress Sensor (L1 Feature Producer).

负责计算科技板块（如 SOXX / QQQ）的微观结构压力，并应用「进档快、出档慢」的
不对称迟滞带（Hysteresis Band），消除震荡市中的同段抖动（Whipsawing）造成的
无谓交易摩擦。

架构纪律（v5.1 拍板）
--------------------
迟滞带 / 平滑 MUST 做在数据生产端（Adapter / L1），绝不动 `core.decision_kernel`
—— 内核保持无状态、纯映射（decide() 一行不改）。内核只认一个泛化字段
``tech_drawdown``（负分数，越负=股价跌得越深）；至于这个负分数是「原始回撤」还是
「迟滞带平滑后的回撤」，内核不关心。这样生产路径注入平滑值、回测路径注入同一
平滑值，行为完全一致。

数学解法：不对称的滚动极值平滑（Asymmetric Rolling Min-Max）
--------------------------------------------------------
1. 原始 20 日峰值回撤序列：``raw_dd = price / rolling_max(20) - 1``（<= 0）。
2. 对 ``raw_dd`` 取 ``N`` 天向下滚动极值：``smoothed = raw_dd.rolling(N).min()``。

因为回撤是负数（如 -0.15），``rolling(min)`` 意味着：只要过去 N 天内有过深跌，
系统就「记住」这个深跌，直到 N 天内都不再创出新低、且始终维持更高水位，警报才
逐级解除。这完美实现了「一旦跌破立刻触发，但反弹必须确认企稳 N 天后才解除限制」。

对比 EMA：不需要调 alpha、没有相位偏移、语义精确（=「N 日最坏回撤」），且对
kernel 完全透明。

Run:  python -m adapters.equity_stress_sensor   （内置 V 反验证）
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class EquityStressSensor:
    """微观结构压力传感器（不对称迟滞带）。

    :param lookback_window: 计算峰值回撤的基准窗口（默认 20 日）
    :param smoothing_lag_days: 迟滞带天数（反弹企稳确认期，默认 2 日；经长周期多环境 A/B 校准）
    """

    def __init__(self, lookback_window: int = 20, smoothing_lag_days: int = 2):
        if lookback_window < 1:
            raise ValueError("lookback_window must be >= 1")
        if smoothing_lag_days < 1:
            raise ValueError("smoothing_lag_days must be >= 1")
        self.lookback_window = lookback_window
        self.smoothing_lag_days = smoothing_lag_days

    # ------------------------------------------------------------------
    # 向量化：返回与输入对齐的完整平滑回撤序列（用于回测，无前视）
    # ------------------------------------------------------------------
    def smoothed_drawdown_series(self, price_series: pd.Series) -> pd.Series:
        """对整条价格序列计算「迟滞带平滑后的 20 日峰值回撤」序列。

        返回值在位置 t 仅依赖 price_series[:t+1]（滚动窗口只看过去），
        因此直接 slice 任意历史日即为「截至当日、无前视」的传感器读数。
        保留输入索引（日期对齐），调用方可直接 reindex 到特征帧。
        """
        price = pd.Series(price_series, dtype="float64")
        idx = price.index
        if len(price) < 2:
            return pd.Series([0.0] * len(price), index=idx)
        roll_max = price.rolling(window=self.lookback_window, min_periods=1).max()
        raw_dd = (price - roll_max) / roll_max
        smoothed = raw_dd.rolling(window=self.smoothing_lag_days, min_periods=1).min()
        return smoothed

    # ------------------------------------------------------------------
    # 点值：生产路径取「最新一日」平滑回撤（喂给内核）
    # ------------------------------------------------------------------
    def compute_smoothed_drawdown(self, price_series: pd.Series) -> float:
        """计算带有不对称迟滞带的最新技术面回撤（点值）。

        严格等价于 ``smoothed_drawdown_series(price_series).iloc[-1]``，
        保证生产路径（点值）与回测路径（序列）读数完全一致。
        若历史不足，返回 0.0（无压力信号，不会被误触发）。
        """
        series = self.smoothed_drawdown_series(price_series)
        if series.empty:
            return 0.0
        latest = float(series.iloc[-1])
        raw_latest = float(price_series.iloc[-1] if len(price_series) else 0.0)
        # 仅用于日志：原始回撤（对照用，不影响返回值）
        price = pd.Series(price_series, dtype="float64").reset_index(drop=True)
        if len(price) >= 2:
            roll_max = price.rolling(window=self.lookback_window, min_periods=1).max()
            raw_dd_latest = float(((price - roll_max) / roll_max).iloc[-1])
            if latest < raw_dd_latest - 1e-12:
                logger.debug(
                    "🛡️ 迟滞带生效: 原始回撤已反弹至 %.2f%%, 但系统为防假摔锁定在 %.2f%%",
                    raw_dd_latest * 100, latest * 100,
                )
        return latest

    # ------------------------------------------------------------------
    # 注入：把平滑后的 tech_drawdown 写入 features 字典
    # ------------------------------------------------------------------
    def inject_feature(self, features: dict, price_series: pd.Series) -> dict:
        """将清洗后的特征注入到 FeatureSchema 字典中。"""
        tech_dd = self.compute_smoothed_drawdown(price_series)
        features["tech_drawdown"] = tech_dd
        return features


# =====================================================================
# 快速逻辑验证 (Dry-run)：假摔 V 反
# =====================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    sensor = EquityStressSensor(lookback_window=20, smoothing_lag_days=3)

    # 模拟一次“假摔 V 反”：价格 100→99→98→88（深跌 -12%）→95（V 反 -5%）→96→101
    prices = pd.Series([100.0, 99.0, 98.0, 88.0, 95.0, 96.0, 101.0])

    print("\n=== 逐日模拟（点值口径，生产路径）===")
    for i in range(4, len(prices) + 1):
        sub = prices.iloc[:i]
        dd = sensor.compute_smoothed_drawdown(sub)
        print(f"Day {i} | 收盘价: {sub.iloc[-1]:.1f} | 平滑回撤(SOXX_DD): {dd*100:+.2f}%")

    print("\n=== 向量化序列口径（回测路径，无前视）===")
    full = sensor.smoothed_drawdown_series(prices)
    for i, dd in enumerate(full, start=1):
        print(f"Day {i} | 收盘价: {prices.iloc[i-1]:.1f} | 平滑回撤: {dd*100:+.2f}%")

    # 断言：深跌日(4)触发 -12%，V 反日(5,6)仍锁定 -12%（迟滞），Day7 企稳后才释放
    assert abs(full.iloc[3] - (-0.12)) < 1e-9, "Day4 应触发 -12%"
    assert abs(full.iloc[4] - (-0.12)) < 1e-9, "Day5 迟滞带应锁定 -12%（未解除）"
    assert abs(full.iloc[5] - (-0.12)) < 1e-9, "Day6 迟滞带应锁定 -12%（未解除）"
    print("\n✅ 不对称迟滞带验证通过：进档快、出档慢（需 N 日企稳才解除）。")
