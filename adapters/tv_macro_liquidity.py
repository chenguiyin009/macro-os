"""Map TradingView macro-liquidity monitor cards to FeatureSchema.

Honest mapping:
- score/state -> danger_score / risk_score
- driver labels VIX/DXY/Gold/etc -> fields when numeric
- does NOT invent tips_yield / nominal UST levels (those stay FRED/yfinance)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.schemas import DataSource, FeatureSchema

logger = logging.getLogger(__name__)

DEFAULT_MACRO_LATEST_PATH = (
    Path(__file__).resolve().parents[2] / "relay" / "logs" / "macro-liquidity.latest.json"
)
DEFAULT_FEATURES_LATEST_PATH = (
    Path(__file__).resolve().parents[2] / "relay" / "logs" / "macro-os-features.latest.json"
)

_STATE_DANGER = {
    "进攻": 20.0,
    "观察": 40.0,
    "防御": 65.0,
    "危机": 85.0,
}

_DRIVER_FIELD = {
    "VIX": "vix",
    "DXY": "dxy",
    "Gold": "gold",
    "GOLD": "gold",
    "HY Stress": "hy_stress_score",
    "Real Yield": "real_yield_driver",
    "BEI": "bei_10y",
    "2Y": "nominal_2y",
    "MOVE": "move",
}


def _parse_driver_entry(entry: str) -> Optional[tuple[str, float]]:
    text = str(entry or "").strip()
    if not text or ":" not in text:
        return None
    label, raw = text.split(":", 1)
    label = label.strip()
    raw = raw.strip().replace("%", "")
    # keep first number
    m = re.search(r"[-+]?\d*\.?\d+", raw)
    if not m:
        return None
    try:
        return label, float(m.group(0))
    except ValueError:
        return None


def extract_driver_values(modules: List[Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for module in modules or []:
        for entry in module.get("drivers") or []:
            parsed = _parse_driver_entry(entry)
            if not parsed:
                continue
            label, value = parsed
            field = _DRIVER_FIELD.get(label)
            if field:
                out[field] = value
    return out


def feature_schema_from_macro_card(
    card_or_result: Dict[str, Any],
    *,
    source: DataSource = DataSource.MCP,
) -> FeatureSchema:
    """Accept either full monitor result {macro: {...}} or card-like dict."""
    macro = card_or_result.get("macro") if isinstance(card_or_result.get("macro"), dict) else card_or_result
    if not isinstance(macro, dict):
        raise ValueError("invalid macro card payload")

    modules = macro.get("modules") or []
    if not isinstance(modules, list):
        modules = []
    drivers = extract_driver_values(modules)

    score = macro.get("score")
    try:
        score_f = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_f = None

    state = str(macro.get("state") or "")
    danger = score_f if score_f is not None else _STATE_DANGER.get(state, 40.0)
    # blend state floor
    danger = max(danger, _STATE_DANGER.get(state, 0.0))
    danger = max(0.0, min(100.0, float(danger)))

    symbol = str(macro.get("symbol") or card_or_result.get("symbol") or "")
    kwargs: Dict[str, Any] = {
        "source": source,
        "fetched_at": datetime.now(timezone.utc),
        "danger_score": danger,
        "risk_score": max(0.0, min(1.0, danger / 100.0)),
        "fragility_score": danger / 10.0,
    }
    if drivers.get("vix") is not None:
        kwargs["vix"] = drivers["vix"]
    if drivers.get("dxy") is not None:
        kwargs["dxy"] = drivers["dxy"]
    if drivers.get("gold") is not None:
        # may be stress score not price; only accept plausible gold price
        g = drivers["gold"]
        if g > 50:  # ETF/price-like
            kwargs["gold"] = g
    if drivers.get("bei_10y") is not None and 0 < drivers["bei_10y"] < 10:
        kwargs["bei_10y"] = drivers["bei_10y"]
    if drivers.get("nominal_2y") is not None and 0 < drivers["nominal_2y"] < 20:
        kwargs["nominal_2y"] = drivers["nominal_2y"]
    # HY stress score is not OAS bp; skip unless looks like spread
    hy = drivers.get("hy_stress_score")
    if hy is not None and hy >= 50:  # likely bp-ish or stress index high
        # treat as soft widening proxy only if large
        kwargs["hy_credit_spread"] = float(hy) if hy > 20 else 320.0 + float(hy)

    if "QQQ" in symbol.upper():
        # price unknown from card; leave qqq_close absent
        pass

    # encode liquidity state for downstream narrative (non-constitutional)
    kwargs["equity_tech_rotation"] = None
    fs = FeatureSchema(**{k: v for k, v in kwargs.items() if v is not None})
    # attach narrative metadata outside schema via dict on instance
    fs.__dict__["_tv_macro_meta"] = {
        "state": state,
        "score": score_f,
        "headline": (macro.get("summary") or {}).get("headline") if isinstance(macro.get("summary"), dict) else macro.get("signal"),
        "modules": [
            {"name": m.get("name"), "state": m.get("state"), "score": m.get("score")}
            for m in modules
            if isinstance(m, dict)
        ],
    }
    return fs


def load_macro_liquidity_features(
    *,
    features_path: Path | None = None,
    latest_path: Path | None = None,
    max_age_seconds: int = 300,
) -> Optional[FeatureSchema]:
    """Load latest macro-liquidity sidecar written by tv-desktop-monitor."""
    import time

    # Prefer flattened features file
    fpath = features_path or DEFAULT_FEATURES_LATEST_PATH
    lpath = latest_path or DEFAULT_MACRO_LATEST_PATH

    for path, kind in ((fpath, "features"), (lpath, "card")):
        if not path.exists():
            continue
        age = time.time() - path.stat().st_mtime
        if age > max_age_seconds:
            logger.info("macro liquidity sidecar stale (%.0fs): %s", age, path)
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            logger.warning("failed reading %s: %s", path, exc)
            continue
        try:
            if kind == "features" and isinstance(raw, dict) and (
                "danger_score" in raw or "vix" in raw or "dxy" in raw
            ):
                # flattened FeatureSchema-like
                return FeatureSchema.model_validate(raw)
            return feature_schema_from_macro_card(raw)
        except Exception as exc:
            logger.warning("macro liquidity map failed for %s: %s", path, exc)
            continue
    return None
