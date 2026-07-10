"""决策路由器测试 - 状态×结构矩阵 + 级别嵌套"""
from __future__ import annotations

import pytest

from trinity.context import (
    ActionType,
    Decision,
    JLevelContext,
    MacroState,
    SpacetimeScore,
    StructureEvidence,
    StructureType,
    TradingLevel,
    TrendDirection,
)
from trinity.decision_router import DecisionRouter


def make_ctx(
    level=TradingLevel.J,
    state=MacroState.MODERATE_STRONG,
    structure_type=StructureType.UNKNOWN,
    direction=TrendDirection.UP,
    price=100.0,
    ma55=95.0,
    boll_mid=98.0,
    boll_upper=105.0,
    boll_lower=91.0,
    symbol="TEST",
    macd_hist=0.0,
    dif=0.0,
    dea=0.0,
) -> JLevelContext:
    """构造测试用上下文"""
    return JLevelContext(
        level=level,
        symbol=symbol,
        state=state,
        state_evidence=[f"test_{state.value}"],
        structure=StructureEvidence(
            structure_type=structure_type,
            direction=direction,
            segments=3,
        ),
        ma55=ma55,
        price=price,
        boll_mid=boll_mid,
        boll_upper=boll_upper,
        boll_lower=boll_lower,
        macd_hist=macd_hist,
        dif=dif,
        dea=dea,
    )


class TestDecisionMatrix:
    """状态×结构决策矩阵"""

    def test_extreme_strong_up_a(self):
        """极强 + 上涨 + A结构 → STRONG_ADD"""
        ctx = make_ctx(state=MacroState.EXTREME_STRONG, structure_type=StructureType.A, direction=TrendDirection.UP)
        router = DecisionRouter()
        decision = router.route_single_level(ctx)
        assert decision.action == ActionType.STRONG_ADD

    def test_extreme_strong_up_b(self):
        """极强 + 上涨 + B结构 → STRONG_ADD"""
        ctx = make_ctx(state=MacroState.EXTREME_STRONG, structure_type=StructureType.B, direction=TrendDirection.UP)
        router = DecisionRouter()
        assert router.route_single_level(ctx).action == ActionType.STRONG_ADD

    def test_extreme_strong_down(self):
        """极强 + 下跌 → HOLD (无对应结构)"""
        ctx = make_ctx(state=MacroState.EXTREME_STRONG, direction=TrendDirection.DOWN)
        router = DecisionRouter()
        assert router.route_single_level(ctx).action == ActionType.HOLD

    def test_strong_up_b(self):
        """强 + 上涨 + B结构 → ADD_ON_PULLBACK"""
        ctx = make_ctx(state=MacroState.STRONG, structure_type=StructureType.B, direction=TrendDirection.UP)
        router = DecisionRouter()
        assert router.route_single_level(ctx).action == ActionType.ADD_ON_PULLBACK

    def test_strong_down_d(self):
        """强 + 下跌 + D结构 → REDUCE_CAUTIOUSLY"""
        ctx = make_ctx(state=MacroState.STRONG, structure_type=StructureType.D, direction=TrendDirection.DOWN)
        router = DecisionRouter()
        assert router.route_single_level(ctx).action == ActionType.REDUCE_CAUTIOUSLY

    def test_moderate_strong_c(self):
        """中偏强 + C结构 → SCOUT_WITH_STOP"""
        ctx = make_ctx(state=MacroState.MODERATE_STRONG, structure_type=StructureType.C, direction=TrendDirection.UP)
        router = DecisionRouter()
        assert router.route_single_level(ctx).action == ActionType.SCOUT_WITH_STOP

    def test_weak_up_d(self):
        """弱 + 上涨 + D结构 → BUY_CAUTIOUSLY"""
        ctx = make_ctx(state=MacroState.WEAK, structure_type=StructureType.D, direction=TrendDirection.UP)
        router = DecisionRouter()
        assert router.route_single_level(ctx).action == ActionType.BUY_CAUTIOUSLY

    def test_weak_down_b(self):
        """弱 + 下跌 + B结构 → SELL_ON_BOUNCE"""
        ctx = make_ctx(state=MacroState.WEAK, structure_type=StructureType.B, direction=TrendDirection.DOWN)
        router = DecisionRouter()
        assert router.route_single_level(ctx).action == ActionType.SELL_ON_BOUNCE

    def test_extreme_weak_down_a(self):
        """极弱 + 下跌 + A结构 → STRONG_REDUCE"""
        ctx = make_ctx(state=MacroState.EXTREME_WEAK, structure_type=StructureType.A, direction=TrendDirection.DOWN)
        router = DecisionRouter()
        assert router.route_single_level(ctx).action == ActionType.STRONG_REDUCE

    def test_extreme_weak_up(self):
        """极弱 + 上涨 → HOLD (小心被骗线)"""
        ctx = make_ctx(state=MacroState.EXTREME_WEAK, direction=TrendDirection.UP)
        router = DecisionRouter()
        assert router.route_single_level(ctx).action == ActionType.HOLD

    def test_structure_mismatch_downgrade(self):
        """结构不匹配 → 降级为 HOLD"""
        # 极强 + 上涨 + D结构 (D不在极强上涨的匹配列表[A,B]中)
        ctx = make_ctx(state=MacroState.EXTREME_STRONG, structure_type=StructureType.D, direction=TrendDirection.UP)
        router = DecisionRouter()
        decision = router.route_single_level(ctx)
        assert decision.action == ActionType.HOLD


