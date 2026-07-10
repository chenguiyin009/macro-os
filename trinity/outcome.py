"""Trinity OS v2.1 - OutcomeSimulator (蓝图阶段二)

性能仿真器：基于历史路径计算决策表现。

双窗口逻辑:
  窗口1 — 固定周期 (Fixed Horizon): 即时有效性, N 根 K 线后的收益/最大浮盈/最大浮亏
  窗口2 — MA55 动态跟踪 (Dynamic Trailing): 趋势持久性, 跌破 MA55 强制平仓

直接对接 EventSourcingTracker / AttributionEngine 的输出逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

from trinity.context import OHLCV
from trinity.indicators import sma


@dataclass
class OutcomeResult:
    """决策结果 — 双窗口收益统计

    固定窗口:
      fixed_return: 固定周期收益率
      fixed_mfe:    最大浮盈 (Max Favorable Excursion)
      fixed_mae:    最大浮亏 (Max Adverse Excursion)

    动态窗口 (MA55 跟踪):
      dynamic_return:     动态平仓收益率
      dynamic_mfe:        动态期间最大浮盈
      dynamic_mae:        动态期间最大浮亏
      dynamic_bars_held:  持仓 K 线数
    """
    # 窗口1: 固定周期
    fixed_return: float
    fixed_mfe: float
    fixed_mae: float

    # 窗口2: MA55 动态跟踪
    dynamic_return: float
    dynamic_mfe: float
    dynamic_mae: float
    dynamic_bars_held: int

    def to_dict(self) -> dict:
        return {
            "fixed_return": round(self.fixed_return, 6),
            "fixed_mfe": round(self.fixed_mfe, 6),
            "fixed_mae": round(self.fixed_mae, 6),
            "dynamic_return": round(self.dynamic_return, 6),
            "dynamic_mfe": round(self.dynamic_mfe, 6),
            "dynamic_mae": round(self.dynamic_mae, 6),
            "dynamic_bars_held": self.dynamic_bars_held,
        }

    @property
    def is_profitable(self) -> bool:
        """固定窗口是否盈利"""
        return self.fixed_return > 0

    @property
    def risk_reward_ratio(self) -> float:
        """固定窗口盈亏比 (MFE / |MAE|)"""
        if self.fixed_mae == 0:
            return float("inf") if self.fixed_mfe > 0 else 0.0
        return abs(self.fixed_mfe / self.fixed_mae)


class OutcomeSimulator:
    """性能仿真器：基于历史路径计算决策表现

    用法:
        sim = OutcomeSimulator()
        result = sim.simulate(entry_index=50, market_data=ohlcv, fixed_horizon=20)
        # 注入归因引擎
        attribution.update_outcome(decision_index, result.fixed_return)
    """

    def simulate(
        self,
        entry_index: int,
        market_data: List[OHLCV],
        fixed_horizon: int = 20,
        ma_period: int = 55,
    ) -> OutcomeResult:
        """计算入场后的双窗口收益

        Args:
            entry_index:  入场 K 线索引
            market_data:  完整市场数据 (OHLCV 列表)
            fixed_horizon: 固定窗口周期 (K 线数)
            ma_period:    动态跟踪均线周期 (默认 55)

        Returns:
            OutcomeResult 双窗口统计
        """
        if entry_index < 0 or entry_index >= len(market_data):
            return OutcomeResult(0, 0, 0, 0, 0, 0, 0)

        # 提取入场后的价格切片 (固定窗口)
        future_end = min(entry_index + fixed_horizon + 1, len(market_data))
        future_data = market_data[entry_index + 1 : future_end]
        if not future_data:
            return OutcomeResult(0, 0, 0, 0, 0, 0, 0)

        entry_price = market_data[entry_index].close
        if entry_price <= 0:
            return OutcomeResult(0, 0, 0, 0, 0, 0, 0)

        closes = [d.close for d in future_data]
        highs = [d.high for d in future_data]
        lows = [d.low for d in future_data]

        # === 1. 固定窗口统计 ===
        fixed_return = closes[-1] / entry_price - 1
        fixed_mfe = max(highs) / entry_price - 1
        fixed_mae = min(lows) / entry_price - 1

        # === 2. 动态 MA55 跟踪统计 ===
        # 策略: 跌破 MA55 强制平仓, 或达到固定窗口 2 倍后强制平仓
        full_closes = [d.close for d in market_data]
        ma_vals = sma(full_closes, ma_period)

        # 找到平仓 K 线
        exit_bar = entry_index + 1
        max_bars = fixed_horizon * 2
        for i in range(entry_index + 1, len(market_data)):
            bars_held = i - entry_index
            # 跌破 MA55 或超过最大持仓
            ma_val = ma_vals[i] if i < len(ma_vals) else None
            if ma_val is not None and market_data[i].close < ma_val:
                exit_bar = i
                break
            if bars_held >= max_bars:
                exit_bar = i
                break
            exit_bar = i

        # 计算动态区间表现
        dynamic_slice = market_data[entry_index + 1 : exit_bar + 1]
        if dynamic_slice:
            d_closes = [d.close for d in dynamic_slice]
            d_highs = [d.high for d in dynamic_slice]
            d_lows = [d.low for d in dynamic_slice]

            dynamic_return = d_closes[-1] / entry_price - 1
            dynamic_mfe = max(d_highs) / entry_price - 1
            dynamic_mae = min(d_lows) / entry_price - 1
            held_bars = len(dynamic_slice)
        else:
            dynamic_return = 0.0
            dynamic_mfe = 0.0
            dynamic_mae = 0.0
            held_bars = 0

        return OutcomeResult(
            fixed_return, fixed_mfe, fixed_mae,
            dynamic_return, dynamic_mfe, dynamic_mae, held_bars,
        )

    def simulate_batch(
        self,
        entry_indices: List[int],
        market_data: List[OHLCV],
        fixed_horizon: int = 20,
        ma_period: int = 55,
    ) -> List[OutcomeResult]:
        """批量计算多个入场点的收益"""
        return [
            self.simulate(idx, market_data, fixed_horizon, ma_period)
            for idx in entry_indices
        ]
