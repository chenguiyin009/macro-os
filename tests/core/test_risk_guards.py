"""Regression guards for the three previously-untested core/ risk modules.

IMPORTANT: these tests pin the EXISTING public behavior of
``core.exposure_engine``, ``core.budget_tree`` and ``core.hmm_inference``.
The original engineering spec assumed a different (fictional) API for these
modules; this suite is written against the real source so it is pure-additive
and cannot break the existing suite.

Goal: detect *silent* regressions in the decision/risk logic — the single
most dangerous failure mode for an automated risk-control system.
"""
import json
import math

import pytest

from core.exposure_engine import ExposureEngine
from core.budget_tree import (
    AllocationProposal,
    AssetClassBudget,
    SectorBudget,
    TechSubSectorBudget,
)
from core.hmm_inference import DEFAULT_PROBS, HMMModel
from core.schemas import RegimeType


class TestExposureEngine:
    """Guards the look-through exposure math and concentration limits."""

    def test_look_through_distributes_weight_to_components(self):
        eng = ExposureEngine()
        true_exp = eng.calculate_look_through({"QQQ": 1.0})
        # QQQ holds NVDA/MSFT/AAPL at 8%/9%/9% per the hardcoded matrix
        assert math.isclose(true_exp["NVDA"], 0.08, abs_tol=1e-6)
        assert math.isclose(true_exp["MSFT"], 0.09, abs_tol=1e-6)
        assert math.isclose(true_exp["AAPL"], 0.09, abs_tol=1e-6)

    def test_look_through_passthrough_for_non_composite(self):
        eng = ExposureEngine()
        true_exp = eng.calculate_look_through({"GOLD": 0.5})
        assert math.isclose(true_exp["GOLD"], 0.5, abs_tol=1e-6)

    def test_check_concentration_flags_excess(self):
        eng = ExposureEngine()
        violations = eng.check_concentration(
            {"NVDA": 0.30, "CASH": 0.70}, limit=0.15
        )
        assert len(violations) == 1
        assert violations[0]["entity"] == "NVDA"
        assert math.isclose(violations[0]["actual"], 0.30)
        assert math.isclose(violations[0]["limit"], 0.15)

    def test_check_concentration_ignores_cash_gold_theme_def(self):
        eng = ExposureEngine()
        # Safe-haven / defensive buckets must never trip concentration
        assert eng.check_concentration(
            {"CASH": 0.99, "GLD": 0.99, "THEME_DEF": 0.99}, limit=0.15
        ) == []

    def test_check_concentration_no_violation_below_limit(self):
        eng = ExposureEngine()
        assert eng.check_concentration(
            {"NVDA": 0.10, "MSFT": 0.10}, limit=0.15
        ) == []


