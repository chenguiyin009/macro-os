"""Positioning Path Overlay (PPO) — observation-layer path gate.

See docs/research/2026-08-21-ppo-positioning-path-design.md
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


PATH_REPAIR = "REPAIR"
PATH_DETERIOR = "DETERIOR"
PATH_BAD_EASE = "BAD_EASE"
PATH_MIXED = "MIXED"
PATH_UNKNOWN = "UNKNOWN"

PATH_ZH = {
    PATH_REPAIR: "定位修复",
    PATH_DETERIOR: "定位恶化",
    PATH_BAD_EASE: "糟糕宽松",
    PATH_MIXED: "混合/未确认",
    PATH_UNKNOWN: "数据不足",
}

GATE_ALLOW = "ALLOW_DISCUSS"
GATE_BLOCK = "BLOCK_ADD"
GATE_NEUTRAL = "NEUTRAL"

GATE_FOR_PATH = {
    PATH_REPAIR: GATE_ALLOW,
    PATH_DETERIOR: GATE_BLOCK,
    PATH_BAD_EASE: GATE_BLOCK,
    PATH_MIXED: GATE_NEUTRAL,
    PATH_UNKNOWN: GATE_NEUTRAL,
}

PATH_SEVERITY = {
    PATH_BAD_EASE: 3,
    PATH_DETERIOR: 2,
    PATH_MIXED: 1,
    PATH_REPAIR: 0,
    PATH_UNKNOWN: 1,
}


@dataclass
class PPOParams:
    mode: str = "flag_only"  # flag_only | soft_cap
    z_dead: float = 0.5
    z_enter: float = 1.0
    nq_break_ret_20: float = -0.05
    nq_lead_eps: float = 0.0
    # Structure (LH/LL) swing detection
    swing_left: int = 2
    swing_right: int = 2
    swing_lookback: int = 60  # bars of history for last 2 swings
    require_lh_ll_for_deteriorate: bool = True
    # Soft caps
    soft_cap_deteriorate: float = 0.55
    soft_cap_bad_ease: float = 0.45
    # Path hysteresis (held path). DETERIOR entry uses max(confirm_days, deteriorate_confirm_days)
    confirm_days: int = 2
    exit_days: int = 2
    deteriorate_confirm_days: int = 3
    enabled: bool = True


def params_from_mapping(cfg: Optional[Mapping[str, Any]] = None) -> PPOParams:
    base = asdict(PPOParams())
    if not cfg:
        return PPOParams(**base)
    for k in list(base):
        if k in cfg and cfg[k] is not None:
            base[k] = cfg[k]
    return PPOParams(**base)


def _f(v: Any) -> float:
    try:
        if v is None:
            return float("nan")
        x = float(v)
        if x != x:
            return float("nan")
        return x
    except (TypeError, ValueError):
        return float("nan")


def _finite(x: float) -> bool:
    return x == x


def classify_curve_skew(
    tips_z5: Any,
    n30_z5: Any,
    *,
    z_dead: float = 0.5,
) -> str:
    t = _f(tips_z5)
    n = _f(n30_z5)
    if not _finite(t) or not _finite(n):
        return "mixed"
    t_up, t_dn = t >= z_dead, t <= -z_dead
    n_up, n_dn = n >= z_dead, n <= -z_dead
    if t_up and n_up:
        return "both_tight"
    if t_dn and n_dn:
        return "both_easy"
    if t_dn and n_up:
        return "ten_easy_thirty_tight"
    if t_up and n_dn:
        return "ten_tight_thirty_easy"
    return "mixed"


def detect_swing_structure(
    closes: Sequence[float],
    *,
    left: int = 2,
    right: int = 2,
) -> Dict[str, Any]:
    """Fractal swing LH/LL + HH/HL on a close series (oldest -> newest).

    A swing high at i: close[i] == max(close[i-left:i+right+1])
    A swing low at i:  close[i] == min(close[i-left:i+right+1])
    Uses the last two confirmed swing highs and lows (right bars must exist).
    """
    x = np.asarray(list(closes), dtype=float)
    n = len(x)
    empty = {
        "lower_high": False,
        "lower_low": False,
        "higher_high": False,
        "higher_low": False,
        "lh_ll": False,
        "hh_hl": False,
        "n_swing_high": 0,
        "n_swing_low": 0,
        "last_highs": [],
        "last_lows": [],
    }
    if n < left + right + 3:
        return empty

    highs_idx: List[int] = []
    lows_idx: List[int] = []
    # last confirmable index is n-1-right
    last_i = n - 1 - right
    for i in range(left, last_i + 1):
        window = x[i - left : i + right + 1]
        if not np.isfinite(window).all():
            continue
        c = x[i]
        if c >= np.max(window) and np.sum(window == c) >= 1:
            # strict-ish: peak
            if c == np.max(window):
                highs_idx.append(i)
        if c <= np.min(window) and c == np.min(window):
            lows_idx.append(i)

    last_highs = [float(x[i]) for i in highs_idx[-2:]]
    last_lows = [float(x[i]) for i in lows_idx[-2:]]
    lh = len(last_highs) == 2 and last_highs[1] < last_highs[0]
    ll = len(last_lows) == 2 and last_lows[1] < last_lows[0]
    hh = len(last_highs) == 2 and last_highs[1] > last_highs[0]
    hl = len(last_lows) == 2 and last_lows[1] > last_lows[0]
    return {
        "lower_high": bool(lh),
        "lower_low": bool(ll),
        "higher_high": bool(hh),
        "higher_low": bool(hl),
        "lh_ll": bool(lh and ll),
        "hh_hl": bool(hh and hl),
        "n_swing_high": len(highs_idx),
        "n_swing_low": len(lows_idx),
        "last_highs": last_highs,
        "last_lows": last_lows,
    }


def structure_from_features(features: Mapping[str, Any]) -> Dict[str, Any]:
    """Prefer explicit flags; else compute from qqq_closes if provided."""
    if features.get("lh_ll") is not None or features.get("lower_high") is not None:
        lh = bool(features.get("lower_high"))
        ll = bool(features.get("lower_low"))
        lh_ll = bool(features.get("lh_ll")) if features.get("lh_ll") is not None else (lh and ll)
        hh = bool(features.get("higher_high"))
        hl = bool(features.get("higher_low"))
        hh_hl = bool(features.get("hh_hl")) if features.get("hh_hl") is not None else (hh and hl)
        return {
            "lower_high": lh,
            "lower_low": ll,
            "higher_high": hh,
            "higher_low": hl,
            "lh_ll": lh_ll,
            "hh_hl": hh_hl,
            "source": "flags",
        }
    closes = features.get("qqq_closes") or features.get("closes")
    if closes is not None and len(list(closes)) >= 10:
        left = int(features.get("swing_left") or 2)
        right = int(features.get("swing_right") or 2)
        out = detect_swing_structure(list(closes), left=left, right=right)
        out["source"] = "closes"
        return out
    return {
        "lower_high": False,
        "lower_low": False,
        "higher_high": False,
        "higher_low": False,
        "lh_ll": False,
        "hh_hl": False,
        "source": "missing",
    }


def _credit_wide(features: Mapping[str, Any], z_dead: float = 0.5) -> bool:
    hy = _f(features.get("hy_z5"))
    if _finite(hy) and hy <= -1.0:
        return True
    tier = str(features.get("denom_tier") or "")
    state = str(features.get("denom_state") or features.get("main_state") or "")
    if tier == "crisis" and ("信用" in state or "CREDIT" in state.upper() or "仓位主导" in state):
        return True
    if "信用传导" in state:
        return True
    return False


def classify_path(features: Mapping[str, Any], params: Optional[PPOParams] = None) -> Dict[str, Any]:
    """Return raw path classification + flags/metrics/advice seeds."""
    p = params or PPOParams()
    tips_z5 = _f(features.get("tips_z5"))
    n30_z5 = _f(features.get("n30_z5"))
    qqq_r20 = _f(features.get("qqq_ret_20"))
    es_r20 = _f(features.get("es_ret_20"))
    hy_z5 = _f(features.get("hy_z5"))
    gold_z5 = _f(features.get("gold_z5"))
    bei_d5 = _f(features.get("bei_d5"))
    rates_engaged = bool(features.get("rates_engaged") or False)
    settle_ok = features.get("settle_ok")
    if settle_ok is None:
        settle_ok = True
    else:
        settle_ok = bool(settle_ok)

    struct = structure_from_features(
        {
            **dict(features),
            "swing_left": p.swing_left,
            "swing_right": p.swing_right,
        }
    )

    have_core = _finite(tips_z5) and _finite(qqq_r20)
    if not have_core:
        return {
            "path": PATH_UNKNOWN,
            "path_zh": PATH_ZH[PATH_UNKNOWN],
            "gate": GATE_NEUTRAL,
            "skew": classify_curve_skew(tips_z5, n30_z5, z_dead=p.z_dead),
            "flags": {},
            "metrics": {
                "tips_z5": None if not _finite(tips_z5) else round(tips_z5, 4),
                "n30_z5": None if not _finite(n30_z5) else round(n30_z5, 4),
                "qqq_ret_20": None if not _finite(qqq_r20) else round(qqq_r20, 4),
            },
            "structure": struct,
            "soft_cap": None,
            "mode": p.mode,
            "advice": {"do": ["等待分母与 NQ 数据齐全"], "dont": []},
        }

    real_not_rising = tips_z5 < p.z_dead
    real_falling = tips_z5 <= -p.z_dead
    n30_not_spiking = (not _finite(n30_z5)) or (
        n30_z5 < p.z_enter and not (rates_engaged and n30_z5 >= p.z_dead)
    )
    ret_bad = qqq_r20 <= p.nq_break_ret_20
    # Tight structure: require LH/LL when enabled and structure available
    struct_available = struct.get("source") != "missing"
    lh_ll = bool(struct.get("lh_ll"))
    hh_hl = bool(struct.get("hh_hl"))
    if p.require_lh_ll_for_deteriorate and struct_available:
        nq_struct_bad = bool(ret_bad and lh_ll)
    elif p.require_lh_ll_for_deteriorate and not struct_available:
        # no structure → do NOT fire DETERIOR on ret alone (tightened)
        nq_struct_bad = False
    else:
        nq_struct_bad = bool(ret_bad)

    # OK structure: positive 20d and not LH/LL; HH/HL is a plus
    nq_struct_ok = (qqq_r20 >= 0.0) and (not ret_bad) and (not lh_ll)
    if struct_available and hh_hl and qqq_r20 >= 0.0:
        nq_struct_ok = True

    if _finite(es_r20):
        nq_leads = qqq_r20 >= (es_r20 + p.nq_lead_eps)
        nq_leads_known = True
    else:
        nq_leads = True
        nq_leads_known = False
    credit_wide = _credit_wide(features, p.z_dead)
    gold_bid = _finite(gold_z5) and gold_z5 >= p.z_dead
    be_falling = _finite(bei_d5) and bei_d5 <= -2.0
    skew = classify_curve_skew(tips_z5, n30_z5, z_dead=p.z_dead)

    # BAD_EASE keeps ret_bad (crisis path); optional gold/credit
    if real_falling and be_falling and ret_bad and (credit_wide or gold_bid):
        path = PATH_BAD_EASE
    elif real_not_rising and nq_struct_bad and (not credit_wide):
        path = PATH_DETERIOR
    elif (
        settle_ok
        and n30_not_spiking
        and nq_struct_ok
        and nq_leads
        and skew != "both_tight"
        and not credit_wide
    ):
        path = PATH_REPAIR
    else:
        path = PATH_MIXED

    soft = path_soft_cap(path, p) if p.mode == "soft_cap" else None
    do, dont = _advice(path, skew)
    return {
        "path": path,
        "path_zh": PATH_ZH.get(path, path),
        "gate": GATE_FOR_PATH.get(path, GATE_NEUTRAL),
        "skew": skew,
        "flags": {
            "real_not_rising": real_not_rising,
            "real_falling": real_falling,
            "n30_not_spiking": n30_not_spiking,
            "ret_bad": ret_bad,
            "nq_struct_bad": nq_struct_bad,
            "nq_struct_ok": nq_struct_ok,
            "lh_ll": lh_ll,
            "hh_hl": hh_hl,
            "nq_leads": nq_leads,
            "nq_leads_known": nq_leads_known,
            "credit_wide": credit_wide,
            "gold_bid": gold_bid,
            "be_falling": be_falling,
            "settle_ok": bool(settle_ok),
            "structure_source": struct.get("source"),
        },
        "metrics": {
            "tips_z5": round(tips_z5, 4),
            "n30_z5": None if not _finite(n30_z5) else round(n30_z5, 4),
            "qqq_ret_20": round(qqq_r20, 4),
            "es_ret_20": None if not _finite(es_r20) else round(es_r20, 4),
            "hy_z5": None if not _finite(hy_z5) else round(hy_z5, 4),
            "gold_z5": None if not _finite(gold_z5) else round(gold_z5, 4),
            "bei_d5": None if not _finite(bei_d5) else round(bei_d5, 4),
        },
        "structure": struct,
        "soft_cap": soft,
        "mode": p.mode,
        "advice": {"do": do, "dont": dont},
    }


def path_soft_cap(path: str, params: Optional[PPOParams] = None) -> Optional[float]:
    p = params or PPOParams()
    if path == PATH_BAD_EASE:
        return float(p.soft_cap_bad_ease)
    if path == PATH_DETERIOR:
        return float(p.soft_cap_deteriorate)
    return None


def _advice(path: str, skew: str) -> Tuple[List[str], List[str]]:
    if path == PATH_REPAIR:
        return (
            ["结算与结构条件允许，可讨论有限再风险（非自动加满）"],
            ["把 10Y 回落当成可以无限拉估值的许可证", "忽视 30Y/曲线偏斜"],
        )
    if path == PATH_DETERIOR:
        return (
            ["等 NQ 出现 HH/HL 或至少结束 LH/LL 后再讨论加仓", f"记录曲线偏斜={skew}"],
            ["真实利率回落就抄 NQ/AI", "仅因 20D 回撤、无 LH/LL 就按恶化满仓砍", "把定位平仓当成衰退腿去追黄金"],
        )
    if path == PATH_BAD_EASE:
        return (
            ["降总风险叙事；检查信用与黄金是否确认坏宽松", "优先防御与流动性"],
            ["把股债双杀当普通利率松再加久期成长"],
        )
    if path == PATH_UNKNOWN:
        return (["等待关键数据"], [])
    return (
        ["维持观察；清单优先于叙事"],
        ["一次解释正确就外推为可预测"],
    )


def apply_path_hysteresis(
    raw_paths: Sequence[str],
    *,
    confirm_days: int = 2,
    exit_days: int = 2,
    deteriorate_confirm_days: Optional[int] = None,
) -> List[str]:
    """Severity hysteresis with optional extra confirm for entering DETERIOR/BAD_EASE."""
    if not raw_paths:
        return []
    confirm_days = max(1, int(confirm_days))
    exit_days = max(1, int(exit_days))
    det_confirm = max(confirm_days, int(deteriorate_confirm_days or confirm_days))
    held = str(raw_paths[0])
    streak_up = 0
    streak_dn = 0
    pending_up = held
    out: List[str] = []
    for raw in raw_paths:
        r = str(raw)
        hs = PATH_SEVERITY.get(held, 1)
        rs = PATH_SEVERITY.get(r, 1)
        need = det_confirm if r in (PATH_DETERIOR, PATH_BAD_EASE) and rs > hs else confirm_days
        if rs > hs:
            streak_dn = 0
            if r != pending_up:
                pending_up = r
                streak_up = 1
            else:
                streak_up += 1
            if streak_up >= need:
                held = pending_up
                streak_up = 0
        elif rs < hs:
            streak_up = 0
            streak_dn += 1
            pending_up = held
            if streak_dn >= exit_days:
                held = r
                streak_dn = 0
        else:
            if r != held:
                held = r
            streak_up = 0
            streak_dn = 0
            pending_up = held
        out.append(held)
    return out


def bind_path_day(
    raw_path: str,
    *,
    prev_held: Optional[str] = None,
    up_streak: int = 0,
    dn_streak: int = 0,
    confirm_days: int = 2,
    exit_days: int = 2,
    deteriorate_confirm_days: Optional[int] = None,
) -> Dict[str, Any]:
    confirm_days = max(1, int(confirm_days))
    exit_days = max(1, int(exit_days))
    det_confirm = max(confirm_days, int(deteriorate_confirm_days or confirm_days))
    if not prev_held:
        return {
            "raw_path": raw_path,
            "held_path": raw_path,
            "hyst": "init",
            "up_streak": 0,
            "dn_streak": 0,
        }
    held = prev_held
    hs = PATH_SEVERITY.get(held, 1)
    rs = PATH_SEVERITY.get(raw_path, 1)
    us, ds = int(up_streak), int(dn_streak)
    note = "hold"
    need = det_confirm if raw_path in (PATH_DETERIOR, PATH_BAD_EASE) and rs > hs else confirm_days
    if rs > hs:
        ds = 0
        us += 1
        if us >= need:
            held = raw_path
            us = 0
            note = "enter_worse"
        else:
            note = "pending_worse"
    elif rs < hs:
        us = 0
        ds += 1
        if ds >= exit_days:
            held = raw_path
            ds = 0
            note = "exit_better"
        else:
            note = "pending_better"
    else:
        if raw_path != held:
            held = raw_path
            note = "lateral"
        us = 0
        ds = 0
    return {
        "raw_path": raw_path,
        "held_path": held,
        "hyst": note,
        "up_streak": us,
        "dn_streak": ds,
        "confirm_needed": need,
    }


def finalize_snapshot(
    classified: Mapping[str, Any],
    *,
    held_path: str,
    hyst: Optional[Mapping[str, Any]] = None,
    params: Optional[PPOParams] = None,
) -> Dict[str, Any]:
    p = params or PPOParams()
    path = held_path
    soft = path_soft_cap(path, p) if p.mode == "soft_cap" else None
    do, dont = _advice(path, str(classified.get("skew") or "mixed"))
    out = dict(classified)
    out["path"] = path
    out["path_zh"] = PATH_ZH.get(path, path)
    out["gate"] = GATE_FOR_PATH.get(path, GATE_NEUTRAL)
    out["soft_cap"] = soft
    out["mode"] = p.mode
    out["advice"] = {"do": do, "dont": dont}
    out["confirm"] = (
        dict(hyst) if hyst else {"held_path": path, "raw_path": classified.get("path"), "hyst": "n/a"}
    )
    return out
