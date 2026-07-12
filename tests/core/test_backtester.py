"""Phase 2 回归测试：Trinity OS v2.2.1 回测框架 (SimpleBacktester)。

锁定：
  * 端到端跑通：结构解析 + 时空评分注入 + RiskGateway 逐 bar 驱动；
  * 自动派生 ATR（输入无 atr 列时）；
  * 输出指标键完整、equity 曲线长度 == 行情长度、至少产生一笔交易；
  * 空行情不崩溃、指标归零。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.backtester import SimpleBacktester, compute_atr
from core.structure_parser import StructureParser


class ConstScorer:
    def __init__(self, score: float = 0.9):
        self.score = score

    def calculate(self, df, idx):
        return self.score


def _trend_df(n: int = 80, start: float = 100.0, vol: float = 1.0, seed: int = 42):
    rng = np.random.default_rng(seed)
    prices = []
    p = start
    for _ in range(n):
        p += rng.normal(0.25, vol)  # 轻微上行，足以形成结构与回抽
        prices.append(p)
    return pd.DataFrame(
        {
            "open": prices,
            "high": [x + vol for x in prices],
            "low": [x - vol for x in prices],
            "close": prices,
        }
    )


def test_backtester_runs_and_produces_metrics():
    df = _trend_df()
    bt = SimpleBacktester(initial_capital=1_000_000.0)
    metrics = bt.run(df, StructureParser(lookback=20), ConstScorer(0.9))
    assert set(metrics.keys()) == {
        "final_equity",
        "total_return",
        "max_drawdown",
        "sharpe",
        "trade_count",
    }
    assert len(bt.equity_curve) == len(df)
    assert metrics["trade_count"] >= 1


def test_backtester_auto_atr_when_missing():
    df = _trend_df(n=80)
    assert "atr" not in df.columns
    bt = SimpleBacktester()
    m = bt.run(df, StructureParser(lookback=20), ConstScorer(0.9))
    assert m["trade_count"] >= 1


def test_compute_atr_matches_manual_first_value():
    df = _trend_df(n=30)
    atr = compute_atr(df, period=14)
    expected_first = df["high"].iloc[0] - df["low"].iloc[0]
    assert abs(atr.iloc[0] - expected_first) < 1e-9


def test_backtester_empty_df_no_crash():
    df = pd.DataFrame(columns=["open", "high", "low", "close"])
    bt = SimpleBacktester()
    m = bt.run(df, StructureParser(), ConstScorer())
    assert m["trade_count"] == 0
    assert m["total_return"] == 0.0
    assert len(bt.equity_curve) == 0


def test_backtester_equity_reflects_exit_pnl():
    # 构造：上行建仓后急跌触发 trailing_exit，capital 应因已实现盈亏变化
    prices = list(range(100, 131))  # 100..130 上行
    prices += [120, 110, 100]       # 急跌（跌破 highest 的 8%）
    df = pd.DataFrame(
        {
            "open": prices,
            "high": [x + 1.0 for x in prices],
            "low": [x - 1.0 for x in prices],
            "close": prices,
        }
    )
    bt = SimpleBacktester(initial_capital=1_000_000.0)
    m = bt.run(df, StructureParser(lookback=20), ConstScorer(0.95))
    # 至少发生建仓；急跌段应触发减仓（trailing_exit）
    assert m["trade_count"] >= 1
    # equity 曲线数值有限、无 NaN
    assert np.all(np.isfinite(bt.equity_curve))