class TestBudgetTree:
    """Guards the L2/L3/L4 budget-sum invariants of AllocationProposal.

    Real API: pydantic models, not a mutable BudgetTree. The validator
    raises ValueError("L{n} budget sum ... != 1.0") when a level's ratios
    do not sum to ~1.0.
    """

    def test_rejects_l2_sum_not_one(self):
        with pytest.raises(ValueError, match="L2 budget sum"):
            AllocationProposal(
                risk_budget=0.5,
                asset_classes=AssetClassBudget(
                    equity_budget=0.5, fixed_income_budget=0.3,
                    commodity_budget=0.1, cash_budget=0.2,  # 1.1 != 1.0
                ),
            )

    def test_rejects_l3_sum_not_one(self):
        with pytest.raises(ValueError, match="L3 budget sum"):
            AllocationProposal(
                risk_budget=0.5,
                asset_classes=AssetClassBudget(
                    equity_budget=0.5, fixed_income_budget=0.25,
                    commodity_budget=0.15, cash_budget=0.10,  # L2 = 1.0
                ),
                sectors=SectorBudget(
                    tech_budget=0.6, defensive_budget=0.6  # 1.2 != 1.0
                ),
            )

    def test_rejects_l4_sum_not_one(self):
        with pytest.raises(ValueError, match="L4 budget sum"):
            AllocationProposal(
                risk_budget=0.5,
                asset_classes=AssetClassBudget(
                    equity_budget=0.5, fixed_income_budget=0.25,
                    commodity_budget=0.15, cash_budget=0.10,  # L2 = 1.0
                ),
                sectors=SectorBudget(
                    tech_budget=0.6, defensive_budget=0.4,  # L3 = 1.0
                    tech_subsectors=TechSubSectorBudget(
                        TECH_HW=0.5, TECH_HYPER=0.5, TECH_AI_APP=0.5  # 1.5 != 1.0
                    ),
                ),
            )

    def test_valid_proposal_flattens_to_absolute_weights(self):
        prop = AllocationProposal(
            risk_budget=0.5,
            asset_classes=AssetClassBudget(
                equity_budget=0.5, fixed_income_budget=0.25,
                commodity_budget=0.15, cash_budget=0.10,  # L2 = 1.0
            ),
            sectors=SectorBudget(
                tech_budget=0.6, defensive_budget=0.4,  # L3 = 1.0
                tech_subsectors=TechSubSectorBudget(
                    TECH_HW=0.5, TECH_HYPER=0.3, TECH_AI_APP=0.2  # L4 = 1.0
                ),
            ),
        )
        w = prop.flatten_to_absolute_weights()
        assert math.isclose(w["CASH"], 0.10, abs_tol=1e-6)
        # THEME_DEF = equity * defensive = 0.5 * 0.4
        assert math.isclose(w["THEME_DEF"], 0.20, abs_tol=1e-6)
        # TECH_HW = equity * tech * 0.5 = 0.5 * 0.6 * 0.5
        assert math.isclose(w["TECH_HW"], 0.15, abs_tol=1e-6)


class TestHMMInference:
    """Guards safe degradation + deterministic inference of the regime model.

    Real API: class is ``HMMModel`` (not ``HMMInference``); inference is
    ``predict_proba(features: dict) -> HMMInferenceResult``. With no model
    file it MUST fall back to a uniform distribution (never crash, never
    retain a stale Bull state).
    """

    def test_fallback_uniform_when_no_model(self):
        model = HMMModel()  # vault/HMM_PARAMS.json is absent in CI
        assert model.is_loaded is False
        result = model.predict_proba({"some_feature": 1.0})
        assert result.predicted_regime == RegimeType.TRANSITION.value
        assert math.isclose(result.confidence, 0.25, abs_tol=1e-6)
        assert math.isclose(sum(result.probs.values()), 1.0, abs_tol=1e-6)
        # Fallback covers exactly the 4 regimes in DEFAULT_PROBS
        # (RegimeType has more members; the unmodeled ones are absent here)
        assert set(result.probs.keys()) == set(DEFAULT_PROBS.keys())

    def test_anomalous_zero_input_never_crashes(self):
        # Spec wanted "safe degradation on extreme input"; real API: extreme
        # features still yield a valid normalized distribution.
        model = HMMModel()
        result = model.predict_proba({f"f{i}": 0.0 for i in range(5)})
        assert sum(result.probs.values()) == pytest.approx(1.0)
        assert result.predicted_regime in {r.value for r in RegimeType}

    def test_loaded_model_infers_deterministically(self, tmp_path):
        # Cover ALL regime states (RegimeType has 11 members); only RISK_ON
        # emits strongly on positive momentum so it must dominate.
        emission_params = {
            r.value: {
                "momentum": {
                    "mu": 1.0 if r == RegimeType.RISK_ON else 0.0,
                    "sigma": 0.1,
                }
            }
            for r in RegimeType
        }
        params = {
            "transition_matrix": {
                RegimeType.TRANSITION.value: {r.value: 0.25 for r in RegimeType}
            },
            "emission_params": emission_params,
        }
        p = tmp_path / "HMM_PARAMS.json"
        p.write_text(json.dumps(params))
        model = HMMModel(params_path=p)
        assert model.is_loaded is True
        # Strong positive momentum must push the regime toward RISK_ON
        res = model.predict_proba({"momentum": 1.0})
        assert res.predicted_regime == RegimeType.RISK_ON.value
        assert res.confidence > 0.25
