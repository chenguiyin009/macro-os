"""Tests for core.regime."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.schemas import RegimeType
from core.regime import compute_regime
from core.macro_state.regime import _determine_risk_on


CONFIG = {
    "regime": {
        "risk_on": {"tips_yield_max": 0.5, "dxy_max": 100.0},
        "tight_liquidity": {"tips_yield_min": 0.8, "dxy_min": 103.0},
        "liquidity_squeeze": {"vix_min": 25.0, "hy_credit_spread_min": 400},
    }
}


class TestComputeRegime:
    def test_risk_on(self) -> None:
        features = {"dxy": 98.0, "vix": 15.0, "tips_yield": 0.3, "hy_credit_spread": 250}
        assert compute_regime(features, CONFIG) == RegimeType.RISK_ON.value

    def test_tight_liquidity(self) -> None:
        features = {"dxy": 105.0, "vix": 20.0, "tips_yield": 1.0, "hy_credit_spread": 350}
        assert compute_regime(features, CONFIG) == RegimeType.TIGHT_LIQUIDITY.value

    def test_liquidity_squeeze_vix(self) -> None:
        features = {"dxy": 98.0, "vix": 30.0, "tips_yield": 0.3, "hy_credit_spread": 250}
        assert compute_regime(features, CONFIG) == RegimeType.LIQUIDITY_SQUEEZE.value

    def test_liquidity_squeeze_hy(self) -> None:
        features = {"dxy": 98.0, "vix": 15.0, "tips_yield": 0.3, "hy_credit_spread": 450}
        assert compute_regime(features, CONFIG) == RegimeType.LIQUIDITY_SQUEEZE.value

    def test_transition_mixed_signals(self) -> None:
        features = {"dxy": 101.0, "vix": 18.0, "tips_yield": 0.6, "hy_credit_spread": 350}
        assert compute_regime(features, CONFIG) == RegimeType.TRANSITION.value

    def test_empty_features(self) -> None:
        assert compute_regime({}, CONFIG) == RegimeType.TRANSITION.value

    def test_missing_keys_fall_back_to_transition(self) -> None:
        features = {"dxy": 98.0}
        assert compute_regime(features, CONFIG) == RegimeType.TRANSITION.value

    def test_high_vix_overrides_all(self) -> None:
        features = {"dxy": 98.0, "vix": 35.0, "tips_yield": 0.3}
        assert compute_regime(features, CONFIG) == RegimeType.LIQUIDITY_SQUEEZE.value

    def test_default_config_fallback(self) -> None:
        features = {"dxy": 98.0, "vix": 15.0, "tips_yield": 0.3}
        assert compute_regime(features, {}) == RegimeType.RISK_ON.value


# ================================================================
# ZIRP Trap Removal Tests (v2.0, 2026-07-18)
# ================================================================

@pytest.fixture
def zirp_config():
    return {
        "regime": {
            "risk_on": {
                "tips_absolute_max": 2.5,
                "tips_roc_60d_threshold": -0.15,
                "tips_percentile_2y": 0.25,
                "min_signals_required": 1,
            }
        }
    }


@pytest.fixture
def tips_history_2y():
    """504 business days of simulated TIPS yields centered at 2.3%."""
    np.random.seed(42)
    dates = pd.date_range(end="2026-07-17", periods=504, freq="B")
    yields = 2.3 + np.random.randn(504) * 0.3
    return pd.Series(yields, index=dates)


class TestRiskOnZIRP:
    """Tests for the ZIRP-trap-removal RISK_ON gate."""

    def test_hard_ceiling_blocks(self, zirp_config):
        """TIPS > 2.5% triggers one-vote veto."""
        history = pd.Series([2.0] * 504)
        assert _determine_risk_on(3.0, history, zirp_config) is False

    def test_roc_signal_triggers(self, zirp_config):
        """60-day ROC below -15% triggers RISK_ON."""
        # 445 days at 2.5% + 59 days at 2.0% = 504 total
        # iloc[-60] = 2.5, current = 2.0 -> ROC = -20% < -15%
        history = pd.Series([2.5] * 445 + [2.0] * 59)
        result = _determine_risk_on(2.0, history, zirp_config)
        assert result is True

    def test_percentile_signal_triggers(self, zirp_config, tips_history_2y):
        """2-year percentile below 25% triggers RISK_ON."""
        tips_history_2y.iloc[-1] = tips_history_2y.min() - 0.1
        result = _determine_risk_on(tips_history_2y.iloc[-1], tips_history_2y, zirp_config)
        assert result is True

    def test_no_signal_no_risk_on(self, zirp_config, tips_history_2y):
        """No marginal signals -> RISK_ON denied (ZIRP trap removed)."""
        result = _determine_risk_on(2.3, tips_history_2y, zirp_config)
        assert result is False
