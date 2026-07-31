"""Phase 0 tests: CN tech sentiment shadow (observation only)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from adapters.sentiment.providers import ManualJsonProvider, MockScenarioProvider
from core.sentiment.engine import SentimentShadowEngine, compute_anchor
from core.sentiment.feishu_card import render_observation_card
from core.sentiment.models import SentimentRawInput
from core.sentiment.types import (
    FLAG_BOUNCE_NOT_REVERSAL,
    FLAG_DRAG_NOT_LIFT,
    FLAG_GAP_UP_FADE,
    FLAG_LATE_BROAD_ETF_BID,
    FLAG_MARGIN_PRESSURE_RELEASE,
    FLAG_MARGIN_TOP100_STRESS,
    FLAG_TRANSMIT_CSI1000,
    FLAG_WAIT_BROAD_BETA,
    InterventionMode,
    TransmissionStage,
)

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "sentiment"


def _engine() -> SentimentShadowEngine:
    return SentimentShadowEngine(
        {
            "sse_anchor_px": 3750,
            "sse_anchor_buffer_pct": 0.008,
            "thresholds": {
                "margin_stress_limit_down": 10,
                "margin_release_rate_high": 0.7,
                "resonance_s4_min": 0.65,
                "corr_crowded": 0.7,
            },
            "playbook_hints": {
                "hs300_offset": -0.005,
                "chinext_offset": -0.01,
                "csi1000_offset": -0.02,
            },
        }
    )


def test_anchor_distance_3750():
    bp, state, _ = compute_anchor(3750.0, 3750.0, 0.008)
    assert bp == pytest.approx(0.0)
    assert state == "IN_ZONE"
    bp2, state2, _ = compute_anchor(3742.0, 3750.0, 0.008)
    assert bp2 < 0
    assert state2 in ("IN_ZONE", "BREAK_TEST")


def test_fixture_2026_07_17_stress_and_drag():
    eng = _engine()
    raw = MockScenarioProvider().fetch("2026-07-17", "AFTERNOON")
    snap = eng.compute(raw)
    assert snap.margin_top100_limit_down_n == 18
    assert snap.intervention_mode == InterventionMode.DRAG_NOT_LIFT.value
    assert FLAG_DRAG_NOT_LIFT in snap.corroboration_flags
    assert FLAG_MARGIN_TOP100_STRESS in snap.corroboration_flags
    assert snap.cycle.stage in ("S1", "S2")
    assert snap.liquidity_transmission_stage == TransmissionStage.HS300_ONLY.value
    assert snap.cycle.rebound_type in ("NONE", "TACTICAL")


def test_fixture_2026_07_21_release_and_transmission():
    eng = _engine()
    raw = MockScenarioProvider().fetch("2026-07-21", "MORNING")
    snap = eng.compute(raw)
    assert snap.margin_top100_open_board_n == 20
    assert snap.deleveraging_release_rate is not None
    assert snap.deleveraging_release_rate >= 0.7
    assert FLAG_MARGIN_PRESSURE_RELEASE in snap.corroboration_flags
    assert snap.liquidity_transmission_stage == TransmissionStage.TO_CSI1000.value
    assert FLAG_TRANSMIT_CSI1000 in snap.corroboration_flags
    assert snap.intervention_mode == InterventionMode.DRAG_NOT_LIFT.value
    assert FLAG_BOUNCE_NOT_REVERSAL in snap.corroboration_flags
    assert FLAG_WAIT_BROAD_BETA in snap.corroboration_flags
    assert FLAG_GAP_UP_FADE in snap.corroboration_flags
    assert FLAG_LATE_BROAD_ETF_BID in snap.corroboration_flags
    assert snap.cycle.stage in ("S2", "S3")
    assert snap.cycle.rebound_type == "TACTICAL"
    # must NOT high-confidence S4 without resonance
    assert snap.cycle.stage != "S4"


def test_missing_margin_data_partial_no_fabrication():
    eng = _engine()
    raw = SentimentRawInput(
        as_of="2026-07-21",
        session="MORNING",
        source="MOCK",
        sse_last=3750.0,
        # margin fields omitted
    )
    snap = eng.compute(raw)
    assert snap.margin_top100_limit_down_n is None
    assert snap.margin_top100_open_board_n is None
    assert snap.deleveraging_release_rate is None
    assert snap.quality in ("PARTIAL", "DEGRADED", "OK")
    assert "margin_top100_limit_down_n" in snap.missing_inputs


def test_feishu_card_shadow_only_and_delta():
    eng = _engine()
    s17 = eng.compute(MockScenarioProvider().fetch("2026-07-17", "AFTERNOON"))
    s21 = eng.compute(MockScenarioProvider().fetch("2026-07-21", "MORNING"))
    title, body = render_observation_card(s21, previous=s17)
    assert "Shadow only" in body
    assert "不修改 Kernel" in body
    assert "Δ 上一日" in body
    assert "CN Tech Cycle" in title


def test_manual_json_provider():
    eng = _engine()
    raw = ManualJsonProvider(FIX / "raw_2026-07-21.json").fetch("2026-07-21", "MORNING")
    snap = eng.compute(raw)
    assert snap.source == "MANUAL"
    assert FLAG_TRANSMIT_CSI1000 in snap.corroboration_flags


def test_decision_kernel_does_not_import_sentiment():
    kernel_path = ROOT / "core" / "decision_kernel.py"
    tree = ast.parse(kernel_path.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "sentiment" not in alias.name
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert "sentiment" not in mod


def test_s4_not_triggered_without_resonance():
    eng = _engine()
    raw = SentimentRawInput(
        as_of="2026-08-01",
        session="CLOSE",
        source="MOCK",
        sse_last=3900.0,
        volume_thrust=0.2,
        advance_decline_tech=0.3,
        margin_top100_limit_down_n=2,
        margin_top100_open_board_n=2,
        margin_top100_new_limit_down_n=0,
        sector_index_resonance=0.2,  # weak
        transmission_hs300=0.8,
        transmission_chinext=0.8,
        transmission_csi1000=0.8,
        gjd_proxy_score=0.3,
    )
    snap = eng.compute(raw)
    assert snap.cycle.stage != "S4" or snap.cycle.stage_confidence < 0.55

