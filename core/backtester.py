"""Trinity OS v2.2.1 — Phase 2 回测框架 (SimpleBacktester)。

WHY THIS FILE EXISTS
--------------------
风险网关 Phase 1 的 demo 是手搓 `MarketState` 推演。Phase 2 需要一条**可回归**的
回测链路，把 `StructureParser` + 上游 `SpacetimeEngine` + `RiskGateway` 串起来，
对历史 OHLC 跑出可量化的绩效指标。链接原型里 `SimpleBacktester` 的 `_build_state`
是 `pass`、equity 曲线直接等于不变 capital（错误）；这里给出**真实可运行**版本。

设计决策（原链接未定义，显式记录）：
  * ATR：若输入 df 无 `atr` 列，用 Wilder 平滑自动派生（period=14），使回测可直接吃
    原始 OHLC。
  * 持仓记账：回测器内部维护 `_book`（每笔建仓的 entry/remaining），与 `RiskGateway`
    内部持仓保持同向（均从最新头寸递减），从而能计算每个 tick 的未实现盈亏。
  * 盈亏：建仓记录成本；`trailing_exit` 时按 `exit_size` 从最新 lot 递减，已实现
    盈亏 = Σ(deduct × (exit_price − entry))，计入 capital；每根 bar 的 equity =
    capital + 未实现盈亏。
  * 指标：final_equity / total_return / max_drawdown / sharpe / trade_count。

纯增量、零耦合：仅依赖 numpy/pandas + 同目录 `risk_gateway_v2_2_1` 与
`structure_parser`，不触碰 runtime/main 等主流程。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import logging
import numpy as np
import pandas as pd

from core.risk_gateway_v2_2_1 import MarketState, RiskGateway
from core.structure_parser import StructureParser

logger = logging.getLogger("Trinity.Backtester")


@runtime_checkable
class SpacetimeScorer(Protocol):
    """上游时空评分引擎协议；calculate(df, idx) -> float ∈ (0, 1]。"""

    def calculate(self, df: pd.DataFrame, idx: int) -> float:  # pragma: no cover - protocol
        ...


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder 平滑 ATR；输入需含 high/low/close。"""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(close)
    if n == 0:
        return pd.Series(dtype=float, name="atr")
    tr = np.empty(n, dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    atr = np.empty(n, dtype=float)
    atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return pd.Series(atr, index=df.index, name="atr")


class SimpleBacktester:
    """对 `RiskGateway` 做单标的、逐 bar 回测，输出绩效指标。"""

    def __init__(self, initial_capital: float = 1_000_000.0) -> None:
        self.initial_capital = float(initial_capital)
        self.capital = self.initial_capital
        self.gateway = RiskGateway()
        self.trades: List[Dict[str, Any]] = []
        self.equity_curve: List[float] = []
        self._book: List[Dict[str, float]] = []

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _ensure_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        if "atr" in df.columns:
            return df
        out = df.copy()
        out["atr"] = compute_atr(out, period=14)
        return out

    def _build_state(
        self,
        df: pd.DataFrame,
        i: int,
        parser: StructureParser,
        scorer: SpacetimeScorer,
        macro_series: Optional[List[Dict[str, float]]],
    ) -> MarketState:
        struct = parser.parse(df, i)
        score = float(scorer.calculate(df, i))
        row = df.iloc[i]
        macro = macro_series[i] if (macro_series is not None and i < len(macro_series)) else {}
        d3 = struct["d3_low"]
        if d3 is None:
            d3 = float(row["low"])
        return MarketState(
            current_price=float(row["close"]),
            d3_low=d3,
            atr=float(row["atr"]) if "atr" in df.columns else float(row.get("atr", 0.0)),
            spacetime_score=score,
            j1_confirmed=struct["j1_confirmed"],
            macro_vix=float(macro.get("vix", 20.0)),
            macro_commodity_shock=float(macro.get("brent_shock", 0.0)),
        )

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run(
        self,
        df: pd.DataFrame,
        structure_parser: StructureParser,
        spacetime_engine: SpacetimeScorer,
        macro_series: Optional[List[Dict[str, float]]] = None,
    ) -> Dict[str, float]:
        self.capital = self.initial_capital
        self.gateway = RiskGateway()
        self.trades = []
        self.equity_curve = []
        self._book = []

        df = self._ensure_atr(df)
        n = len(df)
        for i in range(n):
            state = self._build_state(df, i, structure_parser, spacetime_engine, macro_series)
            log = self.gateway.process_tick(self.capital, state)
            action = log.get("action")

            if action in ("initial_entry", "pyramid_add"):
                pos = log.get("position")
                if pos is not None:
                    self._book.append(
                        {"entry": float(pos.entry_price), "remaining": float(pos.size)}
                    )
                    self.trades.append(
                        {
                            "idx": i,
                            "action": action,
                            "price": state.current_price,
                            "size": pos.size,
                        }
                    )
            elif action == "trailing_exit":
                exit_size = float(log.get("exit_size", 0.0))
                exit_price = state.current_price
                remaining = exit_size
                realized = 0.0
                while remaining > 1e-9 and self._book:
                    lot = self._book[-1]
                    deduct = min(lot["remaining"], remaining)
                    realized += deduct * (exit_price - lot["entry"])
                    lot["remaining"] -= deduct
                    remaining -= deduct
                    if lot["remaining"] <= 1e-9:
                        self._book.pop()
                self.capital += realized
                self.trades.append(
                    {
                        "idx": i,
                        "action": action,
                        "price": exit_price,
                        "size": exit_size,
                        "realized_pnl": realized,
                    }
                )

            unreal = sum(
                lot["remaining"] * (state.current_price - lot["entry"]) for lot in self._book
            )
            self.equity_curve.append(self.capital + unreal)

        return self._calculate_metrics()

    # ------------------------------------------------------------------
    # 指标
    # ------------------------------------------------------------------
    def _calculate_metrics(self) -> Dict[str, float]:
        if not self.equity_curve:
            return {
                "final_equity": self.initial_capital,
                "total_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "trade_count": 0.0,
            }
        eq = pd.Series(self.equity_curve, dtype=float)
        returns = eq.pct_change().dropna()
        with np.errstate(divide="ignore", invalid="ignore"):
            peak = eq.cummax()
            dd = eq / peak - 1.0
        max_dd = float(dd.min()) if len(dd) else 0.0
        sharpe = (
            float(returns.mean() / returns.std() * np.sqrt(252))
            if returns.std() > 0
            else 0.0
        )
        return {
            "final_equity": float(eq.iloc[-1]),
            "total_return": float(eq.iloc[-1] / eq.iloc[0] - 1.0),
            "max_drawdown": max_dd,
            "sharpe": sharpe,
            "trade_count": float(len(self.trades)),
        }


if __name__ == "__main__":  # pragma: no cover - 手动 sanity
    rng = np.random.default_rng(7)
    prices = []
    p = 100.0
    for _ in range(120):
        p += rng.normal(0.2, 1.0)
        prices.append(p)
    demo = pd.DataFrame(
        {
            "open": prices,
            "high": [x + 1.0 for x in prices],
            "low": [x - 1.0 for x in prices],
            "close": prices,
        }
    )

    class _Scorer:
        def calculate(self, df, idx):
            return 0.9

    bt = SimpleBacktester(initial_capital=1_000_000.0)
    metrics = bt.run(demo, StructureParser(lookback=20), _Scorer())
    print("Backtest metrics:", metrics)
