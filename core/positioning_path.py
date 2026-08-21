"""Positioning Path Overlay (PPO) — observation-layer path gate.

See docs/research/2026-08-21-ppo-positioning-path-design.md
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


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

# tighter / worse first for hysteresis rank when comparing severity
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
    soft_cap_deteriorate: float = 0.55
    soft_cap_bad_ease: float = 0.45
    confirm_days: int = 1
    exit_days: int = 2
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
        if x != x:  # nan
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
    """Label 10Y-real vs 30Y nominal/real stress proxy via z5."""
    t = _f(tips_z5)
    n = _f(n30_z5)
    if not _finite(t) or not _finite(n):
        return "mixed"
    t_up = t >= z_dead
    t_dn = t <= -z_dead
    n_up = n >= z_dead
    n_dn = n <= -z_dead
    if t_up and n_up:
        return "both_tight"
    if t_dn and n_dn:
        return "both_easy"
    if t_dn and n_up:
        return "ten_easy_thirty_tight"
    if t_up and n_dn:
        return "ten_tight_thirty_easy"
    return "mixed"


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
            "soft_cap": None,
            "mode": p.mode,
            "advice": {"do": ["等待分母与 NQ 数据齐全"], "dont": []},
        }

    real_not_rising = tips_z5 < p.z_dead
    real_falling = tips_z5 <= -p.z_dead
    n30_not_spiking = (not _finite(n30_z5)) or (n30_z5 < p.z_enter and not (rates_engaged and n30_z5 >= p.z_dead))
    nq_struct_bad = qqq_r20 <= p.nq_break_ret_20
    nq_struct_ok = (qqq_r20 >= 0.0) and (not nq_struct_bad)
    if _finite(es_r20):
        nq_leads = qqq_r20 >= (es_r20 + p.nq_lead_eps)
        nq_leads_known = True
    else:
        nq_leads = True  # skip lead requirement when ES missing
        nq_leads_known = False
    credit_wide = _credit_wide(features, p.z_dead)
    gold_bid = _finite(gold_z5) and gold_z5 >= p.z_dead
    be_falling = _finite(bei_d5) and bei_d5 <= -2.0  # bp over 5d-ish; soft

    skew = classify_curve_skew(tips_z5, n30_z5, z_dead=p.z_dead)

    path = PATH_MIXED
    if real_falling and be_falling and nq_struct_bad and (credit_wide or gold_bid):
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
            "nq_struct_bad": nq_struct_bad,
            "nq_struct_ok": nq_struct_ok,
            "nq_leads": nq_leads,
            "nq_leads_known": nq_leads_known,
            "credit_wide": credit_wide,
            "gold_bid": gold_bid,
            "be_falling": be_falling,
            "settle_ok": bool(settle_ok),
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
            ["等 NQ 结构转强并（若可得）领涨后再讨论加仓", f"记录曲线偏斜={skew}"],
            ["真实利率回落就抄 NQ/AI", "按信用危机式无脑砍仓（信用未确认时）", "把定位平仓当成衰退腿去追黄金"],
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
    confirm_days: int = 1,
    exit_days: int = 2,
) -> List[str]:
    """Hold worse paths longer; require confirm to enter BAD/DETERIOR; exit slower.

    Simplified: treat higher PATH_SEVERITY as "tighter".
    Enter higher severity after confirm_days; leave to lower after exit_days.
    """
    if not raw_paths:
        return []
    confirm_days = max(1, int(confirm_days))
    exit_days = max(1, int(exit_days))
    held = str(raw_paths[0])
    streak_up = 0
    streak_dn = 0
    pending_up = held
    pending_dn = held
    out: List[str] = []
    for raw in raw_paths:
        r = str(raw)
        hs = PATH_SEVERITY.get(held, 1)
        rs = PATH_SEVERITY.get(r, 1)
        if rs > hs:
            streak_dn = 0
            if r != pending_up:
                pending_up = r
                streak_up = 1
            else:
                streak_up += 1
            if streak_up >= confirm_days:
                held = pending_up
                streak_up = 0
        elif rs < hs:
            streak_up = 0
            if r != pending_dn:
                pending_dn = r
                streak_dn = 1
            else:
                streak_dn += 1
            if streak_dn >= exit_days:
                held = pending_dn
                streak_dn = 0
        else:
            # same severity rank — allow lateral move with confirm 1
            if r != held:
                held = r
            streak_up = 0
            streak_dn = 0
            pending_up = held
            pending_dn = held
        out.append(held)
    return out


def bind_path_day(
    raw_path: str,
    *,
    prev_held: Optional[str] = None,
    up_streak: int = 0,
    dn_streak: int = 0,
    confirm_days: int = 1,
    exit_days: int = 2,
) -> Dict[str, Any]:
    """Single-day hysteresis carry for consolidator."""
    confirm_days = max(1, int(confirm_days))
    exit_days = max(1, int(exit_days))
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
    if rs > hs:
        ds = 0
        us += 1
        if us >= confirm_days:
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
    out["confirm"] = dict(hyst) if hyst else {"held_path": path, "raw_path": classified.get("path"), "hyst": "n/a"}
    return out