class TestConfidenceAndRisk:
    """置信度与风险等级"""

    def test_extreme_strong_high_confidence(self):
        """极强状态置信度高"""
        ctx = make_ctx(state=MacroState.EXTREME_STRONG, structure_type=StructureType.A, direction=TrendDirection.UP)
        router = DecisionRouter()
        decision = router.route_single_level(ctx)
        assert decision.confidence >= 0.85

    def test_extreme_weak_low_confidence(self):
        """极弱状态置信度低"""
        ctx = make_ctx(state=MacroState.EXTREME_WEAK, direction=TrendDirection.DOWN, structure_type=StructureType.A)
        router = DecisionRouter()
        decision = router.route_single_level(ctx)
        assert decision.confidence <= 0.35

    def test_spacetime_insufficient_increases_risk(self):
        """时空不充分增加风险"""
        ctx = make_ctx(state=MacroState.MODERATE_STRONG, structure_type=StructureType.C)
        router = DecisionRouter()
        good_st = SpacetimeScore(time_score=0.9, space_score=0.9)
        bad_st = SpacetimeScore(time_score=0.2, space_score=0.3)
        d_good = router.route_single_level(ctx, good_st)
        d_bad = router.route_single_level(ctx, bad_st)
        assert d_bad.risk_level > d_good.risk_level


