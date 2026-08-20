"""Rates-stress cap — independent fifth leg for daily_macro combined budget.

Purpose
-------
Fill the gap where long-end yields are extreme (high rolling percentile and/or
sustained z-score stress) but the denominator state machine has not yet entered
「久期压力」, so tech/growth duration exposure is still only constrained by the
lagged SOXX drawdown dampener.

Design (deliberate)
-------------------
* Signal source is FRED-style yield *levels* (nominal_30y / tips_yield), NOT
  NQ-driver theme scores and NOT TLT price z as the primary input.
* Continuous N-day confirmation before engage; slower exit to avoid single-day
  spike whipsaw.
* Orthogonal to frozen TECH_DRAWDOWN_CAPS (-13/-10/-7 → 0.35/0.50/0.65).
* Does not restore gold denominator hard votes.
* Caps are soft observation ceilings for consolidator min(); not kernel edits.

Default thresholds are starting points for backtest calibration, not frozen
constitution constants.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


@dataclass
class RatesStressParams:
    """Tunable parameters (research / consolidator config; not kernel-frozen)."""

    w_trade: int = 5
    w_vol: int = 60
    w_lvl: int = 756  # ~3y trading days — same window family as denominator_state
    z_enter: float = 1.0
    pct_enter: float = 97.0
    confirm_days: int = 2  # engage only after N consecutive trigger days
    exit_days: int = 3  # stay engaged until N consecutive clear days
    # Cap ladder: tighter when both slope + level extreme
    cap_level_only: float = 0.65  # high percentile alone (static pressure)
    cap_slope_level: float = 0.55  # z + percentile both hot
    cap_extreme: float = 0.45  # very high z sustained with extreme level
    z_extreme: float = 1.75
    # Optional AND confirm with TLT (price down => yields up). 0 disables.
    tlt_z_confirm: float = 0.0
    # When denominator already in hard stress, rates leg may still publish but
    # consolidator will usually not bind (other legs tighter). No special kill.


DEFAULT_PARAMS = RatesStressParams()


def _rate_z5_pct(
    level: pd.Series,
    *,
    w_trade: int,
    w_vol: int,
    w_lvl: int,
) -> Tuple[pd.Series, pd.Series]:
    x = level.astype(float)
    d1 = (x - x.shift(1)) * 100.0
    d5 = (x - x.shift(w_trade)) * 100.0
    vol = d1.rolling(w_vol).std()
    z5 = d5 / (vol * np.sqrt(w_trade))
    pct = x.rolling(w_lvl).apply(lambda a: float((a < a[-1]).mean() * 100.0), raw=True)
    return z5, pct


def _tlt_z5(tlt_px: pd.Series, *, w_trade: int = 5, w_vol: int = 60) -> pd.Series:
    x = tlt_px.astype(float)
    t1 = x.pct_change() * 100.0
    r5 = (x / x.shift(w_trade) - 1.0) * 100.0
    vol = t1.rolling(w_vol).std()
    return r5 / (vol * np.sqrt(w_trade))


def compute_rates_features(
    frame: pd.DataFrame,
    params: Optional[RatesStressParams] = None,
) -> pd.DataFrame:
    """Add n30/tips z5 + pct columns. Requires nominal_30y; tips_yield optional."""
    p = params or DEFAULT_PARAMS
    out = frame.copy()
    if "nominal_30y" not in out.columns:
        raise ValueError("frame must contain nominal_30y")
    n_z, n_p = _rate_z5_pct(
        out["nominal_30y"], w_trade=p.w_trade, w_vol=p.w_vol, w_lvl=p.w_lvl
    )
    out["nominal_30y_z5"] = n_z
    out["nominal_30y_pct"] = n_p
    if "tips_yield" in out.columns:
        t_z, t_p = _rate_z5_pct(
            out["tips_yield"], w_trade=p.w_trade, w_vol=p.w_vol, w_lvl=p.w_lvl
        )
        out["tips_yield_z5"] = t_z
        out["tips_yield_pct"] = t_p
    else:
        out["tips_yield_z5"] = np.nan
        out["tips_yield_pct"] = np.nan
    if "tlt" in out.columns:
        out["tlt_z5"] = _tlt_z5(out["tlt"], w_trade=p.w_trade, w_vol=p.w_vol)
    else:
        out["tlt_z5"] = np.nan
    return out


def _raw_trigger_row(r: Mapping[str, Any], p: RatesStressParams) -> Tuple[bool, str, float]:
    """Return (triggered, reason, suggested_cap_if_engaged)."""
    n_z = r.get("nominal_30y_z5")
    n_p = r.get("nominal_30y_pct")
    t_z = r.get("tips_yield_z5")
    t_p = r.get("tips_yield_pct")

    def _f(v: Any) -> float:
        try:
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return float("nan")
            return float(v)
        except (TypeError, ValueError):
            return float("nan")

    nz, npct = _f(n_z), _f(n_p)
    tz, tpct = _f(t_z), _f(t_p)

    level_hot = (not np.isnan(npct) and npct >= p.pct_enter) or (
        not np.isnan(tpct) and tpct >= p.pct_enter
    )
    slope_hot = (not np.isnan(nz) and nz >= p.z_enter) or (
        not np.isnan(tz) and tz >= p.z_enter
    )
    slope_extreme = (not np.isnan(nz) and nz >= p.z_extreme) or (
        not np.isnan(tz) and tz >= p.z_extreme
    )

    # Optional TLT confirm: TLT z5 <= -tlt_z_confirm means bond price stress (yields up)
    if p.tlt_z_confirm and p.tlt_z_confirm > 0:
        tltz = _f(r.get("tlt_z5"))
        if np.isnan(tltz) or tltz > -p.tlt_z_confirm:
            return False, "tlt_not_confirm", 1.0

    if level_hot and slope_extreme:
        return True, "level+slope_extreme", float(p.cap_extreme)
    if level_hot and slope_hot:
        return True, "level+slope", float(p.cap_slope_level)
    if level_hot:
        return True, "level_only", float(p.cap_level_only)
    return False, "clear", 1.0


def apply_hysteresis(
    raw_trigger: Sequence[bool],
    raw_cap: Sequence[float],
    *,
    confirm_days: int,
    exit_days: int,
) -> Tuple[List[bool], List[float]]:
    """Engage after confirm_days True; stay until exit_days consecutive False."""
    engaged = False
    streak_on = 0
    streak_off = 0
    out_eng: List[bool] = []
    out_cap: List[float] = []
    last_cap = 1.0
    for trig, cap in zip(raw_trigger, raw_cap):
        if trig:
            streak_on += 1
            streak_off = 0
            last_cap = float(cap)
            if not engaged and streak_on >= confirm_days:
                engaged = True
        else:
            streak_off += 1
            streak_on = 0
            if engaged and streak_off >= exit_days:
                engaged = False
                last_cap = 1.0
        out_eng.append(engaged)
        out_cap.append(last_cap if engaged else 1.0)
    return out_eng, out_cap


def compute_rates_stress_series(
    frame: pd.DataFrame,
    params: Optional[RatesStressParams] = None,
) -> pd.DataFrame:
    """Full daily series with rates_cap in (0,1], engaged flag, reason.

    rates_cap=1.0 means non-binding (no stress). Combined budget should treat
    missing/None as absent leg; 1.0 is explicit pass-through.
    """
    p = params or DEFAULT_PARAMS
    feat = compute_rates_features(frame, p)
    raw_t: List[bool] = []
    raw_c: List[float] = []
    reasons: List[str] = []
    for _, row in feat.iterrows():
        trig, reason, cap = _raw_trigger_row(row, p)
        raw_t.append(trig)
        raw_c.append(cap)
        reasons.append(reason)
    eng, caps = apply_hysteresis(
        raw_t, raw_c, confirm_days=p.confirm_days, exit_days=p.exit_days
    )
    out = feat[["nominal_30y_z5", "nominal_30y_pct", "tips_yield_z5", "tips_yield_pct"]].copy()
    if "tlt_z5" in feat.columns:
        out["tlt_z5"] = feat["tlt_z5"]
    out["raw_trigger"] = raw_t
    out["raw_reason"] = reasons
    out["raw_cap"] = raw_c
    out["engaged"] = eng
    out["rates_cap"] = caps
    out["rates_cap_binding"] = [c < 0.999 for c in caps]
    return out


def rates_cap_snapshot(
    levels: Mapping[str, Any],
    history: Optional[pd.DataFrame] = None,
    params: Optional[RatesStressParams] = None,
) -> Dict[str, Any]:
    """Point-in-time snapshot for consolidator when only latest row + history exist.

    Prefer compute_rates_stress_series on a full frame. This helper is for wiring
    when daily artifacts already expose z/pct.
    """
    p = params or DEFAULT_PARAMS
    if history is not None and not history.empty:
        series = compute_rates_stress_series(history, p)
        last = series.iloc[-1]
        return {
            "rates_cap": float(last["rates_cap"]),
            "engaged": bool(last["engaged"]),
            "reason": str(last["raw_reason"]),
            "nominal_30y_z5": None if pd.isna(last["nominal_30y_z5"]) else float(last["nominal_30y_z5"]),
            "nominal_30y_pct": None if pd.isna(last["nominal_30y_pct"]) else float(last["nominal_30y_pct"]),
            "params": asdict(p),
        }
    trig, reason, cap = _raw_trigger_row(levels, p)
    # Without history, hysteresis cannot run; require confirm_days==1 semantics
    engaged = bool(trig) if p.confirm_days <= 1 else False
    return {
        "rates_cap": float(cap if engaged else 1.0),
        "engaged": engaged,
        "reason": reason if engaged else ("pending_confirm" if trig else reason),
        "nominal_30y_z5": levels.get("nominal_30y_z5"),
        "nominal_30y_pct": levels.get("nominal_30y_pct"),
        "params": asdict(p),
        "note": "no_history_hysteresis_skipped" if p.confirm_days > 1 else None,
    }


def params_from_mapping(cfg: Optional[Mapping[str, Any]]) -> RatesStressParams:
    base = asdict(DEFAULT_PARAMS)
    if not cfg:
        return RatesStressParams(**base)
    for k in list(base):
        if k in cfg and cfg[k] is not None:
            base[k] = cfg[k]
    return RatesStressParams(**base)


def overlap_with_duration_state(
    rates: pd.DataFrame,
    denom_state: pd.Series,
    *,
    duration_label: str = "久期压力",
    tight_keywords: Iterable[str] = ("久期压力", "美元压力", "信用传导", "仓位主导"),
) -> Dict[str, Any]:
    """Diagnose incremental value vs existing denominator stress ceilings."""
    idx = rates.index.intersection(denom_state.index)
    r = rates.reindex(idx)
    s = denom_state.reindex(idx).astype(str)
    eng = r["engaged"].fillna(False).astype(bool)
    is_dur = s.str.contains(duration_label, regex=False)
    is_tight = s.apply(lambda x: any(k in x for k in tight_keywords))

    both = int((eng & is_dur).sum())
    rates_only = int((eng & ~is_tight).sum())
    rates_only_not_dur = int((eng & ~is_dur).sum())
    dur_only = int((~eng & is_dur).sum())
    return {
        "n_days": int(len(idx)),
        "rates_engaged_days": int(eng.sum()),
        "duration_days": int(is_dur.sum()),
        "overlap_duration": both,
        "rates_engaged_not_duration": rates_only_not_dur,
        "rates_engaged_not_any_tight": rates_only,
        "duration_not_rates": dur_only,
        "incremental_share": round(rates_only / max(int(eng.sum()), 1), 4),
    }
