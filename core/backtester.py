"""Trinity OS v2.2.1 — Phase 2 回测框架 (SimpleBacktester)，修订版（对齐 Grok 链接规格）。

WHY THIS FILE EXISTS
--------------------
v2.2.1 风险网关需要一条**可回归**的回测链路，把 StructureParser + RiskGateway 串起来。
本文件是链接给出的**权威修订规格**：加入 ATR 动态滑点、双边手续费、浮动盈亏权益曲线、
完整绩效指标（含 win_rate / profit_factor）。相对上一轮"理想成交"版本，本版更贴近实盘。

链接原文已明确标注一处 bug，本实现**直接修复**：
  * 原 `_generate_report` 用逐笔 trades 判定胜负——但建仓笔只有 cost、平仓笔只有
    proceeds，没有任何一笔同时具备二者，于是所有 exits 都被误判为胜（win_rate 恒为 1）。
  * 修复：引入 `round_trips`（每个头寸完整平仓时的净盈亏），win_rate / profit_factor
    基于 round-trip 已实现盈亏计算，符合"完整回合"语义。

设计要点：
  * 滑点：buy 加、sell 减，幅度 = atr * slippage_atr_mult。
  * 手续费：建仓成本含 (1+commission_rate)，平仓 proceeds 扣 (1-commission_rate)。
  * 盈亏记账：每个头寸记录 entry_cost 与累计 realized_proceeds；size 归零时结算一笔
    round-trip 净盈亏（proceeds - entry_cost）。
  * spacetime_score 设为可配参数（链接硬编码 0.87，这里参数化以便测试与复用）。
  * 输入无 atr 列时自动用 Wilder ATR(14) 派生。

纯增量、零耦合：仅依赖 numpy/pandas + 同目录 risk_gateway / structure_parser。
注意：每次 run 请传入一个**全新** RiskGateway 实例（本类不重置传入的网关状态）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import logging
import numpy as np
import pandas as pd

from core.risk_gateway_v2_2_1 import MarketState, RiskGateway
from core.structure_parser import StructureParser, StructureResult

logger = logging.getLogger("Trinity.Backtester")


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    metrics: Dict[str, float]
    trades: List[Dict[str, Any]]


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

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        commission_rate: float = 0.0003,
        slippage_atr_mult: float = 0.35,
        spacetime_score: float = 0.87,
    ) -> None:
        self.initial_capital = float(initial_capital)
        self.commission_rate = commission_rate
        self.slippage_atr_mult = slippage_atr_mult
        self.spacetime_score = spacetime_score
        self.cash = self.initial_capital
        self.positions: List[Dict[str, Any]] = []
        self.equity_curve: List[float] = []
        self.trades: List[Dict[str, Any]] = []
        self.round_trips: List[float] = []

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _ensure_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        if "atr" in df.columns:
            return df
        out = df.copy()
        out["atr"] = compute_atr(out, period=14)
        return out

    def _apply_slippage(self, price: float, atr: float, is_buy: bool) -> float:
        slippage = atr * self.slippage_atr_mult
        return price + slippage if is_buy else price - slippage

    def _calculate_equity(self, current_price: float) -> float:
        floating_pnl = 0.0
        for pos in self.positions:
            floating_pnl += (current_price - pos["entry_price"]) * pos["size"]
        return self.cash + floating_pnl

    def _reset(self) -> None:
        self.cash = self.initial_capital
        self.positions = []
        self.equity_curve = []
        self.trades = []
        self.round_trips = []

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run(
        self,
        df: pd.DataFrame,
        structure_parser: StructureParser,
        risk_gateway: RiskGateway,
    ) -> BacktestResult:
        self._reset()
        df = self._ensure_atr(df)
        for i in range(30, len(df)):
            row = df.iloc[i]
            struct: StructureResult = structure_parser.parse(df, i)
            state = MarketState(
                current_price=float(row["close"]),
                d3_low=struct.d3_low if struct.d3_low is not None else float(row["close"]) * 0.95,
                atr=float(row["atr"]) if "atr" in df.columns else float(row["close"]) * 0.02,
                spacetime_score=self.spacetime_score,
                j1_confirmed=struct.j1_confirmed,
                macro_vix=float(row.get("vix", 22.0)),
            )
            log = risk_gateway.process_tick(self.cash, state)
            self._execute_action(log, row, state)
            equity = self._calculate_equity(float(row["close"]))
            self.equity_curve.append(equity)
        return self._generate_report()

    # ------------------------------------------------------------------
    # 动作执行 + 记账
    # ------------------------------------------------------------------
    def _execute_action(self, log: Dict[str, Any], row: pd.Series, state: MarketState) -> None:
        action = log.get("action")
        if action in ("initial_entry", "pyramid_add"):
            intended_price = float(row["close"])
            exec_price = self._apply_slippage(intended_price, state.atr, is_buy=True)
            pos_obj = log.get("position")
            size = pos_obj.size if pos_obj is not None else 0.0
            if size > 0:
                cost = size * exec_price * (1 + self.commission_rate)
                if self.cash >= cost:
                    self.cash -= cost
                    self.positions.append(
                        {
                            "entry_price": exec_price,
                            "size": size,
                            "entry_cost": cost,
                            "realized_proceeds": 0.0,
                            "hard_stop": pos_obj.hard_stop if pos_obj is not None else exec_price * 0.95,
                        }
                    )
                    self.trades.append(
                        {"type": action, "price": exec_price, "size": size, "cost": cost}
                    )
        elif action == "trailing_exit":
            exit_size = log.get("exit_size", 0.0)
            if exit_size > 0 and self.positions:
                intended_price = float(row["close"])
                exec_price = self._apply_slippage(intended_price, state.atr, is_buy=False)
                remaining_to_exit = exit_size
                for pos in self.positions[:]:
                    if remaining_to_exit <= 0:
                        break
                    deduct = min(pos["size"], remaining_to_exit)
                    proceeds = deduct * exec_price * (1 - self.commission_rate)
                    self.cash += proceeds
                    pos["size"] -= deduct
                    pos["realized_proceeds"] += proceeds
                    remaining_to_exit -= deduct
                    self.trades.append(
                        {"type": "partial_exit", "price": exec_price, "size": deduct, "proceeds": proceeds}
                    )
                    if pos["size"] <= 1e-9:
                        # 完整 round-trip 结算：净盈亏 = 已实现 proceeds - 建仓成本
                        self.round_trips.append(pos["realized_proceeds"] - pos["entry_cost"])
                self.positions = [p for p in self.positions if p["size"] > 1e-9]

    # ------------------------------------------------------------------
    # 指标（已修复 win_rate / profit_factor 的逐笔误判 bug）
    # ------------------------------------------------------------------
    def _generate_report(self) -> BacktestResult:
        equity = pd.Series(self.equity_curve, dtype=float)
        if len(equity):
            returns = equity.pct_change().dropna()
            total_return = (equity.iloc[-1] / self.initial_capital - 1.0) * 100.0
            with np.errstate(divide="ignore", invalid="ignore"):
                peak = equity.cummax()
                max_dd = ((equity / peak - 1.0).min()) * 100.0
            sharpe = (
                float(returns.mean() / returns.std() * np.sqrt(252))
                if returns.std() > 0
                else 0.0
            )
            final_equity = float(equity.iloc[-1])
        else:
            total_return = 0.0
            max_dd = 0.0
            sharpe = 0.0
            final_equity = self.initial_capital

        # FIX: 用完整 round-trip 净盈亏判定胜负，而非逐笔 trades（原实现 exits 全判胜）
        wins = [p for p in self.round_trips if p > 0]
        losses = [p for p in self.round_trips if p < 0]
        win_rate = len(wins) / len(self.round_trips) if self.round_trips else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        metrics = {
            "total_return_pct": round(total_return, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 2),
            "total_trades": len(self.trades),
            "final_equity": round(final_equity, 2),
        }
        return BacktestResult(equity_curve=equity, metrics=metrics, trades=self.trades)


if __name__ == "__main__":  # pragma: no cover - 手动 sanity
    rng = np.random.default_rng(7)
    prices = []
    p = 100.0
    for _ in range(160):
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
    bt = SimpleBacktester(initial_capital=1_000_000.0)
    result = bt.run(demo, StructureParser(), RiskGateway())
    print("Backtest metrics:", result.metrics)
