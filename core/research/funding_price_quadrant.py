"""Research-layer funding-price quadrants (real rate x nominal rate).

Pure functions only. Does NOT call decision_kernel.decide().
Maps the weekly research language (Q1-Q4) onto optional hard_regime hints
for orchestrator payload / CIO narrative alignment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class FundingPriceQuadrant(str, Enum):
    """Real-rate direction x nominal-rate direction (research SSOT)."""

    Q1_STRESS_TEST = "Q1_STRESS_TEST"  # nominal up, real up
    Q2_DEBT_DEFLATION = "Q2_DEBT_DEFLATION"  # nominal down, real up
    Q3_POLICY_EASE = "Q3_POLICY_EASE"  # nominal down, real down
    Q4_REFLATION = "Q4_REFLATION"  # nominal up, real down
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:
        return self.value


_LABEL_ZH = {
    FundingPriceQuadrant.Q1_STRESS_TEST: "压力测试",
    FundingPriceQuadrant.Q2_DEBT_DEFLATION: "债务通缩风险",
    FundingPriceQuadrant.Q3_POLICY_EASE: "政策宽松/分母释放",
    FundingPriceQuadrant.Q4_REFLATION: "再通胀",
    FundingPriceQuadrant.UNKNOWN: "未知",
}

# Maps research quadrant -> constitutional hard_regime *hint* (not a red line).
_QUADRANT_TO_HARD_REGIME_HINT = {
    FundingPriceQuadrant.Q1_STRESS_TEST: "TIGHT_LIQUIDITY",
    FundingPriceQuadrant.Q2_DEBT_DEFLATION: "TIGHT_LIQUIDITY",
    FundingPriceQuadrant.Q3_POLICY_EASE: "RISK_ON",
    FundingPriceQuadrant.Q4_REFLATION: "TRANSITION",
    FundingPriceQuadrant.UNKNOWN: "TRANSITION",
}


@dataclass(frozen=True)
class FundingPriceAssessment:
    """Immutable research assessment for one feature snapshot / week."""

    quadrant: FundingPriceQuadrant
    label_zh: str
    real_rate_direction: str  # up | down | flat | unknown
    nominal_rate_direction: str
    hard_regime_hint: str
    confidence: float
    dominant_drivers: List[str] = field(default_factory=list)
    transmission_layer: str = "unknown"
    credit_stable: Optional[bool] = None
    usd_breakout: Optional[bool] = None
    notes: str = ""
    inputs_used: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "quadrant": self.quadrant.value,
            "label_zh": self.label_zh,
            "real_rate_direction": self.real_rate_direction,
            "nominal_rate_direction": self.nominal_rate_direction,
            "hard_regime_hint": self.hard_regime_hint,
            "confidence": self.confidence,
            "dominant_drivers": list(self.dominant_drivers),
            "transmission_layer": self.transmission_layer,
            "credit_stable": self.credit_stable,
            "usd_breakout": self.usd_breakout,
            "notes": self.notes,
            "inputs_used": dict(self.inputs_used),
        }


def _direction_from_delta(delta: Optional[float], eps: float = 0.5) -> str:
    """Direction from bp-like delta. eps default 0.5bp."""
    if delta is None:
        return "unknown"
    try:
        d = float(delta)
    except (TypeError, ValueError):
        return "unknown"
    if d > eps:
        return "up"
    if d < -eps:
        return "down"
    return "flat"


def _first_present(features: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for k in keys:
        if k in features and features[k] is not None:
            try:
                return float(features[k])
            except (TypeError, ValueError):
                continue
    return None


def _infer_real_direction(features: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    used: Dict[str, Any] = {}
    # Prefer explicit short-window changes (bp)
    delta = _first_present(
        features,
        [
            "tips_yield_change_5d_bp",
            "tips_10y_change_5d_bp",
            "real_rate_change_5d_bp",
            "tips_yield_change_1d_bp",
        ],
    )
    if delta is not None:
        used["real_delta_bp"] = delta
        return _direction_from_delta(delta), used

    # ROC style (fractional / percent points over window)
    roc = _first_present(features, ["tips_yield_roc_60d", "tips_yield_roc"])
    if roc is not None:
        used["tips_yield_roc"] = roc
        # roc in percent points: >0 means real up
        return _direction_from_delta(roc * 100.0, eps=2.0), used

    level = _first_present(features, ["tips_yield", "tips_10y", "real_rate_10y"])
    if level is not None:
        used["tips_yield_level"] = level
        # Level-only fallback vs mild anchor 1.0% (post-2022 world). High alone != uptrend,
        # but level >> anchor with no delta still tags "up" pressure for research mock alignment.
        if level >= 1.8:
            return "up", used
        if level <= 0.5:
            return "down", used
        return "flat", used
    return "unknown", used


def _infer_nominal_direction(features: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    used: Dict[str, Any] = {}
    delta = _first_present(
        features,
        [
            "nominal_30y_change_5d_bp",
            "nominal_10y_change_5d_bp",
            "ust_30y_change_5d_bp",
            "ust_10y_change_5d_bp",
            "nominal_30y_change_1d_bp",
            "nominal_10y_change_1d_bp",
        ],
    )
    if delta is not None:
        used["nominal_delta_bp"] = delta
        return _direction_from_delta(delta), used

    # Prefer 30y then 10y level vs post-2022 mild anchors
    n30 = _first_present(features, ["nominal_30y", "ust_30y"])
    n10 = _first_present(features, ["nominal_10y", "ust_10y"])
    if n30 is not None:
        used["nominal_30y"] = n30
        if n30 >= 4.8:
            return "up", used
        if n30 <= 3.5:
            return "down", used
    if n10 is not None:
        used["nominal_10y"] = n10
        if n10 >= 4.3:
            return "up", used
        if n10 <= 3.2:
            return "down", used
    return "unknown", used


def _credit_stable(features: Dict[str, Any]) -> Optional[bool]:
    if "credit_stable" in features:
        return bool(features.get("credit_stable"))
    hy = _first_present(features, ["hy_credit_spread", "hy_spread_bp"])
    if hy is None:
        return None
    # bp scale: below squeeze gate and not exploding
    return hy < 400.0


def _usd_breakout(features: Dict[str, Any]) -> Optional[bool]:
    if "usd_breakout" in features:
        return bool(features.get("usd_breakout"))
    dxy_chg = _first_present(features, ["dxy_change_5d_pct", "dxy_change_5d"])
    if dxy_chg is not None:
        return dxy_chg >= 1.0  # rough: strong multi-day breakout impulse
    dxy = _first_present(features, ["dxy"])
    if dxy is None:
        return None
    # Level alone does not equal breakout
    return False


def classify_funding_price_quadrant(features: Dict[str, Any]) -> FundingPriceAssessment:
    """Classify funding-price research quadrant from a feature dict.

    Pure: no I/O. Missing inputs degrade to UNKNOWN with low confidence.
    """
    real_dir, real_used = _infer_real_direction(features)
    nom_dir, nom_used = _infer_nominal_direction(features)
    inputs = {**real_used, **nom_used}

    if real_dir in ("unknown", "flat") or nom_dir in ("unknown", "flat"):
        # Allow flat+up combinations only if the other axis is decisive up/down
        if real_dir == "flat" and nom_dir in ("up", "down"):
            pass
        elif nom_dir == "flat" and real_dir in ("up", "down"):
            pass
        elif real_dir == "unknown" or nom_dir == "unknown":
            return FundingPriceAssessment(
                quadrant=FundingPriceQuadrant.UNKNOWN,
                label_zh=_LABEL_ZH[FundingPriceQuadrant.UNKNOWN],
                real_rate_direction=real_dir,
                nominal_rate_direction=nom_dir,
                hard_regime_hint="TRANSITION",
                confidence=0.2,
                notes="insufficient real/nominal direction inputs",
                inputs_used=inputs,
            )

    # Treat flat real with nominal up as stress-test adjacent (duration supply)
    r = real_dir
    n = nom_dir
    if r == "flat":
        r = "up" if n == "up" else ("down" if n == "down" else "flat")
    if n == "flat":
        n = "up" if r == "up" else ("down" if r == "down" else "flat")

    if r == "up" and n == "up":
        q = FundingPriceQuadrant.Q1_STRESS_TEST
    elif r == "up" and n == "down":
        q = FundingPriceQuadrant.Q2_DEBT_DEFLATION
    elif r == "down" and n == "down":
        q = FundingPriceQuadrant.Q3_POLICY_EASE
    elif r == "down" and n == "up":
        q = FundingPriceQuadrant.Q4_REFLATION
    else:
        q = FundingPriceQuadrant.UNKNOWN

    credit_ok = _credit_stable(features)
    usd_bo = _usd_breakout(features)
    conf = 0.55
    if "real_delta_bp" in inputs or "nominal_delta_bp" in inputs:
        conf += 0.25
    if credit_ok is not None:
        conf += 0.1
    conf = min(conf, 0.95)
    if q is FundingPriceQuadrant.UNKNOWN:
        conf = min(conf, 0.35)

    drivers: List[str] = []
    if _first_present(features, ["tips_yield", "tips_10y"]) is not None:
        drivers.append("tips_10y_real")
    if _first_present(features, ["nominal_30y", "ust_30y"]) is not None:
        drivers.append("ust_30y_nominal")
    elif _first_present(features, ["nominal_10y", "ust_10y"]) is not None:
        drivers.append("ust_10y_nominal")

    transmission = "duration_valuation"
    notes = ""
    if q is FundingPriceQuadrant.Q1_STRESS_TEST:
        if credit_ok is True and usd_bo is not True:
            notes = "Q1 stress test: duration re-pricing; credit/usd not confirming systemic squeeze"
            transmission = "duration_valuation"
        elif credit_ok is False:
            notes = "Q1 with credit stress — watch squeeze path separately"
            transmission = "duration_toward_credit"
        else:
            notes = "Q1 stress test (credit/usd confirmation incomplete)"
    elif q is FundingPriceQuadrant.Q2_DEBT_DEFLATION:
        notes = "real up + nominal down: debt-deflation risk branch"
        transmission = "growth_real_burden"
    elif q is FundingPriceQuadrant.Q3_POLICY_EASE:
        notes = "real down + nominal down: denominator relief / ease branch"
        transmission = "denominator_relief"
    elif q is FundingPriceQuadrant.Q4_REFLATION:
        notes = "real down + nominal up: reflation branch"

    hint = _QUADRANT_TO_HARD_REGIME_HINT[q]
    # Explicit non-mapping: never promote Q1 alone to LIQUIDITY_SQUEEZE
    if hint == "LIQUIDITY_SQUEEZE":
        hint = "TIGHT_LIQUIDITY"

    return FundingPriceAssessment(
        quadrant=q,
        label_zh=_LABEL_ZH[q],
        real_rate_direction=real_dir,
        nominal_rate_direction=nom_dir,
        hard_regime_hint=hint,
        confidence=round(conf, 4),
        dominant_drivers=drivers,
        transmission_layer=transmission,
        credit_stable=credit_ok,
        usd_breakout=usd_bo,
        notes=notes,
        inputs_used=inputs,
    )


def assessment_from_week_snapshot(snapshot: Dict[str, Any]) -> FundingPriceAssessment:
    """Build assessment from archived weekly JSON (data/research/*.json)."""
    levels = snapshot.get("levels") or {}
    chg = snapshot.get("changes_5d_bp_or_pct") or {}
    features: Dict[str, Any] = {
        "tips_yield": levels.get("tips_10y"),
        "nominal_10y": levels.get("nominal_10y"),
        "nominal_30y": levels.get("nominal_30y"),
        "nominal_2y": levels.get("nominal_2y"),
        "bei_10y": levels.get("bei_10y"),
        "dxy": levels.get("dxy"),
        "gold": levels.get("gold"),
        "tips_yield_change_5d_bp": chg.get("tips_10y_bp"),
        "nominal_10y_change_5d_bp": chg.get("nominal_10y_bp"),
        "nominal_30y_change_5d_bp": chg.get("nominal_30y_bp"),
        "nominal_2y_change_5d_bp": chg.get("nominal_2y_bp"),
        "credit_stable": (snapshot.get("credit") or {}).get("stable"),
        "usd_breakout": (snapshot.get("usd") or {}).get("breakout"),
    }
    # hy proxy in weekly file may be ratio-like; only pass if clearly bp
    hy = levels.get("hy_credit_spread_bp")
    if hy is not None:
        features["hy_credit_spread"] = hy
    elif snapshot.get("credit", {}).get("stable") is True:
        features["hy_credit_spread"] = 320.0  # stable mid proxy in bp
    base = classify_funding_price_quadrant(features)
    # Prefer explicit snapshot quadrant if present and compatible
    explicit = snapshot.get("funding_price_quadrant")
    if explicit:
        try:
            q = FundingPriceQuadrant(explicit)
            return FundingPriceAssessment(
                quadrant=q,
                label_zh=_LABEL_ZH.get(q, base.label_zh),
                real_rate_direction=base.real_rate_direction,
                nominal_rate_direction=base.nominal_rate_direction,
                hard_regime_hint=_QUADRANT_TO_HARD_REGIME_HINT.get(q, base.hard_regime_hint),
                confidence=max(base.confidence, 0.9),
                dominant_drivers=list(snapshot.get("drivers", {}).get("dominant") or base.dominant_drivers),
                transmission_layer=str(snapshot.get("transmission_layer") or base.transmission_layer),
                credit_stable=(snapshot.get("credit") or {}).get("stable"),
                usd_breakout=(snapshot.get("usd") or {}).get("breakout"),
                notes=str(snapshot.get("core_conclusion") or base.notes),
                inputs_used=base.inputs_used,
            )
        except ValueError:
            pass
    return base
