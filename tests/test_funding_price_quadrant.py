"""Funding-price research quadrant (weekly language Q1-Q4) unit tests."""

from __future__ import annotations

import json
from pathlib import Path

from core.features import build_features
from core.research.funding_price_quadrant import (
    FundingPriceQuadrant,
    assessment_from_week_snapshot,
    classify_funding_price_quadrant,
)
from core.schemas import FeatureSchema


ROOT = Path(__file__).resolve().parents[1]
WEEK_JSON = ROOT / "data" / "research" / "funding_price_week_2026-07-06.json"


def test_week_snapshot_is_q1_stress_test() -> None:
    snap = json.loads(WEEK_JSON.read_text(encoding="utf-8"))
    assessment = assessment_from_week_snapshot(snap)
    assert assessment.quadrant == FundingPriceQuadrant.Q1_STRESS_TEST
    assert assessment.hard_regime_hint == "TIGHT_LIQUIDITY"
    assert assessment.hard_regime_hint != "LIQUIDITY_SQUEEZE"
    assert assessment.credit_stable is True
    assert assessment.usd_breakout is False


def test_classify_from_weekly_feature_levels() -> None:
    feats = {
        "tips_yield": 2.32,
        "tips_yield_change_5d_bp": 14.0,
        "nominal_10y": 4.55,
        "nominal_10y_change_5d_bp": 17.0,
        "nominal_30y": 5.06,
        "nominal_30y_change_5d_bp": 19.0,
        "dxy": 101.12,
        "hy_credit_spread": 320.0,
        "credit_stable": True,
        "usd_breakout": False,
    }
    a = classify_funding_price_quadrant(feats)
    assert a.quadrant == FundingPriceQuadrant.Q1_STRESS_TEST
    assert a.hard_regime_hint == "TIGHT_LIQUIDITY"
    assert "tips_10y_real" in a.dominant_drivers


def test_q1_does_not_map_to_liquidity_squeeze() -> None:
    a = classify_funding_price_quadrant(
        {
            "tips_yield_change_5d_bp": 10.0,
            "nominal_30y_change_5d_bp": 12.0,
            "hy_credit_spread": 300.0,
        }
    )
    assert a.quadrant == FundingPriceQuadrant.Q1_STRESS_TEST
    assert a.hard_regime_hint != "LIQUIDITY_SQUEEZE"


def test_q3_ease_branch() -> None:
    a = classify_funding_price_quadrant(
        {
            "tips_yield_change_5d_bp": -8.0,
            "nominal_10y_change_5d_bp": -10.0,
            "hy_credit_spread": 280.0,
        }
    )
    assert a.quadrant == FundingPriceQuadrant.Q3_POLICY_EASE
    assert a.hard_regime_hint == "RISK_ON"


def test_build_features_passes_nominal_curve() -> None:
    raw = FeatureSchema(
        tips_yield=2.32,
        nominal_10y=4.55,
        nominal_30y=5.06,
        nominal_2y=4.19,
        bei_10y=2.26,
        tips_yield_change_5d_bp=14.0,
        nominal_30y_change_5d_bp=19.0,
    )
    feats = build_features(raw)
    assert feats["nominal_10y"] == 4.55
    assert feats["nominal_30y"] == 5.06
    assert feats["bei_10y"] == 2.26
    assert feats["tips_yield_change_5d_bp"] == 14.0
