"""Phase 1 回归测试：Trinity OS v2.2.1 风险与仓位管理子系统。

WHY THIS FILE EXISTS
--------------------
对应架构设计 + 代码实现（risk_gateway_v2_2_1.py）的单元测试，锁定：
  * 初始入场的风险金额 / 有效距离 / 硬止损公式；
  * 宏观熔断（VIX / 大宗商品）阈值边界；
  * 正金字塔加仓的触发条件（含熔断）、ladder 索引（current_step=0 修复）、子弹打完；
  * 跟踪止盈的 ATR/swing 更新、永不破硬止损、倒金字塔减仓分桶；
  * RiskGateway.process_tick 端到端（初始入场 → 加仓 → 跟踪减仓）与总风险计算。

纯增量、零耦合：仅导入 core.risk_gateway_v2_2_1，不触碰其他模块。
"""
from __future__ import annotations

from datetime import datetime

from core.risk_gateway_v2_2_1 import (
    MarketState,
    Position,
    MacroCircuitBreaker,
    InitialEntryEngine,
    PyramidManager,
    TrailingStopEngine,
    RiskGateway,
)


def make_state(**kw) -> MarketState:
    base = dict(
        current_price=100.0, d3_low=95.0, atr=2.0,
        spacetime_score=0.85, j1_confirmed=True,
        macro_vix=20.0, macro_commodity_shock=0.0,
    )
    base.update(kw)
    return MarketState(**base)


# ---------------- Position / MarketState ----------------
def test_position_remaining_size_initialized():
    p = Position(entry_price=100, size=10, hard_stop=95)
    assert p.remaining_size == 10
    assert p.is_initial is False
    assert p.add_step == 0


def test_marketstate_defaults():
    s = MarketState(current_price=100, d3_low=95, atr=2, spacetime_score=0.9, j1_confirmed=True)
    assert s.macro_vix == 20.0
    assert s.macro_commodity_shock == 0.0
    assert isinstance(s.timestamp, datetime)


# ---------------- MacroCircuitBreaker ----------------
def test_breaker_vix_triggered_at_threshold():
    b = MacroCircuitBreaker()
    assert b.is_triggered(make_state(macro_vix=35.0)) is True
    assert b.is_triggered(make_state(macro_vix=34.9)) is False


def test_breaker_commodity_triggered_at_threshold():
    b = MacroCircuitBreaker()
    assert b.is_triggered(make_state(macro_commodity_shock=0.15)) is True
    assert b.is_triggered(make_state(macro_commodity_shock=0.14)) is False


def test_breaker_custom_thresholds():
    b = MacroCircuitBreaker(vix_threshold=30.0, commodity_shock_threshold=0.10)
    assert b.is_triggered(make_state(macro_vix=30.0)) is True
    assert b.is_triggered(make_state(macro_commodity_shock=0.10)) is True


# ---------------- InitialEntryEngine ----------------
def test_initial_entry_none_when_score_zero():
    e = InitialEntryEngine()
    assert e.calculate_entry(1_000_000, make_state(spacetime_score=0.0)) is None


def test_initial_entry_calculation():
    e = InitialEntryEngine(base_risk_pct=0.01)
    acc = 1_000_000.0
    s = make_state(current_price=100, d3_low=95, atr=2.0, spacetime_score=0.80)
    pos = e.calculate_entry(acc, s)
    assert pos is not None
    risk = acc * 0.01 * 0.80                       # 8000
    structural = 100 - 95                          # 5
    atr_dist = 2.0 * 1.8 * 0.75                    # 2.7
    eff = max(structural, atr_dist)                # 5
    assert abs(pos.hard_stop - (100 - eff)) < 1e-9  # 95.0
    assert abs(pos.size - risk / eff) < 1e-9       # 1600
    assert pos.is_initial is True


def test_initial_entry_atr_dominates_dist():
    e = InitialEntryEngine(base_risk_pct=0.01)
    acc = 1_000_000.0
    # structural (100-99=1) < atr_dist (5*1.8*0.75=6.75) -> ATR 主导
    s = make_state(current_price=100, d3_low=99.0, atr=5.0, spacetime_score=0.80)
    pos = e.calculate_entry(acc, s)
    atr_dist = 5.0 * 1.8 * 0.75                    # 6.75
    eff = max(1.0, atr_dist)                       # 6.75
    assert abs(pos.hard_stop - (100 - eff)) < 1e-9  # 93.25
    assert abs(pos.size - (acc * 0.01 * 0.80) / eff) < 1e-9


# ---------------- PyramidManager ----------------
def test_pyramid_can_add_all_conditions():
    pm = PyramidManager()
    s = make_state(current_price=103, d3_low=95, atr=2, spacetime_score=0.85, j1_confirmed=True)
    assert pm.can_add_position(s, current_total_risk_pct=0.0) is True


