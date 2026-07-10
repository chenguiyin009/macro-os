"""TemporalBuffer 与 ReplayEngine 测试 (蓝图阶段二)"""
from __future__ import annotations

import pytest

from trinity.context import OHLCV
from trinity.replay import ReplayEngine, TemporalBuffer


def make_ohlcv(n: int) -> list[OHLCV]:
    return [
        OHLCV(timestamp=i, open=10 + i, high=11 + i, low=9 + i, close=10 + i, volume=1000)
        for i in range(n)
    ]


class TestTemporalBuffer:
    """防未来函数缓冲区"""

    def test_initial_state(self):
        buf = TemporalBuffer(make_ohlcv(100))
        assert buf.cursor == 0
        assert buf.visible_length == 1
        assert buf.total_length == 100
        assert buf.has_more

    def test_advance(self):
        buf = TemporalBuffer(make_ohlcv(10))
        assert buf.advance()  # cursor → 1
        assert buf.cursor == 1
        assert buf.visible_length == 2

    def test_advance_to_end(self):
        buf = TemporalBuffer(make_ohlcv(3))
        assert buf.advance()  # → 1
        assert buf.advance()  # → 2
        assert not buf.advance()  # 已到末尾
        assert buf.cursor == 2

    def test_visible_only_past(self):
        """visible() 只返回当前及之前的数据"""
        buf = TemporalBuffer(make_ohlcv(50))
        buf.seek(10)
        visible = buf.visible()
        assert len(visible) == 11  # 0..10
        assert visible[-1].close == 20  # close = 10 + index = 10 + 10

    def test_peek_future_warning(self):
        """peek(offset>0) 可以看到未来 (回测中应避免)"""
        buf = TemporalBuffer(make_ohlcv(10))
        buf.seek(5)
        future = buf.peek(offset=1)
        assert future is not None
        assert future.close == 16  # index=6, close=10+6

    def test_peek_past(self):
        buf = TemporalBuffer(make_ohlcv(10))
        buf.seek(5)
        past = buf.peek(offset=-1)
        assert past.close == 14  # index=4

    def test_reset(self):
        buf = TemporalBuffer(make_ohlcv(10))
        buf.seek(5)
        buf.reset()
        assert buf.cursor == 0

    def test_seek(self):
        buf = TemporalBuffer(make_ohlcv(10))
        buf.seek(7)
        assert buf.cursor == 7
        assert buf.visible_length == 8

    def test_seek_clamp(self):
        """seek 超出范围时自动钳制"""
        buf = TemporalBuffer(make_ohlcv(10))
        buf.seek(100)
        assert buf.cursor == 9
        buf.seek(-5)
        assert buf.cursor == 0

    def test_getitem_protects_future(self):
        """索引访问不允许超出可见范围"""
        buf = TemporalBuffer(make_ohlcv(10))
        buf.seek(3)
        # 可以访问 0..3
        assert buf[3].close == 13
        # 不能访问 4 (未来)
        with pytest.raises(IndexError, match="可见范围"):
            _ = buf[4]

    def test_negative_index(self):
        """负索引从可见范围末尾算"""
        buf = TemporalBuffer(make_ohlcv(10))
        buf.seek(4)
        assert buf[-1].close == 14  # visible[4]
        assert buf[-5].close == 10  # visible[0]

    def test_iter_visible(self):
        buf = TemporalBuffer(make_ohlcv(5))
        buf.seek(2)
        items = list(buf)
        assert len(items) == 3

    def test_empty_buffer(self):
        buf = TemporalBuffer([])
        assert buf.total_length == 0
        assert not buf.has_more
        assert buf.current() is None


class TestReplayEngine:
    """回测引擎"""

    def test_run_basic(self):
        data = make_ohlcv(50)
        engine = None  # 不需要真实引擎, 用基础 _step
        replay = ReplayEngine(engine, data, symbol="TEST")
        results = replay.run(min_bars=10)
        # 50 bars, min_bars=10: cursor 9..49 = 41 步
        assert len(results) == 41
        assert results[0]["cursor"] == 9  # 第一次满足 min_bars=10 时 cursor=9
        assert results[-1]["cursor"] == 49

    def test_run_min_bars(self):
        """min_bars 过大时结果少"""
        data = make_ohlcv(20)
        replay = ReplayEngine(None, data)
        results = replay.run(min_bars=15)
        # cursor 14..19 = 6 步
        assert len(results) == 6

    def test_summary(self):
        data = make_ohlcv(30)
        replay = ReplayEngine(None, data, symbol="REPLAY")
        replay.run(min_bars=10)
        summary = replay.summary()
        assert summary["symbol"] == "REPLAY"
        assert summary["total_bars"] == 30
        assert summary["total_steps"] > 0

    def test_no_look_ahead(self):
        """验证回测中不会访问未来数据"""
        data = make_ohlcv(20)
        replay = ReplayEngine(None, data)
        results = replay.run(min_bars=5)
        # 每步的 visible_length 应等于 cursor+1, 不超过总长度
        for r in results:
            assert r["visible_length"] == r["cursor"] + 1
            assert r["visible_length"] <= 20


