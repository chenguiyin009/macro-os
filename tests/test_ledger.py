"""事件溯源账本测试"""
from __future__ import annotations

import json
import os
import tempfile

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
from trinity.ledger import EventSourcingTracker, validate_ledger


def make_decision(
    action=ActionType.STRONG_ADD,
    confidence=0.85,
    symbol="TEST",
    evidence=None,
) -> Decision:
    return Decision(
        action=action,
        confidence=confidence,
        spacetime=SpacetimeScore(time_score=0.8, space_score=0.9),
        risk_level=0.2,
        level=TradingLevel.J,
        evidence=evidence or ["test evidence 1", "test evidence 2"],
        note="test note",
        symbol=symbol,
    )


def make_contexts(symbol="TEST"):
    return {
        TradingLevel.J_PLUS_2: JLevelContext(
            level=TradingLevel.J_PLUS_2, symbol=symbol, state=MacroState.EXTREME_STRONG,
        ),
        TradingLevel.J: JLevelContext(
            level=TradingLevel.J, symbol=symbol, state=MacroState.EXTREME_STRONG,
            structure=StructureEvidence(structure_type=StructureType.A, direction=TrendDirection.UP),
        ),
        TradingLevel.J_MINUS_1: JLevelContext(level=TradingLevel.J_MINUS_1, symbol=symbol),
        TradingLevel.J_MINUS_2: JLevelContext(level=TradingLevel.J_MINUS_2, symbol=symbol),
    }


class TestRecordAndReplay:
    """记录与回放"""

    def test_record_single_event(self):
        tracker = EventSourcingTracker()
        ctx = make_contexts()
        decision = make_decision()
        event = tracker.record(ctx, decision)

        assert event.event_id == "EVT-000001"
        assert event.symbol == "TEST"
        assert event.decision["action"] == "STRONG_ADD"
        assert len(event.evidence) == 2
        assert "J+2" in event.contexts

    def test_record_multiple_events(self):
        tracker = EventSourcingTracker()
        for i in range(5):
            tracker.record(make_contexts(), make_decision(confidence=0.5 + i * 0.1))
        assert tracker.count == 5
        events = tracker.replay()
        assert len(events) == 5
        assert events[0].event_id == "EVT-000001"
        assert events[-1].event_id == "EVT-000005"

    def test_replay_for_symbol(self):
        tracker = EventSourcingTracker()
        tracker.record(make_contexts("AAA"), make_decision(symbol="AAA"))
        tracker.record(make_contexts("BBB"), make_decision(symbol="BBB"))
        tracker.record(make_contexts("AAA"), make_decision(symbol="AAA"))
        aaa = tracker.replay_for_symbol("AAA")
        assert len(aaa) == 2

    def test_event_id_increment(self):
        tracker = EventSourcingTracker()
        e1 = tracker.record(make_contexts(), make_decision())
        e2 = tracker.record(make_contexts(), make_decision())
        assert e1.event_id != e2.event_id


class TestQuery:
    """查询"""

    def test_query_by_action(self):
        tracker = EventSourcingTracker()
        tracker.record(make_contexts(), make_decision(action=ActionType.STRONG_ADD))
        tracker.record(make_contexts(), make_decision(action=ActionType.HOLD))
        tracker.record(make_contexts(), make_decision(action=ActionType.STRONG_ADD))
        result = tracker.query(action="STRONG_ADD")
        assert len(result) == 2

    def test_query_by_confidence(self):
        tracker = EventSourcingTracker()
        tracker.record(make_contexts(), make_decision(confidence=0.3))
        tracker.record(make_contexts(), make_decision(confidence=0.9))
        result = tracker.query(min_confidence=0.8)
        assert len(result) == 1
        assert result[0].decision["confidence"] == 0.9