def test_pyramid_blocked_when_step_exhausted():
    pm = PyramidManager()
    pm.current_step = len(pm.ladder)               # 4
    assert pm.can_add_position(make_state(), 0.0) is False


def test_pyramid_blocked_on_macro_breaker():
    pm = PyramidManager()
    s = make_state(current_price=103, d3_low=95, spacetime_score=0.85, j1_confirmed=True, macro_vix=40)
    assert pm.can_add_position(s, 0.0) is False


def test_pyramid_blocked_when_price_below_d3_or_no_j1():
    pm = PyramidManager()
    s_price = make_state(current_price=95, d3_low=95, spacetime_score=0.85, j1_confirmed=True)
    assert pm.can_add_position(s_price, 0.0) is False
    s_no_j1 = make_state(current_price=103, d3_low=95, spacetime_score=0.85, j1_confirmed=False)
    assert pm.can_add_position(s_no_j1, 0.0) is False


def test_pyramid_blocked_low_score_or_high_risk():
    pm = PyramidManager()
    s_score = make_state(current_price=103, d3_low=95, spacetime_score=0.79, j1_confirmed=True)
    assert pm.can_add_position(s_score, 0.0) is False
    s_risk = make_state(current_price=103, d3_low=95, spacetime_score=0.85, j1_confirmed=True)
    assert pm.can_add_position(s_risk, 0.04) is False   # >= max_total_risk_pct


def test_pyramid_add_on_ladder_and_step():
    pm = PyramidManager()
    assert pm.current_step == 0                      # FIX: 从 0 开始
    s = make_state(current_price=103, d3_low=95, atr=2, spacetime_score=0.85)
    pos0 = pm.calculate_add_on(1_000_000, s, 0.01)
    assert pm.current_step == 1
    assert pos0.add_step == 0
    assert abs(pos0.hard_stop - 95.0) < 1e-9         # 绑定 d3_low
    expected0 = (1_000_000 * 0.01 * 0.40 * 0.85) / max(103 - 95, 0.0001)
    assert abs(pos0.size - expected0) < 1e-9         # ladder[0]=0.40
    pos1 = pm.calculate_add_on(1_000_000, s, 0.01)
    assert pm.current_step == 2
    assert pos1.add_step == 1
    expected1 = (1_000_000 * 0.01 * 0.30 * 0.85) / 8.0
    assert abs(pos1.size - expected1) < 1e-9         # ladder[1]=0.30


def test_pyramid_bullets_exhausted():
    pm = PyramidManager()
    for _ in range(4):
        assert pm.can_add_position(
            make_state(current_price=103, d3_low=95, spacetime_score=0.85, j1_confirmed=True), 0.0
        )
        pm.calculate_add_on(1_000_000, make_state(current_price=103, d3_low=95, spacetime_score=0.85), 0.01)
    assert pm.current_step == 4
    assert pm.can_add_position(
        make_state(current_price=103, d3_low=95, spacetime_score=0.85, j1_confirmed=True), 0.0
    ) is False


# ---------------- TrailingStopEngine ----------------
def test_trailing_stop_never_below_hard_stop():
    te = TrailingStopEngine(trailing_multiplier=2.0)
    s = make_state(current_price=110, atr=2.0)
    stop = te.update_trailing_stop(s, swing_low=None, initial_hard_stop=95.0)
    assert stop >= 95.0
    assert abs(stop - 106.0) < 1e-9                  # atr_stop = 110 - 2*2


def test_trailing_stop_with_swing_low():
    te = TrailingStopEngine(trailing_multiplier=2.0)
    s = make_state(current_price=110, atr=2.0)
    stop = te.update_trailing_stop(s, swing_low=100.0, initial_hard_stop=95.0)
    # atr_stop=106, swing_stop=99, max -> 106, floored at 95
    assert abs(stop - 106.0) < 1e-9


def test_trailing_stop_monotonic():
    te = TrailingStopEngine()
    s1 = make_state(current_price=110, atr=2.0)
    stop1 = te.update_trailing_stop(s1, swing_low=None, initial_hard_stop=95.0)
    s2 = make_state(current_price=108, atr=2.0)
    stop2 = te.update_trailing_stop(s2, swing_low=None, initial_hard_stop=95.0)
    assert stop2 >= stop1
    assert stop2 >= 95.0