class TestTemporalBufferIntegration:
    """TemporalBuffer 与引擎集成"""

    def test_with_trinity_engine(self):
        """TemporalBuffer + TrinityEngine 不崩溃"""
        from trinity.core.engine import TrinityEngine, JLevelContext, State
        from datetime import datetime

        data = make_ohlcv(60)
        engine = TrinityEngine()
        buf = TemporalBuffer(data)
        buf.seek(29)  # 从 visible_length=30 开始
        decisions_made = 0

        while True:
            visible = buf.visible()
            # 用可见数据构造上下文 (简化)
            ctx = JLevelContext(
                symbol="INTEGRATION",
                timestamp=datetime.now(),
                state_j2=State.BULL,
                state_j=State.MID_BULL,
                j_minus_1_has_divergence=False,
                j_close=visible[-1].close,
                j_ma55=visible[-1].close * 0.95,
                j_has_completed_ma55_pullback=True,
                price=visible[-1].close,
            )
            decision = engine.analyze(ctx, structure_score=0.8, direction="UP",
                                      duration_curr=18, duration_ref=18)
            decisions_made += 1

            if not buf.advance():
                break

        assert decisions_made > 0
        # 归因引擎应收集了所有决策
        assert engine.attribution.count == decisions_made


class TestIronLaws:
    """蓝图三条铁律的回归断言

    铁律 #1 (延迟双指针) 与 铁律 #2 (不可变证据链) 已由代码实现,
    此处用显式断言锁定契约, 防止未来重构破坏。
    铁律 #3 (统计显著性) 的断言见 tests/test_alpha_report.py::
    test_significance_guard_blocks_weight_advice_under_10。
    """

    def test_law1_delayed_two_pointer_physical_identity(self):
        """铁律#1: 实时流只可见 cursor 及之前的数据, 且返回物理同一对象"""
        buf = TemporalBuffer(make_ohlcv(20))
        buf.seek(5)
        visible = buf.visible()
        # 物理引用地址一致性: visible 末元素就是 current 所指对象 (非拷贝)
        assert visible[-1] is buf.current()
        # 时间轴因果一致性: visible 是完整序列的前缀, 不含任何未来数据
        assert visible == make_ohlcv(20)[:6]
        assert len(visible) == buf.cursor + 1
        assert len(visible) <= buf.total_length

    def test_law1_future_data_truncation(self):
        """铁律#1: 禁止访问未来索引 (越界即截断/抛错)"""
        buf = TemporalBuffer(make_ohlcv(10))
        buf.seek(3)
        # 当前可见 0..3, 访问未来索引 4 必须失败
        with pytest.raises(IndexError, match="可见范围"):
            _ = buf[4]
        # peek(offset>0) 属于显式"应避免"通道, 可调用但不应进入决策主路径
        assert buf.peek(offset=1) is not None

    def test_law2_immutable_provenance(self):
        """铁律#2: 事件一旦持久化不可原地修改 (快照式 record, 追加不可变)"""
        from trinity.ledger import EventSourcingTracker
        from trinity.context import (
            MacroState, StructureType, TrendDirection, TradingLevel,
            JLevelContext, StructureEvidence, SpacetimeScore,
        )
        from trinity.decision_router import DecisionRouter

        tracker = EventSourcingTracker()
        router = DecisionRouter()
        j2 = JLevelContext(
            level=TradingLevel.J_PLUS_2, symbol="X", state=MacroState.STRONG,
            structure=StructureEvidence(structure_type=StructureType.A, direction=TrendDirection.UP),
        )
        j = JLevelContext(
            level=TradingLevel.J, symbol="X", state=MacroState.STRONG,
            structure=StructureEvidence(structure_type=StructureType.A, direction=TrendDirection.UP),
        )
        j1 = JLevelContext(level=TradingLevel.J_MINUS_1, symbol="X")
        j2m = JLevelContext(level=TradingLevel.J_MINUS_2, symbol="X")
        sp = SpacetimeScore(time_score=0.6, space_score=0.7)
        decision = router.route(j2, j, j1, j2m, sp)

        tracker.record(
            {TradingLevel.J_PLUS_2: j2, TradingLevel.J: j,
             TradingLevel.J_MINUS_1: j1, TradingLevel.J_MINUS_2: j2m},
            decision,
        )
        # 记录后再篡改原始决策对象
        original_conf = tracker._events[0].decision["confidence"]
        decision.confidence = -999.0  # 篡改原始对象
        # 已持久化的事件不受影响 —— 不可变证据链 (快照式 record)
        assert tracker._events[0].decision["confidence"] == original_conf
