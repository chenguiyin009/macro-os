"""PR-3 回归：build_features 已透传红线所需特征（vix / hy_credit_spread）。

红线模块只消费既有已接线特征，不新增平行 macro_* 五件套；
若 build_features 漏透传，红线在真实数据上会永远沉默。
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
    # core_pce 为百分比量纲，必须透传，否则 core_pce_max 红线在真实数据上永远沉默
    raw = FeatureSchema(vix=42.0, hy_credit_spread=520.0, dxy=104.0, core_pce=3.8)
    feats = build_features(raw)
    assert feats.get("core_pce") == 3.8


def test_build_features_omits_unset_fields() -> None:
    feats = build_features(FeatureSchema())
    # 未提供的宏观特征不应出现在特征 dict 中（避免红线误判）
    assert "vix" not in feats
    assert "hy_credit_spread" not in feats
    assert "core_pce" not in feats
