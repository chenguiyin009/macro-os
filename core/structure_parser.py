"""Trinity OS v2.2.1 — Phase 2 结构解析器 (StructureParser)。

WHY THIS FILE EXISTS
--------------------
风险网关 v2.2.1 的 Phase 1 只落地了 `risk_gateway_v2_2_1.py`（接受标准化的
`MarketState`，输出动作）。Phase 2 需要把"原始行情"翻译成 `MarketState`，
本模块就是这条链路的输入端：从 OHLC 序列中识别结构低点 (D3 low) 与 J-1 回抽确认。

链接原型里 `StructureParser` 的三个核心方法是 `pass` 占位；这里给出**真实可运行**
的实现，设计决策（原链接未定义）如下，全部显式记录以便审计与回归：

  * `_detect_pivots`：分形摆动低点。bar `i` 在其左右各 `pivot_{left,right}` 根
    窗口内，低点严格小于所有邻 bar 低点时，判定为摆动低点。
  * `_find_d3_low`：在最近 `lookback` 根 bar 的摆动低点中，取**最低**者作为结构
    防御低点（即"守住就不破"的参考位）。这是对"D3 低点"的可操作解释。
  * `_check_j1_pullback_confirmation`：J-1 回抽确认 = 上一根 bar 的 low 在容忍度
    `j1_tolerance` 内未下破 D3 结构低点（形成更高低点），且收阳（close > open）。

纯增量、零耦合：仅依赖 pandas + 同目录 `risk_gateway_v2_2_1.MarketState`。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.risk_gateway_v2_2_1 import MarketState


class StructureParser:
    """从 OHLC 序列解析结构低点与回抽确认，供 RiskGateway 构造 MarketState。"""

    def __init__(
        self,
        lookback: int = 20,
        pivot_left: int = 1,
        pivot_right: int = 1,
        j1_tolerance: float = 0.005,
    ) -> None:
        self.lookback = int(lookback)
        self.pivot_left = int(pivot_left)
        self.pivot_right = int(pivot_right)
        self.j1_tolerance = float(j1_tolerance)
        self.last_d3_low: Optional[float] = None
        self.last_structure_valid: bool = False

    # ------------------------------------------------------------------
    # 私有解析原语
    # ------------------------------------------------------------------
    def _detect_pivots(self, df_slice: pd.DataFrame) -> List[Tuple[int, float]]:
        """识别分形摆动低点：bar i 的 low 严格小于窗口内所有邻 bar 的 low。"""
        lows = df_slice["low"].to_numpy(dtype=float)
        n = len(lows)
        pivots: List[Tuple[int, float]] = []
        L, R = self.pivot_left, self.pivot_right
        for i in range(L, n - R):
            is_low = True
            for k in range(1, L + 1):
                if lows[i] >= lows[i - k]:
                    is_low = False
                    break
            if is_low:
                for k in range(1, R + 1):
                    if lows[i] >= lows[i + k]:
                        is_low = False
                        break
            if is_low:
                pivots.append((i, float(lows[i])))
        return pivots

    def _find_d3_low(
        self, pivots: List[Tuple[int, float]], current_idx: int
    ) -> Optional[float]:
        """最近 lookback 根 bar 内的摆动低点中取最低者作为结构防御低点。"""
        lo = current_idx - self.lookback
        recent = [price for (idx, price) in pivots if idx >= lo]
        if not recent:
            return None
        return min(recent)

    def _check_j1_pullback_confirmation(
        self, df: pd.DataFrame, idx: int, d3_low: Optional[float]
    ) -> bool:
        """J-1 回抽确认：上一根 bar 未下破结构（更高低点）且收阳。"""
        if idx < 1 or d3_low is None:
            return False
        prev = df.iloc[idx - 1]
        prev_low = float(prev["low"])
        prev_open = float(prev["open"])
        prev_close = float(prev["close"])
        held = prev_low >= d3_low * (1.0 - self.j1_tolerance)
        bullish = prev_close > prev_open
        return held and bullish

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def parse(self, df: pd.DataFrame, current_idx: int) -> Dict[str, Any]:
        """解析截至 current_idx 的结构信息。"""
        sl = df.iloc[: current_idx + 1]
        pivots = self._detect_pivots(sl)
        d3_low = self._find_d3_low(pivots, current_idx)
        j1_confirmed = (
            self._check_j1_pullback_confirmation(df, current_idx, d3_low)
            if d3_low is not None
            else False
        )
        self.last_d3_low = d3_low
        self.last_structure_valid = d3_low is not None
        return {
            "d3_low": d3_low,
            "j1_confirmed": j1_confirmed,
            "structure_valid": self.last_structure_valid,
        }


def build_market_state(
    parser: StructureParser,
    spacetime_engine: Any,
    df: pd.DataFrame,
    idx: int,
    macro_data: Optional[Dict[str, float]] = None,
) -> MarketState:
    """把原始行情 + 结构解析 + 时空评分 + 宏观数据，组装成 RiskGateway 的输入契约。

    `spacetime_engine` 需提供 `calculate(df, idx) -> float`（Phase 2 由上游注入；
    回测测试中以假对象注入）。当结构暂无有效 D3 低点时，回退为当根 bar 的 low。
    """
    macro_data = macro_data or {}
    struct = parser.parse(df, idx)
    score = float(spacetime_engine.calculate(df, idx))
    row = df.iloc[idx]
    d3 = struct["d3_low"]
    if d3 is None:
        d3 = float(row["low"])
    return MarketState(
        current_price=float(row["close"]),
        d3_low=d3,
        atr=float(row["atr"]) if "atr" in df.columns else 0.0,
        spacetime_score=score,
        j1_confirmed=struct["j1_confirmed"],
        macro_vix=float(macro_data.get("vix", 20.0)),
        macro_commodity_shock=float(macro_data.get("brent_shock", 0.0)),
    )
