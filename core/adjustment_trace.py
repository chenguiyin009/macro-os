"""Macro OS v4.5 - Decision trace + Decay Score (read-only)."""

from __future__ import annotations
import datetime, hashlib, json
from dataclasses import dataclass, field
from typing import Any, Dict

SCHEMA_VERSION = "1.0.0"
DECAY_WEIGHTS = {"volume_slope": 0.4, "micro_fracture": 0.3, "credit_quality": 0.2, "rate_velocity": 0.1}


@dataclass
class AdjustmentTrace:
    schema_version: str = SCHEMA_VERSION
    trace_hash: str = ""
    timestamp: str = ""
    macro_quadrant: str = ""
    macro_confirmation: str = ""
    divergence_score: float = 0.0
    divergence_phase: str = ""
    fracture_type: str = ""
    credit_confidence: float = 1.0
    base_budget: float = 1.0
    vol_adjustment: float = 0.0
    recovery_adjustment: float = 0.0
    volume_divergence_adjustment: float = 0.0
    proxy_penalty: float = 0.0
    final_budget: float = 1.0
    delta_breakdown: Dict[str, float] = field(default_factory=dict)
    decay_volume_slope: float = 0.0
    decay_micro_fracture: float = 0.0
    decay_credit_quality: float = 0.0
    decay_rate_velocity: float = 0.0
    decay_total: float = 0.0
    kernel_authority: str = ""
    kernel_action: str = ""


def compute_trace_hash(t: AdjustmentTrace) -> str:
    data = {k: v for k, v in vars(t).items() if k != "trace_hash"}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def compute_decay_score(macro_state, features: Dict[str, Any], divergence_score: float) -> Dict[str, float]:
    volume = features.get("volume", 0)
    vol_ratio = volume / features.get("volume_20d_avg", 1) if features.get("volume_20d_avg", 1) > 0 else 1.0
    vs = max(0.0, min(1.0, (1.0 - vol_ratio) / 0.15))
    mf = max(0.0, min(1.0, divergence_score * 1.2))
    ct = getattr(macro_state, "credit_trend", 0)
    cq = max(0.0, min(1.0, (1.0 - abs(ct)) if ct < 0 else 0.0))
    tt = getattr(macro_state, "tips_trend", 0)
    rv = max(0.0, min(1.0, abs(tt) * 0.5))
    total = sum(DECAY_WEIGHTS[k] * v for k, v in [("volume_slope", vs), ("micro_fracture", mf), ("credit_quality", cq), ("rate_velocity", rv)])
    return {"volume_slope": round(vs, 4), "micro_fracture": round(mf, 4), "credit_quality": round(cq, 4), "rate_velocity": round(rv, 4), "total": round(total, 4)}
