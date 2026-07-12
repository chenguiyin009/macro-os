"""Phase 2 回归测试：Trinity OS v2.2.1 回测框架（滑点/手续费修订版）。

锁定：
  * 端到端跑通：StructureParser + RiskGateway 逐 bar 驱动；
  * 滑点方向（buy 加、sell 减）；
  * 自动派生 ATR（输入无 atr 列时）；
  * **win_rate / profit_factor 修复回归**：基于完整 round-trip 净盈亏，而非逐笔
    trades 误判（原 bug 会让所有 exits 全判胜 -> win_rate 恒为 1）；
  * 空行情不崩溃、指标归零。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.backtester import BacktestResult, SimpleBacktester, compute_atr
from core.risk_gateway_v2_2_1 import RiskGateway
from core.structure_parser import StructureParser


def _mk_df(n: int = 80, seed: int = 1):
    rng = np.random.default_rng(seed)
    prices = []
    p = 100.0
    for _ in range(n):
        p += rng.normal(0.2, 1.0)
        prices.append(p)
    return pd.DataFrame(
        {
            "open": prices,
            "high": [x + 1.0 for x in prices],
            "low": [x - 1.0 for x in prices],
            "close": prices,
        }
    )


def test_backtester_runs_end_to_end():
    df = _mk_df(80)
    bt = SimpleBacktester()
    res = bt.run(df, StructureParser(), RiskGateway())
    assert isinstance(res, BacktestResult)
    assert set(res.metrics.keys()) == {
        "total_return_pct",
        "max_drawdown_pct",
        "sharpe_ratio",
        "win_rate",
        "profit_factor",
        "total_trades",
        "final_equity",
    }
    assert len(res.equity_curve) == len(df) - 30  # 主循环从 i=30 起
    assert res.metrics["total_trades"] >= 0


def test_slippage_direction():
    bt = SimpleBacktester()
    buy = bt._apply_slippage(100.0, 2.0, is_buy=True)
    sell = bt._apply_slippage(100.0, 2.0, is_buy=False)
    assert buy > 100.0
    assert sell < 100.0


def test_compute_atr_first_value():
    df = _mk_df(30)
    atr = compute_atr(df, 14)
    assert abs(atr.iloc[0] - (df["high"].iloc[0] - df["low"].iloc[0])) < 1e-9


def test_win_rate_uses_round_trips_not_per_trade():
    # 2 胜 1 负 -> win_rate = 2/3，profit_factor = 300/50
    bt = SimpleBacktester()
    bt.initial_capital = 1_000_000.0
    bt.equity_curve = [1_000_000.0, 1_100_000.0]
    bt.round_trips = [100.0, -50.0, 200.0]
    bt.trades = [
        {"type": "initial_entry", "cost": 100.0},
        {"type": "partial_exit", "proceeds": 200.0},
    ]
    report = bt._generate_report()
    # win_rate 四舍五入到 4 位：round(2/3,4)=0.6667
    assert abs(report.metrics["win_rate"] - 2 / 3) < 1e-3
    assert report.metrics["profit_factor"] == 300.0 / 50.0


def test_win_rate_bug_regression_not_all_wins():
    # 原 bug：exits 全判胜 -> win_rate=1.0；修复后单笔亏损 round trip 应为 0.0
    bt = SimpleBacktester()
    bt.initial_capital = 1_000_000.0
    bt.equity_curve = [1_000_000.0, 900_000.0]
    bt.round_trips = [-100.0]
    bt.trades = [{"type": "partial_exit", "proceeds": 50.0}]
    report = bt._generate_report()
    assert report.metrics["win_rate"] == 0.0
    assert report.metrics["profit_factor"] == 0.0  # 无盈利 -> inf 取 0


def test_backtester_empty_df_no_crash():
    df = pd.DataFrame(columns=["open", "high", "low", "close"])
    bt = SimpleBacktester()
    res = bt.run(df, StructureParser(), RiskGateway())
    assert res.metrics["total_trades"] == 0
    assert len(res.equity_curve) == 0
