"""Trinity OS v2.2.1 — Phase 2 结构解析器 (StructureParser)，修订版（对齐 Grok 链接规格）。

WHY THIS FILE EXISTS
--------------------
v2.2.1 风险网关需要"原始行情 -> MarketState"的翻译层。本文件是链接给出的
**权威修订规格**：用 D 结构（H-L-H-L + D3 破 D1）识别结构低点，并做 J-1 回抽确认
与 ATR 有效性过滤。相对上一轮自实现的"最低近期摆动低点"版本，本算法更严格、更接近
实盘结构定义，故据此对齐（上一轮实现整体替换）。

设计要点（来自链接规格）：
  * `_detect_pivots`：2-bar 窗口内的 H/L 分形（high/low 严格大于左右各 2 根）。
  * `_validate_d_structure`：取最近 4 个 pivot，必须为 H-L-H-L，且 D3 低点严格低于
    D1 低点，否则结构不成立。
  * `_check_j1_pullback_confirmation`：当前 low 高于 D3 结构低点（守住），且最近 5 根
    内出现过更低低点（发生过回抽）。
  * `parse`：idx<20 或 pivot 不足 4 个时返回 structure_valid=False；并要求 atr>0。

纯增量、零耦合：仅依赖 pandas（+ dataclasses），不 import 任何 Trinity 其它模块。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import pandas as pd


@dataclass
class StructureResult:
    d3_low: Optional[float]
    j1_confirmed: bool
    structure_valid: bool
    last_pivot_low: Optional[float] = None


class StructureParser:
    """从 OHLC 序列解析 D 结构与 J-1 回抽确认。"""

    def __init__(self, atr_period: int = 14, atr_filter_mult: float = 0.8) -> None:
        self.atr_period = int(atr_period)
        self.atr_filter_mult = float(atr_filter_mult)

    def parse(self, df: pd.DataFrame, current_idx: int) -> StructureResult:
        if current_idx < 20:
            return StructureResult(d3_low=None, j1_confirmed=False, structure_valid=False)

        window = df.iloc[: current_idx + 1].copy()
        pivots = self._detect_pivots(window)
        if len(pivots) < 4:
            return StructureResult(d3_low=None, j1_confirmed=False, structure_valid=False)

        d3_low = self._validate_d_structure(pivots)
        if d3_low is None:
            return StructureResult(d3_low=None, j1_confirmed=False, structure_valid=False)

        j1_confirmed = self._check_j1_pullback_confirmation(window, d3_low)
        current_atr = float(window["atr"].iloc[-1])
        structure_valid = (float(window["low"].iloc[-1]) > d3_low) and (current_atr > 0)

        return StructureResult(
            d3_low=d3_low,
            j1_confirmed=j1_confirmed,
            structure_valid=structure_valid,
            last_pivot_low=pivots[-1][1] if pivots else None,
        )

    def _detect_pivots(self, df: pd.DataFrame) -> List[Tuple[str, float, int]]:
        pivots: List[Tuple[str, float, int]] = []
        n = len(df)
        for i in range(2, n - 2):
            high = float(df["high"].iloc[i])
            low = float(df["low"].iloc[i])
            if (
                high > df["high"].iloc[i - 2 : i].max()
                and high > df["high"].iloc[i + 1 : i + 3].max()
            ):
                pivots.append(("H", high, i))
            if (
                low < df["low"].iloc[i - 2 : i].min()
                and low < df["low"].iloc[i + 1 : i + 3].min()
            ):
                pivots.append(("L", low, i))
        return pivots

    def _validate_d_structure(self, pivots: List[Tuple[str, float, int]]) -> Optional[float]:
        if len(pivots) < 4:
            return None
        recent = pivots[-4:]
        types = [p[0] for p in recent]
        if types != ["H", "L", "H", "L"]:
            return None
        d1_low = recent[1][1]
        d3_low = recent[3][1]
        if d3_low >= d1_low:
            return None
        return d3_low

    def _check_j1_pullback_confirmation(self, df: pd.DataFrame, d3_low: float) -> bool:
        recent_lows = df["low"].iloc[-5:]
        return (float(df["low"].iloc[-1]) > d3_low) and (
            float(recent_lows.min()) < float(df["low"].iloc[-5])
        )