class TestLevelNesting:
    """级别嵌套 J+2→J→J-1→J-2"""

    def test_main_surge_snipe(self):
        """主涨段狙击: J+2极强 + J上涨 + J-1布林带确认 → STRONG_ADD"""
        ctx_j2 = make_ctx(level=TradingLevel.J_PLUS_2, state=MacroState.EXTREME_STRONG)
        ctx_j = make_ctx(level=TradingLevel.J, state=MacroState.EXTREME_STRONG,
                         structure_type=StructureType.A, direction=TrendDirection.UP)
        ctx_j1 = make_ctx(level=TradingLevel.J_MINUS_1, state=MacroState.EXTREME_STRONG,
                          price=98.0, boll_mid=98.5)  # 价格在中轨附近
        ctx_j2m = make_ctx(level=TradingLevel.J_MINUS_2, state=MacroState.EXTREME_STRONG,
                           price=98.0, ma55=96.0)  # 价格在55线上方

        router = DecisionRouter()
        decision = router.route(ctx_j2, ctx_j, ctx_j1, ctx_j2m)
        assert decision.action == ActionType.STRONG_ADD
        assert any("主涨段" in e for e in decision.evidence)

    def test_j2_compresses_j(self):
        """J+2 压制 J: J+2极弱时, 即使J上涨也受限"""
        ctx_j2 = make_ctx(level=TradingLevel.J_PLUS_2, state=MacroState.EXTREME_WEAK)
        ctx_j = make_ctx(level=TradingLevel.J, state=MacroState.STRONG,
                         structure_type=StructureType.B, direction=TrendDirection.UP)
        ctx_j1 = make_ctx(level=TradingLevel.J_MINUS_1)
        ctx_j2m = make_ctx(level=TradingLevel.J_MINUS_2)

        router = DecisionRouter()
        decision = router.route(ctx_j2, ctx_j, ctx_j1, ctx_j2m)
        # J+2极弱 + J上涨方向 → 矩阵查找 (极弱, UP) → HOLD
        assert decision.action == ActionType.HOLD

    def test_spacetime_downgrades_space_low(self):
        """Space Score <= 0.75 → STRONG_ADD 降级为 ADD_ON_PULLBACK"""
        ctx_j2 = make_ctx(level=TradingLevel.J_PLUS_2, state=MacroState.EXTREME_STRONG)
        ctx_j = make_ctx(level=TradingLevel.J, state=MacroState.EXTREME_STRONG,
                         structure_type=StructureType.A, direction=TrendDirection.UP)
        ctx_j1 = make_ctx(level=TradingLevel.J_MINUS_1, price=98.0, boll_mid=98.5)
        ctx_j2m = make_ctx(level=TradingLevel.J_MINUS_2, price=98.0, ma55=96.0)

        # space=0.6 <= 0.75 但 overall=0.68 >= 0.5 → 降级不至 WAIT
        st = SpacetimeScore(time_score=0.8, space_score=0.6)
        router = DecisionRouter()
        decision = router.route(ctx_j2, ctx_j, ctx_j1, ctx_j2m, spacetime=st)
        assert decision.action == ActionType.ADD_ON_PULLBACK
        assert any("降级" in e for e in decision.evidence)

    def test_spacetime_downgrades_to_wait(self):
        """时空严重不足 (overall < 0.5) → 直接 HOLD (蓝图 v2.1 EXIT/WAIT)"""
        ctx_j2 = make_ctx(level=TradingLevel.J_PLUS_2, state=MacroState.EXTREME_STRONG)
        ctx_j = make_ctx(level=TradingLevel.J, state=MacroState.EXTREME_STRONG,
                         structure_type=StructureType.A, direction=TrendDirection.UP)
        ctx_j1 = make_ctx(level=TradingLevel.J_MINUS_1, price=98.0, boll_mid=98.5)
        ctx_j2m = make_ctx(level=TradingLevel.J_MINUS_2, price=98.0, ma55=96.0)

        bad_st = SpacetimeScore(time_score=0.2, space_score=0.3)  # overall=0.26 < 0.5
        router = DecisionRouter()
        decision = router.route(ctx_j2, ctx_j, ctx_j1, ctx_j2m, spacetime=bad_st)
        assert decision.action == ActionType.HOLD
        assert any("退出" in e or "观望" in e for e in decision.evidence)

    def test_take_profit_t(self):
        """TAKE_PROFIT_T: 突破遇阻 + J-1背离 + 动能衰减"""
        router = DecisionRouter()
        ctx_j2 = make_ctx(level=TradingLevel.J_PLUS_2, state=MacroState.STRONG,
                          price=100.0, ma55=99.0)  # 价格接近MA55(遇阻), 非极强(动能衰减)
        ctx_j = make_ctx(level=TradingLevel.J, state=MacroState.STRONG,
                         structure_type=StructureType.B, direction=TrendDirection.UP)
        ctx_j1 = make_ctx(level=TradingLevel.J_MINUS_1, macd_hist=-0.1)  # MACD柱为负(背离)
        ctx_j2m = make_ctx(level=TradingLevel.J_MINUS_2)
        decision = router.route(ctx_j2, ctx_j, ctx_j1, ctx_j2m)
        assert decision.action == ActionType.TAKE_PROFIT_T
        assert any("做T止盈" in e for e in decision.evidence)

    def test_exit_wait_structure_broken(self):
        """EXIT/WAIT: 结构破坏 (J-1背离 + J跌破MA55)"""
        router = DecisionRouter()
        ctx_j2 = make_ctx(level=TradingLevel.J_PLUS_2, state=MacroState.STRONG)
        ctx_j = make_ctx(level=TradingLevel.J, state=MacroState.STRONG,
                         structure_type=StructureType.B, direction=TrendDirection.UP,
                         price=90.0, ma55=100.0)  # 价格跌破MA55
        ctx_j1 = make_ctx(level=TradingLevel.J_MINUS_1, macd_hist=-0.2)  # 背离
        ctx_j2m = make_ctx(level=TradingLevel.J_MINUS_2)
        decision = router.route(ctx_j2, ctx_j, ctx_j1, ctx_j2m)
        assert decision.action == ActionType.HOLD
        assert any("退出" in e or "观望" in e for e in decision.evidence)

    def test_evidence_chain_complete(self):
        """证据链应包含各级别信息"""
        ctx_j2 = make_ctx(level=TradingLevel.J_PLUS_2, state=MacroState.STRONG)
        ctx_j = make_ctx(level=TradingLevel.J, state=MacroState.STRONG,
                         structure_type=StructureType.B, direction=TrendDirection.UP)
        ctx_j1 = make_ctx(level=TradingLevel.J_MINUS_1)
        ctx_j2m = make_ctx(level=TradingLevel.J_MINUS_2)

        router = DecisionRouter()
        decision = router.route(ctx_j2, ctx_j, ctx_j1, ctx_j2m)
        evidence_text = " ".join(decision.evidence)
        assert "J+2" in evidence_text
        assert "J]" in evidence_text or "[J]" in evidence_text
        assert "置信度" in evidence_text
        assert "风险" in evidence_text

    def test_decision_serializable(self):
        """决策可序列化为字典"""
        ctx = make_ctx(state=MacroState.EXTREME_STRONG, structure_type=StructureType.A, direction=TrendDirection.UP)
        router = DecisionRouter()
        decision = router.route_single_level(ctx)
        d = decision.to_dict()
        assert d["action"] == "STRONG_ADD"
        assert isinstance(d["evidence"], list)
        assert isinstance(d["confidence"], float)
