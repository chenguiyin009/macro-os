from __future__ import annotations

import pytest

from core.features import build_features
from core.fracture_aware_sizer import FractureAwareSizer
from core.policy_engine import PolicyEngine
from core.regime import compute_regime
from core.schemas import FeatureSchema, RegimeType
from core.config_validation import validate_macro_configuration


REGIME_CONFIG = {
    "regime": {
        "risk_on": {
            "tips_yield_roc_60d_max": -0.10,
            "dxy_zscore_60d_max": -1.0,
            "tips_yield_max": 0.5,
            "dxy_max": 100.0,
        },
        "tight_liquidity": {"tips_yield_min": 0.8, "dxy_min": 103.0},
        "liquidity_squeeze": {"vix_min": 25.0, "hy_credit_spread_min": 400},
    }
}


def test_dynamic_regime_inputs_override_absolute_watermarks() -> None:
    raw = FeatureSchema(
        dxy=105.0,
        vix=15.0,
        tips_yield=1.2,
        hy_credit_spread=300,
        tips_yield_roc_60d=-0.12,
        dxy_zscore_60d=-1.2,
    )

    features = build_features(raw)

    assert compute_regime(features, REGIME_CONFIG) == RegimeType.RISK_ON.value


def test_watchlist_risk_dimensions_change_sizer_output() -> None:
    watchlist = {
        "QQQ": {
            "macro_sensitivity": ["RATES"],
            "moat_score": 0.6,
            "logic_stability": 0.5,
            "has_active_catalyst": True,
            "atr_percent_20d": 0.020,
            "beta_to_spy": 1.35,
            "max_portfolio_weight": 0.40,
        },
        "TLT": {
            "macro_sensitivity": ["RATES"],
            "moat_score": 0.6,
            "logic_stability": 0.5,
            "has_active_catalyst": True,
            "atr_percent_20d": 0.008,
            "beta_to_spy": -0.20,
            "max_portfolio_weight": 0.60,
        },
    }

    sizer = FractureAwareSizer(watchlist)
    adjusted = sizer.adjust_weights({"QQQ": 0.6, "TLT": 0.4}, ["RATES"], "MID")

    assert adjusted["QQQ"] < adjusted["TLT"]
    assert adjusted["QQQ"] <= 0.40 + 1e-9


def test_policy_engine_reads_constitution_config() -> None:
    engine = PolicyEngine(
        constitution_config={
            "red_lines": {
                "core_pce_max": 4.0,
                "vix_escape_hatch": 35.0,
            },
            "execution": {
                "max_daily_turnover": 0.10,
                "min_cash_buffer": 0.20,
            },
        }
    )

    result = engine.execute_constitution(
        {"QQQ": 0.6, "CASH": 0.4},
        {"VIX": 20.0, "core_pce": 2.5},
        {"QQQ": 0.0, "CASH": 1.0},
    )

    assert result["target_allocation"]["QQQ"] <= 0.10 + 1e-9
    assert result["target_allocation"]["CASH"] >= 0.20 - 1e-9


def test_validate_macro_configuration_rejects_unbalanced_scoring_weights() -> None:
    thresholds = {
        "scoring": {
            "weights": {
                "regime_base": 0.4,
                "trend_strength": 0.25,
                "volatility_adjust": 0.20,
                "liquidity_adjust": 0.10,
            }
        }
    }

    watchlist = {"assets": {}}

    with pytest.raises(ValueError, match="scoring.weights"):
        validate_macro_configuration(thresholds, watchlist)


def test_validate_macro_configuration_rejects_missing_watchlist_risk_dimensions() -> None:
    thresholds = {
        "scoring": {
            "weights": {
                "regime_base": 0.4,
                "trend_strength": 0.25,
                "volatility_adjust": 0.20,
                "liquidity_adjust": 0.15,
            }
        }
    }
    watchlist = {
        "assets": {
            "QQQ": {
                "macro_sensitivity": ["RATES"],
                "moat_score": 0.6,
                "logic_stability": 0.5,
                "has_active_catalyst": True,
            }
        }
    }

    with pytest.raises(ValueError, match="atr_percent_20d"):
        validate_macro_configuration(thresholds, watchlist)
