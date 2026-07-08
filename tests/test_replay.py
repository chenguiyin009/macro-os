"""Tests for the Replay Engine."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.replay_engine import ReplayEngine, TemporalBuffer, TemporalViolation
from core.regime_labeler import label_regime, compute_max_drawdown, compute_realized_volatility
from core.pnl_simulator import PnLSimulator
from core.evaluation import ConfusionMatrix, transition_accuracy, stability_score, ReplayEvaluation
from core.schemas import Event

import pytest

CONFIG = {
    "regime": {
        "risk_on": {"tips_yield_max": 0.5, "dxy_max": 100.0},
        "tight_liquidity": {"tips_yield_min": 0.8, "dxy_min": 103.0},
        "liquidity_squeeze": {"vix_min": 25.0, "hy_credit_spread_min": 400},
    }
}


class TestTemporalBuffer:
    def test_collect_before_returns_latest(self) -> None:
        buf = TemporalBuffer()
        buf.add({"dxy": 100.0}, "2026-01-01T00:00:00Z")
        buf.add({"dxy": 101.0}, "2026-01-02T00:00:00Z")
        features = buf.collect_before("2026-01-03T00:00:00Z")
        assert features["dxy"] == 101.0

    def test_lookahead_raises_exception(self) -> None:
        buf = TemporalBuffer()
        buf.add({"dxy": 101.0}, "2026-01-03T00:00:00Z")
        with pytest.raises(TemporalViolation, match="LOOKAHEAD VIOLATION"):
            buf.collect_before("2026-01-02T00:00:00Z")

    def test_empty_buffer_returns_default(self) -> None:
        buf = TemporalBuffer()
        features = buf.collect_before("2026-01-01T00:00:00Z")
        assert "dxy" in features


class TestRegimeLabeler:
    def test_max_drawdown_computation(self) -> None:
        prices = [100.0, 110.0, 95.0, 90.0, 105.0]
        dd = compute_max_drawdown(prices)
        assert abs(dd - (-0.1818)) < 0.001

    def test_realized_volatility(self) -> None:
        prices = [100.0, 101.0, 100.5, 102.0, 101.0]
        vol = compute_realized_volatility(prices)
        assert vol > 0.0

    def test_label_squeeze_from_drawdown(self) -> None:
        prices = [100.0, 98.0, 95.0, 92.0, 94.0]
        label = label_regime(prices, drawdown_threshold=-0.05)
        assert label["regime"] == "LIQUIDITY_SQUEEZE"

    def test_label_risk_on(self) -> None:
        prices = [100.0, 101.0, 102.0, 101.5, 103.0]
        label = label_regime(prices, drawdown_threshold=-0.05, volatility_threshold=0.03)
        assert label["regime"] == "RISK_ON"


class TestPnLSimulator:
    def test_no_trade_no_cost(self) -> None:
        sim = PnLSimulator()
        decisions = [{"action": "NO_TRADE", "confidence": 0.0}]
        result = sim.simulate(decisions, [0.01])
        assert result["gross_pnl"] == 0.0
        assert result["total_costs_bps"] == 0.0

    def test_trade_incurs_costs(self) -> None:
        sim = PnLSimulator(spread_bps=1.0, slippage_bps=2.0)
        decisions = [{"action": "LONG", "confidence": 0.8}]
        result = sim.simulate(decisions, [0.01])
        assert result["total_costs_bps"] == 3.0

    def test_regime_switch_penalty(self) -> None:
        sim = PnLSimulator(regime_switch_penalty_bps=5.0)
        decisions = [
            {"action": "LONG", "confidence": 0.8},
            {"action": "NO_TRADE", "confidence": 0.0},
        ]
        result = sim.simulate(decisions, [0.01, -0.01])
        assert result["total_costs_bps"] >= 5.0

    def test_mismatched_arrays_raises(self) -> None:
        sim = PnLSimulator()
        with pytest.raises(ValueError):
            sim.simulate(
                [{"action": "LONG", "confidence": 0.8}],
                [0.01, 0.02],
            )


class TestConfusionMatrix:
    def test_perfect_accuracy(self) -> None:
        cm = ConfusionMatrix()
        for r in ["RISK_ON", "RISK_ON", "TIGHT_LIQUIDITY", "TIGHT_LIQUIDITY"]:
            cm.add(r, r)
        assert cm.accuracy() == 1.0

    def test_partial_accuracy(self) -> None:
        cm = ConfusionMatrix()
        cm.add("RISK_ON", "RISK_ON")
        cm.add("TIGHT_LIQUIDITY", "RISK_ON")
        assert cm.accuracy() == 0.5

    def test_empty_matrix(self) -> None:
        cm = ConfusionMatrix()
        assert cm.accuracy() == 0.0


class TestTransitionAccuracy:
    def test_all_transitions_correct(self) -> None:
        actual = ["A", "A", "B", "B", "C"]
        predicted = ["A", "A", "B", "B", "C"]
        assert transition_accuracy(actual, predicted) == 1.0


class TestStabilityScore:
    def test_no_switches_max_stability(self) -> None:
        assert stability_score(["A", "A", "A"], 3) == 1.0


class TestReplayEngine:
    def create_test_events(self, path: Path) -> None:
        events = [
            Event(source="MOCK", symbol="MACRO", event_type="INIT",
                   ts="2026-01-01T00:00:00Z", payload={}),
            Event(source="MOCK", symbol="MACRO", event_type="DECISION",
                   ts="2026-01-02T00:00:00Z",
                   payload={"action": "NO_TRADE", "confidence": 0.0,
                            "risk_score": 0.5,
                            "features": {"dxy": 104.0, "vix": 18.0,
                                         "tips_yield": 0.6, "hy_credit_spread": 300}}),
        ]
        with open(path, "w") as f:
            for e in events:
                f.write(e.to_jsonl() + "\n")

    def test_replay_on_empty_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.jsonl"
            path.write_text("")
            engine = ReplayEngine(path)
            result = engine.run()
            assert "error" in result

    def test_replay_with_minimal_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            self.create_test_events(path)
            engine = ReplayEngine(path, config=CONFIG)
            result = engine.run()
            assert "error" not in result
            assert "confusion_matrix" in result
            assert "pnl" in result

    def test_replay_save_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            self.create_test_events(path)
            output_dir = Path(tmpdir) / "output"
            engine = ReplayEngine(path, config=CONFIG)
            metrics = engine.run()
            engine.save_results(metrics, output_dir)
            assert (output_dir / "REPLAY_RESULTS.json").exists()

    def test_temporal_violation_during_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            events = [
                Event(source="MOCK", symbol="MACRO", event_type="DECISION",
                       ts="2026-01-02T00:00:00Z",
                       payload={"features": {"dxy": 105.0}}),
            ]
            with open(path, "w") as f:
                for e in events:
                    f.write(e.to_jsonl() + "\n")
            engine = ReplayEngine(path, config=CONFIG)
            engine.buffer.add({"dxy": 105.0}, "2026-01-05T00:00:00Z")
            with pytest.raises(TemporalViolation, match="LOOKAHEAD VIOLATION"):
                engine.run()