class TestSummary:
    """摘要统计"""

    def test_summary(self):
        tracker = EventSourcingTracker()
        tracker.record(make_contexts(), make_decision(action=ActionType.STRONG_ADD, symbol="A"))
        tracker.record(make_contexts(), make_decision(action=ActionType.HOLD, symbol="B"))
        summary = tracker.summary()
        assert summary["total_events"] == 2
        assert summary["actions"]["STRONG_ADD"] == 1
        assert summary["actions"]["HOLD"] == 1
        assert summary["symbols"]["A"] == 1


class TestSaveLoad:
    """保存与加载"""

    def test_save_and_load(self):
        tracker = EventSourcingTracker()
        tracker.record(make_contexts(), make_decision())
        tracker.record(make_contexts(), make_decision())

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            filepath = f.name

        try:
            tracker.save(filepath)
            assert os.path.exists(filepath)

            # 加载到新 tracker
            tracker2 = EventSourcingTracker()
            tracker2.load(filepath)
            assert tracker2.count == 2
            events = tracker2.replay()
            assert events[0].symbol == "TEST"
            assert events[0].decision["action"] == "STRONG_ADD"
        finally:
            os.unlink(filepath)

    def test_load_preserves_counter(self):
        """加载后计数器继续递增"""
        tracker = EventSourcingTracker()
        tracker.record(make_contexts(), make_decision())
        tracker.record(make_contexts(), make_decision())

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            filepath = f.name

        try:
            tracker.save(filepath)
            tracker2 = EventSourcingTracker()
            tracker2.load(filepath)
            # 新事件 ID 应从 3 开始
            e = tracker2.record(make_contexts(), make_decision())
            assert e.event_id == "EVT-000003"
        finally:
            os.unlink(filepath)

    def test_clear(self):
        tracker = EventSourcingTracker()
        tracker.record(make_contexts(), make_decision())
        tracker.clear()
        assert tracker.count == 0


class TestValidateLedger:
    """账本校验"""

    def test_valid_ledger(self):
        tracker = EventSourcingTracker()
        tracker.record(make_contexts(), make_decision())
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            filepath = f.name
        try:
            tracker.save(filepath)
            ok, errors = validate_ledger(filepath)
            assert ok, f"校验失败: {errors}"
            assert len(errors) == 0
        finally:
            os.unlink(filepath)

    def test_missing_file(self):
        ok, errors = validate_ledger("/nonexistent/path.json")
        assert not ok
        assert any("不存在" in e for e in errors)

    def test_empty_ledger(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            filepath = f.name
            json.dump({"version": "2.1.0", "events": []}, f)
        try:
            ok, errors = validate_ledger(filepath)
            assert not ok
            assert any("为空" in e for e in errors)
        finally:
            os.unlink(filepath)

    def test_corrupt_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            filepath = f.name
            f.write("{invalid json")
        try:
            ok, errors = validate_ledger(filepath)
            assert not ok
            assert any("JSON" in e for e in errors)
        finally:
            os.unlink(filepath)

    def test_duplicate_ids(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            filepath = f.name
            event = {
                "event_id": "EVT-000001",
                "timestamp": 0,
                "symbol": "X",
                "contexts": {},
                "decision": {"action": "HOLD", "confidence": 0.5},
                "evidence": ["e1"],
            }
            json.dump({"version": "2.1.0", "events": [event, event]}, f)
        try:
            ok, errors = validate_ledger(filepath)
            assert not ok
            assert any("重复" in e for e in errors)
        finally:
            os.unlink(filepath)

    def test_invalid_action(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            filepath = f.name
            event = {
                "event_id": "EVT-000001",
                "timestamp": 0,
                "symbol": "X",
                "contexts": {},
                "decision": {"action": "INVALID_ACTION", "confidence": 0.5},
                "evidence": ["e1"],
            }
            json.dump({"version": "2.1.0", "events": [event]}, f)
        try:
            ok, errors = validate_ledger(filepath)
            assert not ok
            assert any("未知动作" in e for e in errors)
        finally:
            os.unlink(filepath)
