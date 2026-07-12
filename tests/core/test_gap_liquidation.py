"""Phase 2 跳空强制全平专项回归测试（独立文件，PDF 规格要求）。

锁定：
  * 跳空低开（open < hard_stop）→ 逐个持仓检测 → FATAL_GAP_LIQUIDATION；
  * 盘中击穿（current_price < hard_stop）→ 同上；
  * 跳空强平返回字段完整性（reason / breached_stop / gap_price / liquidation_price）；
  * 回测器端真实撮合（卖空滑点 + 手续费 → 亏损 round_trip）；
  * 确定性数据框架（避免 Flaky Test，PDF 要求）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.backtester import SimpleBacktester
from core.risk_gateway_v2_2_1 import MarketState, RiskGateway
from core.structure_parser import StructureParser


# ------------------------------------------------------------------
# 确定性辅助数据
# ------------------------------------------------------------------
def _gap_breach_df() -> pd.DataFrame:
    """构造确定性跳空行情：前 50 根上行（触发建仓），第 51 根跳空低开击穿 hard_stop。"""
    n = 55
    idx = range(n)
    opens = [100.0 + i * 0.5 for i in idx]
    highs = [o + 2.0 for o in opens]
    lows = [o - 1.0 for o in opens]
    closes = [o + 0.3 for o in opens]
    # 第 51 根跳空低开
    opens[50] = 90.0
    highs[50] = 92.0
    lows[50] = 88.0
    closes[50] = 89.5
    # 后续保持低位
    for i in range(51, n):
        opens[i] = 89.0 + (i - 51) * 0.1
        highs[i] = opens[i] + 1.0
        lows[i] = opens[i] - 1.0
        closes[i] = opens[i] + 0.1
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})


def _ms(**kw) -> MarketState:
    """构造 MarketState，缺省值非跳空场景。"""
    base = {
        "current_price": 105.0,
        "d3_low": 98.0,
        "atr": 2.0,
        "spacetime_score": 0.95,
        "j1_confirmed": False,
        "macro_vix": 20.0,
    }
    base.update(kw)
    return MarketState(**base)


# ------------------------------------------------------------------
# 网关层面
# ------------------------------------------------------------------
def test_gateway_gap_on_open_triggers_liquidation():
    gw = RiskGateway()
    s1 = _ms(current_price=105.0)
    gw.process_tick(1_000_000.0, s1)
    assert len(gw.active_positions) == 1

    # 跳空低开
    s2 = _ms(current_price=90.0, open=90.0)
    res = gw.process_tick(1_000_000.0, s2)
    assert res["action"] == "fatal_gap_liquidation"
    assert res["reason"] == "hard_stop_gap_breach"
    assert abs(res["gap_price"] - 90.0) < 1e-9
    assert abs(res["liquidation_price"] - min(res["breached_stop"], res["gap_price"])) < 1e-9
    assert res["total_closed_size"] > 0
    assert len(gw.active_positions) == 0


def test_gateway_gap_on_current_price_triggers_liquidation():
    gw = RiskGateway()
    gw.process_tick(1_000_000.0, _ms(current_price=105.0))

    # 开盘未破，盘中击穿
    res = gw.process_tick(1_000_000.0, _ms(current_price=93.0, open=100.0))
    assert res["action"] == "fatal_gap_liquidation"
    assert abs(res["gap_price"] - 93.0) < 1e-9


def test_gateway_no_liquidation_when_price_holds():
    gw = RiskGateway()
    gw.process_tick(1_000_000.0, _ms(current_price=105.0))
    res = gw.process_tick(1_000_000.0, _ms(current_price=100.0, open=99.0))
    assert res["action"] != "fatal_gap_liquidation"
    assert len(gw.active_positions) == 1


def test_gateway_per_pos_hard_stop_check():
    """PDF 核心意图：加仓后第二笔 hard_stop 更紧（=d3_low），穿透任一笔即全平。"""
    gw = RiskGateway()
    # 初始建仓
    gw.process_tick(1_000_000.0, _ms(current_price=105.0))
    assert len(gw.active_positions) == 1

    # 加仓（hard_stop = d3_low = 98.0，比初始 hard_stop 更紧）
    gw.process_tick(1_000_000.0, _ms(current_price=108.0, j1_confirmed=True, spacetime_score=0.92))
    assert len(gw.active_positions) == 2

    # 穿透第二笔的 hard_stop(98.0)
    res = gw.process_tick(1_000_000.0, _ms(current_price=96.0, open=96.0))
    assert res["action"] == "fatal_gap_liquidation"
    assert len(gw.active_positions) == 0
    assert res["total_closed_size"] > 0


# ------------------------------------------------------------------
# 回测器层面：端到端 + 真实流血
# ------------------------------------------------------------------
def test_backtester_gap_liquidation_records_real_loss():
    """确定性数据框架：跳空低开触发强平 → 回测器记录亏损 round_trip。"""
    df = _gap_breach_df()
    bt = SimpleBacktester(initial_capital=1_000_000.0)
    result = bt.run(df, StructureParser(), RiskGateway())

    # 确认发生了跳空强平
    gap_trades = [t for t in bt.trades if t["type"] == "fatal_gap_liquidation"]
    assert len(gap_trades) >= 1

    # 确认是真实亏损（跳空价 < 建仓价，叠加卖空滑点+手续费）
    assert any(r < 0 for r in bt.round_trips), (
        f"跳空强平应产生负盈亏 round_trip，实际 round_trips={bt.round_trips}"
    )

    # 指标计算不崩溃
    assert "total_return_pct" in result.metrics
    assert result.metrics["total_trades"] >= 1


def test_backtester_no_phantom_fill_at_hard_stop():
    """理想止损幻觉回归测试：跳空时成交价应差于 hard_stop，而非等于 hard_stop。"""
    df = _gap_breach_df()
    bt = SimpleBacktester()
    bt.run(df, StructureParser(), RiskGateway())
    gap_trades = [t for t in bt.trades if t["type"] == "fatal_gap_liquidation"]
    if gap_trades:
        exec_price = gap_trades[0]["price"]
        # 跳空低开价 90 + 卖空滑点 -> 成交价 < 90（绝不等于 hard_stop 的理想价）
        assert exec_price < 90.0, f"跳空强平成交价 {exec_price} 不应理想化为 hard_stop，应 < 90"
