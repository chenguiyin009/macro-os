"""Denominator (funding-price) state machine — Python port of the TradingView
Pine Script '资金价格斜率状态机 v1.6' (by 本杰明乌萨奇).

================================================================================
DESIGN NOTES — READ BEFORE PROMOTING ANYTHING
================================================================================
* This is a FAITHFUL logic port, NOT a frozen production gate. The Pine author
  explicitly states the thresholds are hand-set and unbacktested. Therefore every
  threshold here is exposed as a parameter (DenominatorParams) and MUST be
  re-calibrated + backtested before any promotion into the frozen decision kernel.
* Inputs are grouped:
  - Core (FRED, always present in Macro OS): DFII10 (tips_yield), DGS10/30/2
    (nominal curve), T10YIE (bei_10y), DTWEXBGS rebased (dxy), HY OAS
    (hy_credit_spread), VIX.
  - Confirmation (OPTIONAL): IWM/KRE (financing transmission), QQQ/SOXX (duration
    uptake), GLD (gold), SPX (hedge test), VIX3M (vol term structure). When a
    column is absent, the corresponding confidence votes ABSTAIN (0) and any
    equity-gated branch relaxes (so the state machine still works on macro data
    alone). This matches the Pine philosophy where these are "受灾/广度" checks,
    not primary drivers.
* Pine runs per-bar on a chart. Here every row is a trading day, so `newDay` is
  always true; the hysteresis vars (qPrevDay / rawPrevDay / stC / quadC) are
  carried across rows via a sequential loop — exactly mirroring Pine's `var`
  persistence across bars.
* The "仓位主导(覆盖)" state requires equity hedge-failure detection
  (SPX<0 & GLD<0 & TLT<0 + VIX term inversion). Without equity data it can NEVER
  be emitted by this port; that is a documented limitation, not a bug.
================================================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

# --- State vocabulary (kept identical to the Pine source) -------------------
STATE_EASE = "分母端宽松"
STATE_DURATION = "久期压力"
STATE_DOLLAR = "美元压力"
STATE_CREDIT = "信用传导"
STATE_POSITIONING = "仓位主导(覆盖)"
STATE_SPLIT = "分裂/未确认"
STATE_WARMUP = "预热中"

DONT_DO = {
    STATE_EASE: "不做逆势宏观空头(空NQ/空黄金); 若20D未转负, 只按relief对待, 不按真宽松加久期",
    STATE_DURATION: "不抄底长久期(NQ/TLT/黄金多头缓); 压力在估值层, 别急着按信用危机减仓",
    STATE_DOLLAR: "不接海外高beta/EM/半导体的刀; 黄金多头需要额外理由",
    STATE_CREDIT: "不按'利率下行=宽松'买股; 降低总仓, 长债涨不等于股票安全",
    STATE_POSITIONING: "不用宏观逻辑扛仓位事件, 不加新宏观表达; 3-5日内看信用是否跟进定性质",
    STATE_SPLIT: "不加新表达, 降仓位; 允许说'今天不知道'",
    STATE_WARMUP: "等待数据预热",
}

# Series that are rate LEVELS in % (use first-difference x100 -> bp)
_RATE_COLS = ["tips_yield", "nominal_2y", "nominal_10y", "nominal_30y", "bei_10y", "hy_credit_spread"]
# Series that are price/index LEVELS (use ratio-1 x100 -> %)
_PX_COLS = ["dxy", "gold", "iwm", "kre", "qqq", "sox", "spx"]


@dataclass
class DenominatorParams:
    """All Pine `input.*` thresholds. Exposed (NOT frozen) — see module docstring."""

    w_trade: int = 5
    w_regime: int = 20
    w_vol: int = 60
    w_lvl: int = 756
    z_dead: float = 0.5
    z_enter: float = 1.0
    z_same: float = 0.25
    dur_tip: float = 6.8
    bp_trig: float = 4.0


# --------------------------------------------------------------------------- #
# Per-series statistics (mirror Pine f_rateAll / f_pxAll)                      #
# --------------------------------------------------------------------------- #
def _rate_stats(s: pd.Series, p: DenominatorParams):
    x = s.astype(float)
    d1 = (x - x.shift(1)) * 100.0
    d5 = (x - x.shift(p.w_trade)) * 100.0
    d20 = (x - x.shift(p.w_regime)) * 100.0
    vol = d1.rolling(p.w_vol).std()
    z5 = d5 / (vol * np.sqrt(p.w_trade))
    z20 = d20 / (vol * np.sqrt(p.w_regime))
    pct = x.rolling(p.w_lvl).apply(lambda a: float((a < a[-1]).mean() * 100.0), raw=True)
    return d1, d5, d20, z5, z20, pct


def _px_stats(s: pd.Series, p: DenominatorParams):
    x = s.astype(float)
    t1 = (x / x.shift(1) - 1) * 100.0
    r5 = (x / x.shift(p.w_trade) - 1) * 100.0
    r20 = (x / x.shift(p.w_regime) - 1) * 100.0
    vol = t1.rolling(p.w_vol).std()
    z5 = r5 / (vol * np.sqrt(p.w_trade))
    z20 = r20 / (vol * np.sqrt(p.w_regime))
    pct = x.rolling(p.w_lvl).apply(lambda a: float((a < a[-1]).mean() * 100.0), raw=True)
    return t1, r5, r20, z5, z20, pct


def _dir(z, z_dead):
    if z is None or (isinstance(z, float) and np.isnan(z)):
        return 0
    return 1 if z >= z_dead else (-1 if z <= -z_dead else 0)


def _arrow(z):
    return "↑" if z > 0 else "↓"


def _slope(z5, z20, p):
    if z5 is None or z20 is None or np.isnan(z5) or np.isnan(z20):
        return "—"
    a5, a20 = abs(z5), abs(z20)
    if a5 < p.z_dead and a20 < p.z_dead:
        return "横盘"
    if a5 >= p.z_dead and a20 < p.z_dead:
        return _arrow(z5) + " 新启动"
    if a5 < p.z_dead and a20 >= p.z_dead:
        return _arrow(z20) + " 暂停·衰竭"
    if np.sign(z5) != np.sign(z20):
        return "转" + _arrow(z5)
    if a5 >= a20 + p.z_same:
        return _arrow(z5) + " 加速"
    if a5 <= a20 - p.z_same:
        return _arrow(z5) + " 减速"
    return _arrow(z5) + " 延续"


def _v(v):
    return "✓" if v == 1 else ("✗" if v == -1 else "○")


def _nz(v):
    return 0.0 if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


# --------------------------------------------------------------------------- #
# Confidence (v1.6 four-item structure)                                       #
# --------------------------------------------------------------------------- #
def _confidence(state, r, p, has):
    tips_z5 = r.get("tips_yield_z5")
    n30_z5 = r.get("nominal_30y_z5")
    dxy_z5 = r.get("dxy_z5")
    cs_z5 = r.get("hy_credit_spread_z5")
    dGld = _dir(r.get("gold_z5"), p.z_dead) if has("gold") else 0
    dCs = _dir(cs_z5, p.z_dead)
    dDxy = _dir(dxy_z5, p.z_dead)

    if state == STATE_EASE:
        i1 = 1 if (_nz(tips_z5) <= -p.z_enter and _nz(n30_z5) <= -p.z_enter) else 0
        i2, i3, i4 = dGld, dCs, dDxy
        score = i1 + i2 + i3 + i4
        det = "强度" + _v(i1) + " 黄金" + _v(i2) + " 信用" + _v(i3) + " 美元" + _v(i4)
    elif state == STATE_DURATION:
        i1 = 1 if (_nz(tips_z5) >= p.z_enter and _nz(n30_z5) >= p.z_enter) else 0
        i2 = dGld
        qz, sz = r.get("qqq_z5"), r.get("sox_z5")
        if (not pd.isna(qz) and qz <= -p.z_dead) or (not pd.isna(sz) and sz <= -p.z_dead):
            i3 = 1
        elif (not pd.isna(qz) and qz >= p.z_dead and not pd.isna(sz) and sz >= p.z_dead):
            i3 = -1
        else:
            i3 = 0
        tips_d5, bei_d5 = r.get("tips_yield_d5"), r.get("bei_10y_d5")
        if not pd.isna(tips_d5) and not pd.isna(bei_d5):
            i4 = 1 if abs(tips_d5) >= abs(bei_d5) else (-1 if abs(bei_d5) >= 2.0 * abs(tips_d5) else 0)
        else:
            i4 = 0
        score = i1 + i2 + i3 + i4
        det = "强度" + _v(i1) + " 黄金" + _v(i2) + " 受灾" + _v(i3) + " 来源" + _v(i4)
    elif state == STATE_DOLLAR:
        i1 = 1 if (_nz(dxy_z5) >= 1.5 or (_nz(dxy_z5) >= p.z_enter and _nz(tips_z5) >= p.z_dead)) else 0
        sz = r.get("sox_z5")
        i2 = 1 if (not pd.isna(sz) and sz <= -p.z_dead) else (-1 if (not pd.isna(sz) and sz >= p.z_dead) else 0)
        i3 = dGld
        dxy_tag = r.get("__dxyTag", "")
        i4 = 1 if dxy_tag == "(偏压力: 避险/融资)" else (-1 if dxy_tag == "(偏良性: 利差驱动)" else 0)
        score = i1 + i2 + i3 + i4
        det = "强度" + _v(i1) + " 受灾" + _v(i2) + " 黄金" + _v(i3) + " 性质" + _v(i4)
    elif state == STATE_CREDIT:
        i1 = 1 if _nz(cs_z5) <= -1.5 else 0
        iwm, kre = r.get("iwm_z5"), r.get("kre_z5")
        if (not pd.isna(iwm) and iwm <= -p.z_dead and not pd.isna(kre) and kre <= -p.z_dead):
            i2 = 1
        elif (not pd.isna(iwm) and iwm >= p.z_dead and not pd.isna(kre) and kre >= p.z_dead):
            i2 = -1
        else:
            i2 = 0
        vix, vix3m = r.get("vix"), r.get("vix3m")
        if not pd.isna(vix) and not pd.isna(vix3m) and vix3m > 0:
            i3 = 1 if vix / vix3m > 1.0 else (-1 if vix / vix3m < 0.85 else 0)
        else:
            i3 = 0
        spx = r.get("spx_z5")
        i4 = 1 if (not pd.isna(spx) and spx <= -p.z_dead) else (-1 if (not pd.isna(spx) and spx >= p.z_dead) else 0)
        score = i1 + i2 + i3 + i4
        det = "强度" + _v(i1) + " 广度" + _v(i2) + " 波动" + _v(i3) + " 股指" + _v(i4)
    else:
        return None, "", "—"

    lbl = "高" if score >= 3 else ("中" if score >= 1 else "低")
    return score, det, lbl


# --------------------------------------------------------------------------- #
# Main entry                                                                  #
# --------------------------------------------------------------------------- #
def compute_denominator_states(frame: pd.DataFrame, params: Optional[DenominatorParams] = None) -> pd.DataFrame:
    """Run the denominator state machine over a daily feature frame.

    `frame` must be date-indexed and contain the core columns
    (tips_yield, nominal_2y/10y/30y, bei_10y, dxy, hy_credit_spread, vix) plus
    any optional confirmation columns (gold, iwm, kre, qqq, sox, spx, vix3m).
    Returns a date-indexed DataFrame with the daily state machine output.
    """
    p = params or DenominatorParams()
    df = frame.copy()
    has = lambda c: c in df.columns and bool(df[c].notna().any())

    for col in _RATE_COLS:
        if has(col):
            d1, d5, d20, z5, z20, pct = _rate_stats(df[col], p)
            df[f"{col}_d1"], df[f"{col}_d5"], df[f"{col}_d20"] = d1, d5, d20
            df[f"{col}_z5"], df[f"{col}_z20"], df[f"{col}_pct"] = z5, z20, pct
    for col in _PX_COLS:
        if has(col):
            t1, r5, r20, z5, z20, pct = _px_stats(df[col], p)
            df[f"{col}_t1"], df[f"{col}_r5"], df[f"{col}_r20"] = t1, r5, r20
            df[f"{col}_z5"], df[f"{col}_z20"], df[f"{col}_pct"] = z5, z20, pct

    out = []
    stC = STATE_WARMUP
    quadC = "横盘/混合"
    raw_prev = STATE_SPLIT
    quad_prev = "横盘/混合"

    for i in range(len(df)):
        r = df.iloc[i]
        tips_z5 = r.get("tips_yield_z5")
        n30_z5 = r.get("nominal_30y_z5")
        n10_z5 = r.get("nominal_10y_z5")
        n02_z5 = r.get("nominal_2y_z5")
        dxy_z5 = r.get("dxy_z5")
        cs_z5 = r.get("hy_credit_spread_z5")

        dTips = _dir(tips_z5, p.z_dead)
        dN10 = _dir(n10_z5, p.z_dead)

        ready = (
            has("tips_yield") and not pd.isna(tips_z5)
            and not pd.isna(dxy_z5)
            and not pd.isna(cs_z5)
            and not pd.isna(n30_z5)
        )

        # --- four-quadrant (nominal x real) ---
        quad_raw = "横盘/混合"
        if dN10 == 1 and dTips == 1:
            quad_raw = "Q1 压力测试"
        elif dN10 == -1 and dTips == 1:
            quad_raw = "Q2 债务通缩"
        elif dN10 == -1 and dTips == -1:
            quad_raw = "Q3 宽松/衰退买债"
        elif dN10 == 1 and dTips == -1:
            quad_raw = "Q4 再通胀"
        if ready and quad_raw == quad_prev and quad_raw != quadC:
            quadC = quad_raw

        # --- positioning override (requires equity hedge; unavailable here) ---
        pos_override = False

        # --- state booleans ---
        st_credit = not pd.isna(cs_z5) and cs_z5 <= -1.0
        if has("iwm") or has("kre"):
            iwm, kre = r.get("iwm_z5"), r.get("kre_z5")
            st_credit = st_credit and (
                (not pd.isna(iwm) and iwm <= -0.5) or (not pd.isna(kre) and kre <= -0.5)
            )
        st_dollar = not pd.isna(dxy_z5) and dxy_z5 >= 1.0 and not pd.isna(tips_z5) and tips_z5 >= -0.25
        st_durat = (
            not pd.isna(tips_z5) and tips_z5 >= 0.5
            and not pd.isna(n30_z5) and n30_z5 >= 0.5
            and (pd.isna(cs_z5) or cs_z5 > -1.0)
        )
        st_ease = (
            not pd.isna(tips_z5) and tips_z5 <= -0.5
            and not pd.isna(n30_z5) and n30_z5 <= -0.5
            and not pd.isna(dxy_z5) and dxy_z5 <= 0.25
            and (pd.isna(cs_z5) or cs_z5 >= -0.5)
        )

        raw_st = STATE_SPLIT
        if not ready:
            raw_st = STATE_WARMUP
        elif pos_override:
            raw_st = STATE_POSITIONING
        elif st_credit:
            raw_st = STATE_CREDIT
        elif st_dollar:
            raw_st = STATE_DOLLAR
        elif st_durat:
            raw_st = STATE_DURATION
        elif st_ease:
            raw_st = STATE_EASE

        # --- stC hysteresis ---
        strong_now = max(abs(_nz(tips_z5)), abs(_nz(dxy_z5)), abs(_nz(cs_z5))) >= p.z_enter
        if raw_st == STATE_POSITIONING:
            stC = raw_st
        elif raw_st != stC and (raw_st == raw_prev or strong_now):
            stC = raw_st

        # --- dxyTag (needs SPX for benign judgement; default pressure if absent) ---
        dxy_tag = ""
        if _dir(dxy_z5, p.z_dead) == 1:
            spx_z5 = r.get("spx_z5") if has("spx") else None
            n02_d5 = r.get("nominal_2y_d5")
            if (not pd.isna(spx_z5) and spx_z5 > 0) and (not pd.isna(n02_d5) and n02_d5 > 0):
                dxy_tag = "(偏良性: 利差驱动)"
            else:
                dxy_tag = "(偏压力: 避险/融资)"
        r = r.copy()
        r["__dxyTag"] = dxy_tag

        # --- nomSrc (actual-rate vs BEI driven) ---
        tips_d5, bei_d5 = r.get("tips_yield_d5"), r.get("bei_10y_d5")
        if not pd.isna(tips_d5) and not pd.isna(bei_d5):
            nom_src = "实际利率驱动" if abs(tips_d5) >= abs(bei_d5) else "通胀补偿驱动"
        else:
            nom_src = "—"

        # --- confidence ---
        conf_score, conf_det, conf_lbl = _confidence(stC, r, p, has)

        # --- tips note (display) ---
        tips_high = not pd.isna(r.get("tips_yield_pct")) and r.get("tips_yield_pct") >= 80
        if tips_high and dTips == -1:
            tips_note = "高位缓和(relief)"
        elif tips_high and dTips == 1:
            tips_note = "高位再紧缩"
        elif dTips == 1:
            tips_note = "早期压力"
        elif dTips == -1:
            tips_note = "改善"
        else:
            tips_note = "横盘"

        out.append({
            "state": stC,
            "raw_state": raw_st,
            "confidence_label": conf_lbl,
            "confidence_score": conf_score if conf_score is not None else "",
            "confidence_detail": conf_det,
            "quadrant": quadC,
            "nominal_source": nom_src,
            "dxy_tag": dxy_tag,
            "tips_note": tips_note,
            "dont_do": DONT_DO.get(stC, DONT_DO[STATE_SPLIT]),
            "strong_now": strong_now,
            "tips_z5": _nz(tips_z5),
            "dxy_z5": _nz(dxy_z5),
            "cs_z5": _nz(cs_z5),
            "n30_z5": _nz(n30_z5),
        })

        raw_prev = raw_st
        quad_prev = quadC

    result = pd.DataFrame(out, index=df.index)
    result.index.name = "date"
    return result
