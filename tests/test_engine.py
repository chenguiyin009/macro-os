"""Trinity 核心引擎测试

覆盖: State 枚举 / SpacetimeScore / StructureParser / StateMachineEngine
      / AntiAmnesiaTracker / DecisionRouterGateway / TrinityEngine / 桥接适配器
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

import pytest

from trinity.core.engine import (
    AntiAmnesiaTracker,
    AttributionEngine,
    Decision,
    DecisionRouterGateway,
    EvidenceFactor,
    JLevelContext,
    MemoryEvent,
    SpacetimeScore,
    State,
    StateMachineEngine,
    StructureParser,
    TrinityEngine,
    calculate_spacetime,
    to_legacy_macro_state,
)


# ========== 辅助函数 ==========

def make_ctx(
    symbol="TEST",
    state_j2=State.EXTREME_BULL,
    state_j=State.BULL,
    j_div=False,
    ma55_pullback=True,
    price=100.0,
    j_close=98.0,
    j_ma55=95.0,
) -> JLevelContext:
    return JLevelContext(
        symbol=symbol,
        timestamp=datetime.now(),
        state_j2=state_j2,
        state_j=state_j,
        j_minus_1_has_divergence=j_div,
        j_close=j_close,
        j_ma55=j_ma55,
        j_has_completed_ma55_pullback=ma55_pullback,
        price=price,
    )


# ========== 1. State 枚举 ==========

class TestStateEnum:
    def test_extreme_bull_is_extreme(self):
        assert State.EXTREME_BULL.is_extreme
        assert State.EXTREME_BEAR.is_extreme

    def test_non_extreme(self):
        assert not State.BULL.is_extreme
        assert not State.NEUTRAL.is_extreme

    def test_bullish(self):
        assert State.EXTREME_BULL.is_bullish
        assert State.BULL.is_bullish
        assert State.MID_BULL.is_bullish
        assert not State.BEAR.is_bullish

    def test_neutral_not_clear(self):
        assert not State.NEUTRAL.is_clear
        assert State.BULL.is_clear

    def test_seven_states(self):
        assert len(State) == 7


# ========== 2. SpacetimeScore ==========

class TestSpacetimeScore:
    def test_fields(self):
        score = SpacetimeScore(
            time_score=0.8, space_score=0.9,
            total_score=0.85, reference="test",
        )
        assert score.time_score == 0.8
        assert score.space_score == 0.9
        assert score.details == {}


# ========== 3. StructureParser ==========

class TestStructureParser:
    def test_insufficient_pivots(self):
        parser = StructureParser()
        score, desc = parser.evaluate_d_structure([])
        assert score == 0.25
        assert "不足" in desc

    def test_complete_d_structure(self):
        parser = StructureParser()
        pivots = [
            {"price": 100, "is_high": True, "index": 0},   # D1
            {"price": 70, "is_high": False, "index": 10},   # D2
            {"price": 90, "is_high": True, "index": 20},    # D3 < D1
            {"price": 60, "is_high": False, "index": 30},   # D4 < D2
        ]
        score, desc = parser.evaluate_d_structure(pivots)
        assert score >= 0.85
        assert "D1-D2-D3" in desc

    def test_non_alternating(self):
        parser = StructureParser()
        pivots = [
            {"price": 100, "is_high": True},
            {"price": 90, "is_high": True},   # 连续高点
            {"price": 80, "is_high": False},
            {"price": 70, "is_high": False},
        ]
        score, desc = parser.evaluate_d_structure(pivots)
        assert score <= 0.3

    def test_uptrend_d_structure(self):
        """上涨 D 结构: D1(低)→D2(高)→D3(低,高于D1)→D4(高,破D2)"""
        parser = StructureParser()
        pivots = [
            {"price": 50, "is_high": False},
            {"price": 80, "is_high": True},
            {"price": 60, "is_high": False},   # D3 > D1
            {"price": 90, "is_high": True},    # D4 > D2
        ]
        score, desc = parser.evaluate_d_structure(pivots)
        assert score >= 0.85


# ========== 4. calculate_spacetime ==========

class TestCalculateSpacetime:
    def test_symmetric_durations(self):
        """等长时间 → 高分"""
        ctx = make_ctx()
        score = calculate_spacetime(ctx, structure_score=0.9, duration_curr=18, duration_ref=18)
        assert score.time_score == 1.0
        # 蓝图 v2.1: total = time*0.4 + space*0.6 = 1.0*0.4 + 0.9*0.6 = 0.94
        assert score.total_score >= 0.9
        assert "18周" in score.reference

    def test_weighted_formula(self):
        """蓝图 v2.1: total = time*0.4 + space*0.6"""
        ctx = make_ctx()
        score = calculate_spacetime(ctx, structure_score=0.8, duration_curr=18, duration_ref=18)
        expected = 1.0 * 0.4 + 0.8 * 0.6
        assert abs(score.total_score - expected) < 1e-10

    def test_asymmetric_durations(self):
        """差异大 → 低分"""
        ctx = make_ctx()
        score = calculate_spacetime(ctx, structure_score=0.5, duration_curr=5, duration_ref=30)
        assert score.time_score < 0.5

    def test_zero_duration(self):
        """零时长不崩溃"""
        ctx = make_ctx()
        score = calculate_spacetime(ctx, structure_score=0.5, duration_curr=0, duration_ref=0)
        assert score.time_score == 0.0

    def test_details_populated(self):
        ctx = make_ctx()
        score = calculate_spacetime(ctx, structure_score=0.8, duration_curr=15, duration_ref=18)
        assert "duration_curr" in score.details
        assert "time_deviation" in score.details
        assert score.details["time_deviation"] == 3


# ========== 5. StateMachineEngine ==========

class TestStateMachineEngine:
    def test_extreme_bull(self):
        sm = StateMachineEngine()
        state, evidence = sm.determine_state(dif=0.5, dea=-0.3)
        assert state == State.EXTREME_BULL
        assert len(evidence) > 0

    def test_extreme_bear(self):
        sm = StateMachineEngine()
        state, _ = sm.determine_state(dif=-0.5, dea=0.3)
        assert state == State.EXTREME_BEAR

    def test_bull(self):
        sm = StateMachineEngine()
        state, _ = sm.determine_state(dif=0.5, dea=0.3)
        assert state == State.BULL

    def test_neutral_zero(self):
        """DIF=DEA=0 → 混沌"""
        sm = StateMachineEngine()
        state, _ = sm.determine_state(dif=0.0, dea=0.0)
        assert state == State.NEUTRAL

    def test_event_detection(self):
        sm = StateMachineEngine()
        # 低位金叉
        state, evidence = sm.determine_state(
            dif=-0.1, dea=-0.2, prev_dif=-0.3, prev_dea=-0.15
        )
        assert any("LOW_GOLDEN_CROSS" in e for e in evidence)

    def test_run_sequence(self):
        """序列运行不崩溃"""
        sm = StateMachineEngine()
        dif_series = [-0.5, -0.3, -0.1, 0.1, 0.3, 0.5]
        dea_series = [-0.3, -0.25, -0.2, -0.15, -0.1, -0.05]
        states = sm.run_sequence(dif_series, dea_series)
        assert len(states) == 6
        # 应从偏空过渡到偏多
        assert states[-1].is_bullish

    def test_empty_sequence(self):
        sm = StateMachineEngine()
        assert sm.run_sequence([], []) == []


# ========== 6. AntiAmnesiaTracker ==========

class TestAntiAmnesiaTracker:
    def test_record_and_replay(self):
        tracker = AntiAmnesiaTracker()
        ctx = make_ctx()
        decision = Decision(
            action="STRONG_ADD", confidence=0.9, reasons=["r1"],
            spacetime_score=None, risk_level=0.2,
        )
        event = tracker.record(ctx, decision)
        assert event.event_id.startswith("MEM-")
        assert tracker.count == 1
        assert len(tracker.replay()) == 1

    def test_recall_by_state(self):
        tracker = AntiAmnesiaTracker()
        # 记录两条不同状态的决策
        ctx1 = make_ctx(state_j2=State.EXTREME_BULL)
        ctx2 = make_ctx(state_j2=State.EXTREME_BEAR)
        d = Decision(action="HOLD", confidence=0.5, reasons=["r"], spacetime_score=None, risk_level=0.3)
        tracker.record(ctx1, d)
        tracker.record(ctx2, d)
        # 回忆极强状态的决策
        recalled = tracker.recall(state_j2=State.EXTREME_BULL)
        assert len(recalled) == 1
        assert recalled[0].context_snapshot["state_j2"] == "极强"

    def test_recall_similar(self):
        """回忆相似情境 (防失忆核心)"""
        tracker = AntiAmnesiaTracker()
        ctx = make_ctx(symbol="000001", state_j2=State.BULL, state_j=State.MID_BULL)
        d = Decision(action="ADD_ON_PULLBACK", confidence=0.75, reasons=["r"], spacetime_score=None, risk_level=0.3)
        tracker.record(ctx, d)
        # 再次遇到相似情境
        ctx2 = make_ctx(symbol="000001", state_j2=State.BULL, state_j=State.MID_BULL)
        similar = tracker.recall_similar(ctx2)
        assert len(similar) == 1
        assert similar[0].decision["action"] == "ADD_ON_PULLBACK"

    def test_update_outcome(self):
        """回填事后结果"""
        tracker = AntiAmnesiaTracker()
        ctx = make_ctx()
        d = Decision(action="HOLD", confidence=0.5, reasons=["r"], spacetime_score=None, risk_level=0.3)
        event = tracker.record(ctx, d)
        assert event.outcome is None
        ok = tracker.update_outcome(event.event_id, "盈利+5%")
        assert ok
        assert tracker.replay()[0].outcome == "盈利+5%"

    def test_save_load(self):
        tracker = AntiAmnesiaTracker()
        ctx = make_ctx()
        d = Decision(action="HOLD", confidence=0.5, reasons=["r"], spacetime_score=None, risk_level=0.3)
        tracker.record(ctx, d)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            filepath = f.name
        try:
            tracker.save(filepath)
            tracker2 = AntiAmnesiaTracker()
            tracker2.load(filepath)
            assert tracker2.count == 1
            assert tracker2.replay()[0].symbol == "TEST"
        finally:
            os.unlink(filepath)

    def test_summary(self):
        tracker = AntiAmnesiaTracker()
        ctx = make_ctx(symbol="A")
        d = Decision(action="HOLD", confidence=0.5, reasons=["r"], spacetime_score=None, risk_level=0.3)
        tracker.record(ctx, d)
        tracker.record(ctx, d)
        summary = tracker.summary()
        assert summary["total_memories"] == 2
        assert summary["symbols"]["A"] == 2

    def test_clear(self):
        tracker = AntiAmnesiaTracker()
        ctx = make_ctx()
        d = Decision(action="HOLD", confidence=0.5, reasons=["r"], spacetime_score=None, risk_level=0.3)
        tracker.record(ctx, d)
        tracker.clear()
        assert tracker.count == 0


# ========== 7. DecisionRouterGateway ==========

class TestDecisionRouterGateway:
    def test_extreme_bull_up(self):
        router = DecisionRouterGateway()
        ctx = make_ctx(state_j2=State.EXTREME_BULL)
        decision = router.route(ctx, direction="UP")
        assert decision.action == "STRONG_ADD"

    def test_extreme_bear_down(self):
        router = DecisionRouterGateway()
        ctx = make_ctx(state_j2=State.EXTREME_BEAR)
        decision = router.route(ctx, direction="DOWN")
        assert decision.action == "STRONG_REDUCE"

    def test_neutral_hold(self):
        """混沌态 → HOLD"""
        router = DecisionRouterGateway()
        ctx = make_ctx(state_j2=State.NEUTRAL)
        decision = router.route(ctx, direction="UP")
        assert decision.action == "HOLD"

    def test_main_surge_upgrade(self):
        """主涨段狙击: 升级为 STRONG_ADD"""
        router = DecisionRouterGateway()
        ctx = make_ctx(
            state_j2=State.EXTREME_BULL,
            j_div=False,
            ma55_pullback=True,
        )
        decision = router.route(ctx, direction="UP")
        assert decision.action == "STRONG_ADD"
        assert any("主涨段" in r for r in decision.reasons)

    def test_spacetime_downgrade_space_low(self):
        """Space Score <= 0.75 → STRONG_ADD 降级为 ADD_ON_PULLBACK"""
        router = DecisionRouterGateway()
        ctx = make_ctx(state_j2=State.EXTREME_BULL, j_div=False, ma55_pullback=True)
        # total >= 0.5 (不触发 WAIT), 但 space_score = 0.6 <= 0.75
        st = SpacetimeScore(time_score=0.8, space_score=0.6, total_score=0.68, reference="space_low")
        decision = router.route(ctx, spacetime_score=st, direction="UP")
        assert decision.action == "ADD_ON_PULLBACK"
        assert any("降级" in r for r in decision.reasons)

    def test_spacetime_downgrade_to_wait(self):
        """时空严重不足 (total < 0.5) → 直接降为 WAIT (蓝图 v2.1 EXIT/WAIT)"""
        router = DecisionRouterGateway()
        ctx = make_ctx(state_j2=State.EXTREME_BULL, j_div=False, ma55_pullback=True)
        bad_st = SpacetimeScore(time_score=0.2, space_score=0.3, total_score=0.25, reference="very_bad")
        decision = router.route(ctx, spacetime_score=bad_st, direction="UP")
        assert decision.action == "WAIT"
        assert any("退出" in r or "观望" in r for r in decision.reasons)

    def test_decision_has_reasons(self):
        router = DecisionRouterGateway()
        ctx = make_ctx()
        decision = router.route(ctx)
        assert len(decision.reasons) > 0
        assert any("J+2" in r for r in decision.reasons)
        assert any("置信度" in r for r in decision.reasons)
        assert any("风险" in r for r in decision.reasons)

    def test_decision_serializable(self):
        router = DecisionRouterGateway()
        ctx = make_ctx()
        decision = router.route(ctx)
        d = decision.to_dict()
        assert d["action"] is not None
        assert isinstance(d["reasons"], list)
        assert isinstance(d["timestamp"], str)


# ========== 8. TrinityEngine 集成 ==========

class TestTrinityEngine:
    def test_analyze_returns_decision(self):
        engine = TrinityEngine()
        ctx = make_ctx(state_j2=State.EXTREME_BULL, state_j=State.BULL)
        decision = engine.analyze(
            ctx, structure_score=0.85, structure_type="A",
            direction="UP", duration_curr=18, duration_ref=18,
        )
        assert decision.action is not None
        assert 0 <= decision.confidence <= 1
        assert decision.spacetime_score is not None
        assert decision.spacetime_score.total_score >= 0.8

    def test_analyze_records_memory(self):
        engine = TrinityEngine()
        ctx = make_ctx(symbol="INTEGRATION")
        engine.analyze(ctx)
        assert engine.memory.count == 1

    def test_recall_after_analyze(self):
        """分析后可回忆 (防失忆)"""
        engine = TrinityEngine()
        ctx = make_ctx(symbol="RECALL", state_j2=State.BULL, state_j=State.MID_BULL)
        engine.analyze(ctx)
        # 相似情境应能回忆
        ctx2 = make_ctx(symbol="RECALL", state_j2=State.BULL, state_j=State.MID_BULL)
        memories = engine.recall_similar(ctx2)
        assert len(memories) == 1

    def test_save_load_memory(self):
        engine = TrinityEngine()
        ctx = make_ctx()
        engine.analyze(ctx)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            filepath = f.name
        try:
            engine.save_memory(filepath)
            engine2 = TrinityEngine()
            engine2.load_memory(filepath)
            assert engine2.memory.count == 1
        finally:
            os.unlink(filepath)

    def test_multiple_analyze_accumulate(self):
        engine = TrinityEngine()
        for i in range(5):
            ctx = make_ctx(symbol=f"S{i}")
            engine.analyze(ctx)
        assert engine.memory.count == 5
        summary = engine.memory.summary()
        assert summary["total_memories"] == 5


# ========== 9. 桥接适配器 ==========

class TestBridge:
    def test_state_mapping(self):
        from trinity.context import MacroState
        assert to_legacy_macro_state(State.EXTREME_BULL) == MacroState.EXTREME_STRONG
        assert to_legacy_macro_state(State.BULL) == MacroState.STRONG
        assert to_legacy_macro_state(State.EXTREME_BEAR) == MacroState.EXTREME_WEAK
        assert to_legacy_macro_state(State.NEUTRAL) == MacroState.MODERATE_STRONG

    def test_all_states_mapped(self):
        """所有 7 个状态都能映射"""
        from trinity.context import MacroState
        for state in State:
            mapped = to_legacy_macro_state(state)
            assert isinstance(mapped, MacroState)


# ========== 10. 现有系统兼容性 ==========

class TestExistingSystemCompat:
    """确保新引擎不破坏现有系统"""

    def test_existing_imports_still_work(self):
        """现有模块导入正常"""
        from trinity.context import MacroState, Decision as LegacyDecision
        from trinity.decision_router import DecisionRouter
        from trinity.state_machine import StateMachine
        from trinity.ledger import EventSourcingTracker
        assert MacroState.EXTREME_STRONG is not None
        assert DecisionRouter is not None
        assert StateMachine is not None
        assert EventSourcingTracker is not None

    def test_existing_orchestrator_still_works(self):
        """现有编排器仍可运行"""
        from trinity.orchestrator import Orchestrator
        orch = Orchestrator()
        decisions = orch.run(symbol="COMPAT", bars=80, dry_run=True, seed=42)
        assert len(decisions) >= 1


# ========== 11. 归因引擎 (蓝图 v2.1 §4.2) ==========

class TestEvidenceFactor:
    """结构化证据因子"""

    def test_creation(self):
        f = EvidenceFactor(module="J+2", factor="MACD_HIST", value=0.82, weight=0.3)
        assert f.module == "J+2"
        assert f.factor == "MACD_HIST"
        assert f.value == 0.82
        assert f.weight == 0.3
        # contribution = value * weight
        assert f.contribution == round(0.82 * 0.3, 4)

    def test_explicit_contribution(self):
        f = EvidenceFactor(module="J", factor="STATE", value=0.5, weight=0.4, contribution=0.3)
        assert f.contribution == 0.3

    def test_to_dict(self):
        f = EvidenceFactor(module="J-1", factor="MA55", value=1.0, weight=0.2)
        d = f.to_dict()
        assert d["module"] == "J-1"
        assert d["contribution"] == round(1.0 * 0.2, 4)


class TestAttributionEngine:
    """归因引擎"""

    def test_collect_returns_factors(self):
        engine = AttributionEngine()
        ctx = make_ctx()
        factors = engine.collect(ctx, structure_score=0.85, direction="UP")
        assert len(factors) >= 4
        # 应包含各模块的因子
        modules = {f.module for f in factors}
        assert "J+2" in modules
        assert "J" in modules
        assert "J-1" in modules

    def test_collect_with_spacetime(self):
        engine = AttributionEngine()
        ctx = make_ctx()
        st = SpacetimeScore(time_score=0.9, space_score=0.95, total_score=0.93, reference="test")
        factors = engine.collect(ctx, spacetime=st, structure_score=0.85, direction="UP")
        st_factor = [f for f in factors if f.factor == "TOTAL_SCORE"]
        assert len(st_factor) == 1
        assert st_factor[0].value == 0.93

    def test_attribute_stats(self):
        """归因统计"""
        engine = AttributionEngine()
        ctx1 = make_ctx(state_j2=State.EXTREME_BULL)
        ctx2 = make_ctx(state_j2=State.BEAR)
        engine.collect(ctx1, structure_score=0.9, direction="UP")
        engine.collect(ctx2, structure_score=0.3, direction="DOWN")
        stats = engine.attribute()
        assert "STATE" in stats
        assert stats["STATE"]["count"] == 2
        assert "avg_contribution" in stats["STATE"]

    def test_rank(self):
        """因子排序"""
        engine = AttributionEngine()
        ctx = make_ctx(state_j2=State.EXTREME_BULL, ma55_pullback=True, j_div=False)
        engine.collect(ctx, structure_score=0.95, direction="UP")
        ranked = engine.rank()
        assert len(ranked) > 0
        # 排序应为降序
        for i in range(1, len(ranked)):
            assert ranked[i - 1][1] >= ranked[i][1]

    def test_attribute_by_outcome(self):
        """按盈亏归因"""
        engine = AttributionEngine()
        ctx1 = make_ctx(state_j2=State.EXTREME_BULL)
        ctx2 = make_ctx(state_j2=State.BEAR)
        engine.collect(ctx1, structure_score=0.9, direction="UP")
        engine.collect(ctx2, structure_score=0.3, direction="DOWN")
        # 回填结果
        engine.update_outcome(0, 0.05)   # 盈利
        engine.update_outcome(1, -0.03)  # 亏损
        result = engine.attribute_by_outcome()
        assert "profitable" in result
        assert "unprofitable" in result
        assert len(result["profitable"]) > 0

    def test_to_dict(self):
        engine = AttributionEngine()
        ctx = make_ctx()
        engine.collect(ctx, structure_score=0.8, direction="UP")
        d = engine.to_dict()
        assert "factors" in d
        assert "attribution" in d
        assert "ranking" in d


# ========== 12. 新增决策路由 (蓝图 v2.1 §3.3) ==========

class TestNewDecisionRouting:
    """蓝图 v2.1 新增的决策路由逻辑"""

    def test_strong_add_requires_space_score(self):
        """STRONG_ADD 要求 Space Score > 0.75"""
        router = DecisionRouterGateway()
        ctx = make_ctx(state_j2=State.EXTREME_BULL, j_div=False, ma55_pullback=True)
        # Space Score = 0.8 > 0.75 → STRONG_ADD
        good_st = SpacetimeScore(time_score=0.5, space_score=0.8, total_score=0.68, reference="good_space")
        decision = router.route(ctx, spacetime_score=good_st, direction="UP")
        assert decision.action == "STRONG_ADD"
        # Space Score = 0.6 <= 0.75 → 降级
        bad_st = SpacetimeScore(time_score=1.0, space_score=0.6, total_score=0.76, reference="bad_space")
        decision2 = router.route(ctx, spacetime_score=bad_st, direction="UP")
        assert decision2.action == "ADD_ON_PULLBACK"

    def test_take_profit_t(self):
        """TAKE_PROFIT_T: 突破遇阻 + 小级别背离 + 动能衰减"""
        router = DecisionRouterGateway()
        # 价格在 MA55 附近 (遇阻) + J-1 背离 + J+2 非极强
        ctx = make_ctx(
            state_j2=State.BULL,        # 非极强 = 动能衰减
            j_div=True,                 # 小级别背离
            ma55_pullback=True,
            price=100.0,
            j_ma55=99.0,               # 价格接近 MA55 (遇阻)
        )
        decision = router.route(ctx, direction="UP")
        assert decision.action == "TAKE_PROFIT_T"
        assert any("做T止盈" in r for r in decision.reasons)

    def test_exit_wait_structure_broken(self):
        """EXIT/WAIT: 结构破坏 (背离 + 跌破 MA55)"""
        router = DecisionRouterGateway()
        ctx = make_ctx(
            state_j2=State.BULL,
            j_div=True,                 # 顶背离
            ma55_pullback=False,
            price=90.0,
            j_ma55=100.0,              # 价格跌破 MA55
        )
        decision = router.route(ctx, direction="UP")
        assert decision.action == "WAIT"
        assert any("退出" in r or "观望" in r for r in decision.reasons)

    def test_exit_wait_spacetime_failed(self):
        """EXIT/WAIT: 时空未达标 (total < 0.5)"""
        router = DecisionRouterGateway()
        ctx = make_ctx(state_j2=State.MID_BULL, j_div=False, ma55_pullback=True)
        bad_st = SpacetimeScore(time_score=0.2, space_score=0.3, total_score=0.26, reference="fail")
        decision = router.route(ctx, spacetime_score=bad_st, direction="UP")
        assert decision.action == "WAIT"

    def test_no_take_profit_when_extreme_bull(self):
        """极强状态下不做 T (动能未衰减)"""
        router = DecisionRouterGateway()
        ctx = make_ctx(
            state_j2=State.EXTREME_BULL,  # 极强 = 动能未衰减
            j_div=True,
            ma55_pullback=True,
            price=100.0,
            j_ma55=99.0,
        )
        decision = router.route(ctx, direction="UP")
        # 极强状态下即使有背离也不做 T
        assert decision.action != "TAKE_PROFIT_T"


# ========== 13. 引擎集成归因 ==========

class TestEngineAttribution:
    """TrinityEngine 集成归因引擎"""

    def test_analyze_collects_attribution(self):
        """analyze 后归因引擎应有记录"""
        engine = TrinityEngine()
        ctx = make_ctx(state_j2=State.EXTREME_BULL)
        engine.analyze(
            ctx, structure_score=0.9, direction="UP",
            duration_curr=18, duration_ref=18,
        )
        assert engine.attribution.count == 1

    def test_attribution_ranking_after_multiple(self):
        """多次分析后可排序因子"""
        engine = TrinityEngine()
        for _ in range(3):
            ctx = make_ctx(state_j2=State.EXTREME_BULL, ma55_pullback=True)
            engine.analyze(ctx, structure_score=0.9, direction="UP", duration_curr=18, duration_ref=18)
        ranked = engine.attribution.rank()
        assert len(ranked) > 0

    def test_attribution_to_dict(self):
        engine = TrinityEngine()
        ctx = make_ctx()
        engine.analyze(ctx, structure_score=0.8, direction="UP", duration_curr=15, duration_ref=18)
        d = engine.attribution.to_dict()
        assert len(d["factors"]) == 1
        assert "attribution" in d
