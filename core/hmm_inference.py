"""Macro OS v4.1 — HMM live inference ONLY.

STRICT RULE: This module contains zero training code.
- Load: vault/HMM_PARAMS.json
- Predict: predict_proba(features) -> Dict[str, float]
- Fallback: uniform probs if model missing
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.schemas import HMMInferenceResult, RegimeType

logger = logging.getLogger(__name__)

DEFAULT_PROBS = {
    RegimeType.RISK_ON.value: 0.25,
    RegimeType.TIGHT_LIQUIDITY.value: 0.25,
    RegimeType.LIQUIDITY_SQUEEZE.value: 0.25,
    RegimeType.TRANSITION.value: 0.25,
}


class HMMModel:
    """Hidden Markov Model for regime inference.

    Loaded from JSON param file. Inference only — no training.
    Falls back to uniform probabilities if model file is missing
    or features cannot be processed.
    """

    def __init__(self, params_path: Optional[Path] = None) -> None:
        self.params_path = params_path
        self._loaded: bool = False
        self._params: Dict[str, Any] = {}
        self._states: List[str] = [r.value for r in RegimeType]

        if params_path and params_path.exists():
            self._load(params_path)

    def _load(self, path: Path) -> None:
        """Load model parameters from JSON."""
        try:
            with open(path, "r") as f:
                self._params = json.load(f)
            self._loaded = True
            logger.info("HMM model loaded from %s", path)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Failed to load HMM model: %s", e)
            self._loaded = False

    def predict_proba(self, features: Dict[str, Any]) -> HMMInferenceResult:
        """Predict regime probabilities from features.

        Uses loaded model if available, otherwise falls back to
        uniform distribution (no assumptions).

        Args:
            features: Dict of macro feature values.

        Returns:
            HMMInferenceResult with probabilities per regime.
        """
        if not self._loaded or not self._params:
            return self._fallback("Model not loaded")

        try:
            return self._infer(features)
        except Exception as e:
            logger.warning("HMM inference failed: %s", e)
            return self._fallback(str(e))

    def _infer(self, features: Dict[str, Any]) -> HMMInferenceResult:
        """Run inference using loaded model parameters.

        Simplified emission-based inference:
        Uses transition matrix + emission probs to compute
        posterior regime probabilities.
        """
        transition = self._params.get("transition_matrix", {})
        emissions = self._params.get("emission_params", {})

        # Compute emission scores from features
        raw_probs: Dict[str, float] = {}
        for state in self._states:
            state_emission = emissions.get(state, {})
            score = 1.0
            for feat_key, feat_val in features.items():
                if feat_key.startswith("_"):
                    continue
                if isinstance(feat_val, (int, float)):
                    mu = state_emission.get(feat_key, {}).get("mu", 0.0)
                    sigma = state_emission.get(feat_key, {}).get("sigma", 1.0)
                    if sigma > 0:
                        score *= math.exp(-0.5 * ((feat_val - mu) / sigma) ** 2)
            raw_probs[state] = score

        # Normalize
        total = sum(raw_probs.values())
        if total > 0:
            probs = {k: v / total for k, v in raw_probs.items()}
        else:
            probs = dict(DEFAULT_PROBS)

        # Weight by transition from previous state (if available)
        prev_state = features.get("_prev_regime")
        if prev_state and prev_state in transition:
            trans_probs = transition[prev_state]
            for state in probs:
                trans_w = trans_probs.get(state, 0.25)
                probs[state] = probs.get(state, 0.25) * 0.7 + trans_w * 0.3

            total = sum(probs.values())
            if total > 0:
                probs = {k: v / total for k, v in probs.items()}

        predicted = max(probs, key=probs.get)
        confidence = probs[predicted]

        return HMMInferenceResult(
            probs=probs,
            predicted_regime=predicted,
            confidence=round(confidence, 4),
        )

    def _fallback(self, reason: str = "") -> HMMInferenceResult:
        """Return uniform probabilities when model unavailable."""
        return HMMInferenceResult(
            probs=dict(DEFAULT_PROBS),
            predicted_regime=RegimeType.TRANSITION.value,
            confidence=0.25,
        )

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def params(self) -> Dict[str, Any]:
        return dict(self._params)


import math
