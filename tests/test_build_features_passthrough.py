"""PR-3 回归：build_features 透传红线/编排层所需字段。

红线模块只消费既有已接线特征，不新增平行 macro_* 五件套。
recovery_signal / risk_score 供 orchestrator；danger 归 policy_engine（0-100）。
"""
from __future__ import annotations

from core.features import build_features
from core.schemas import FeatureSchema


def test_build_features_passes_red_line_inputs() -> None:
    raw = FeatureSchema(vix=42.0, hy_credit_spread=520.0, dxy=104.0)
    feats = build_features(raw)
    assert feats.get("vix") == 42.0
    assert feats.get("hy_credit_spread") == 520.0


def test_build_features_passes_core_pce_percent() -> None:
    raw = FeatureSchema(vix=42.0, hy_credit_spread=520.0, dxy=104.0, core_pce=3.8)
    feats = build_features(raw)
    assert feats.get("core_pce") == 3.8


def test_build_features_passes_risk_and_recovery_fields() -> None:
    raw = FeatureSchema(
        ovx=55.0,
        danger_score=12.0,
        fragility_score=1.5,
        risk_score=0.4,
        recovery_signal=True,
    )
    feats = build_features(raw)
    assert feats["ovx"] == 55.0
    assert feats["danger_score"] == 12.0
    assert feats["fragility_score"] == 1.5
    assert feats["risk_score"] == 0.4
    assert feats["recovery_signal"] is True


def test_build_features_omits_unset_optional_macro_fields() -> None:
    feats = build_features(FeatureSchema())
    assert "vix" not in feats
    assert "hy_credit_spread" not in feats
    assert "core_pce" not in feats
    assert "ovx" not in feats
    # schema defaults still surface for scoring/orchestrator consumers
    assert feats["danger_score"] == 0.0
    assert feats["risk_score"] == 0.0
    assert feats["recovery_signal"] is False


def test_hy_alias_jnk_populates_schema() -> None:
    fs = FeatureSchema.model_validate({"jnk": 450})
    assert fs.hy_credit_spread == 450