def test_calculate_exit_size_buckets():
    te = TrailingStopEngine()
    te.highest_price = 100.0
    assert te.calculate_exit_size(91.0, 100.0) == 30.0   # drawdown 0.09 > 0.08 -> 30%
    assert te.calculate_exit_size(94.0, 100.0) == 15.0   # drawdown 0.06 > 0.05 -> 15%
    assert te.calculate_exit_size(97.0, 100.0) == 0.0    # drawdown 0.03 -> 0
    te2 = TrailingStopEngine()
    assert te2.calculate_exit_size(50.0, 100.0) == 0.0   # highest <= 0


# ---------------- RiskGateway end-to-end ----------------
def test_gateway_initial_entry():
    gw = RiskGateway()
    res = gw.process_tick(
        1_000_000, make_state(current_price=100, d3_low=95, atr=2, spacetime_score=0.87, j1_confirmed=False)
    )
    assert res["action"] == "initial_entry"
    assert len(gw.active_positions) == 1
    assert gw.initial_hard_stop is not None


def test_gateway_pyramid_add_then_trailing_exit():
    gw = RiskGateway()
    gw.process_tick(
        1_000_000, make_state(current_price=100, d3_low=95, atr=2, spacetime_score=0.87, j1_confirmed=False)
    )
    res2 = gw.process_tick(
        1_000_000, make_state(current_price=103, d3_low=95, atr=2, spacetime_score=0.91, j1_confirmed=True)
    )
    assert res2["action"] == "pyramid_add"
    assert len(gw.active_positions) == 2
    before = sum(p.remaining_size for p in gw.active_positions)

    gw.trailing_engine.highest_price = 112.0
    res3 = gw.process_tick(
        1_000_000, make_state(current_price=98, d3_low=95, atr=2.8, spacetime_score=0.65, j1_confirmed=True)
    )
    assert res3["action"] == "trailing_exit"
    assert res3["exit_size"] > 0
    after = sum(p.remaining_size for p in gw.active_positions)
    assert after < before


def test_gateway_total_risk_pct():
    gw = RiskGateway()
    gw.process_tick(
        1_000_000, make_state(current_price=100, d3_low=95, atr=2, spacetime_score=0.87, j1_confirmed=False)
    )
    risk = gw._calculate_total_risk_pct(1_000_000)
    p = gw.active_positions[0]
    expected = (p.entry_price - p.hard_stop) * p.remaining_size / 1_000_000
    assert abs(risk - expected) < 1e-9


def test_gateway_status_fields():
    gw = RiskGateway()
    gw.process_tick(
        1_000_000, make_state(current_price=100, d3_low=95, atr=2, spacetime_score=0.87, j1_confirmed=False)
    )
    st = gw.get_status()
    assert set(st.keys()) == {
        "active_positions_count", "total_remaining_size",
        "current_trailing_stop", "pyramid_step", "initial_hard_stop",
    }


# ---------------- Phase 2: 硬止损跳空强制全平 ----------------
def test_gateway_gap_liquidation_on_open_below_hard_stop():
    gw = RiskGateway()
    gw.process_tick(1_000_000, make_state(current_price=100, d3_low=95, atr=2, spacetime_score=0.87, j1_confirmed=False))
    # 跳空低开 90，击穿 hard_stop=95
    res = gw.process_tick(1_000_000, make_state(current_price=90, open=90, d3_low=95, atr=2, spacetime_score=0.87))
    assert res["action"] == "fatal_gap_liquidation"
    # 真实撮合价 = min(hard_stop, gap_price) = min(95, 90) = 90
    assert abs(res["liquidation_price"] - 90.0) < 1e-9
    assert len(gw.active_positions) == 0
    assert gw.initial_hard_stop is None
    assert res["total_closed_size"] > 0


def test_gateway_gap_liquidation_on_current_price_below_hard_stop():
    gw = RiskGateway()
    gw.process_tick(1_000_000, make_state(current_price=100, d3_low=95, atr=2, spacetime_score=0.87, j1_confirmed=False))
    # 盘中击穿（open 未破，但 current_price 跌破）
    res = gw.process_tick(1_000_000, make_state(current_price=92, open=96, d3_low=95, atr=2, spacetime_score=0.87))
    assert res["action"] == "fatal_gap_liquidation"
    # 真实撮合价 = min(95, 92) = 92
    assert abs(res["liquidation_price"] - 92.0) < 1e-9


def test_gateway_no_fatal_when_price_holds_above_hard_stop():
    gw = RiskGateway()
    gw.process_tick(1_000_000, make_state(current_price=100, d3_low=95, atr=2, spacetime_score=0.87, j1_confirmed=False))
    # 价格仍在 hard_stop 之上 -> 不触发强平
    res = gw.process_tick(1_000_000, make_state(current_price=98, open=97, d3_low=95, atr=2, spacetime_score=0.87))
    assert res["action"] != "fatal_gap_liquidation"
    assert len(gw.active_positions) >= 1
