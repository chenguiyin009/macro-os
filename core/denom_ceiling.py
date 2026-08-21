"""Denominator ceiling binding + exit hysteresis (D-enhancement + Scheme C).

Scheme C:
  - Split crisis into credit-crisis (hard floor) vs liquidity-squeeze (higher floor)
  - Faster exit from tight/squeeze (confirm_exit default 2)
  - Optional per-tier exit days
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence


TIER_ORDER = ("crisis", "squeeze", "tight", "unconfirmed", "risk_on")
TIER_RANK = {
    "crisis": 0,
    "squeeze": 1,
    "tight": 2,
    "unconfirmed": 3,
    "risk_on": 4,
    "default": 3,
}


@dataclass
class DenomCeilingParams:
    confirm_enter: int = 1
    confirm_exit: int = 2  # Scheme C: faster exit (was 3)
    # Extra days required to leave crisis/squeeze specifically (None = use confirm_exit)
    crisis_exit: int = 2
    squeeze_exit: int = 2
    state_tier_map: Optional[Dict[str, str]] = None


DEFAULT_STATE_TIER_MAP: Dict[str, str] = {
    # hard credit / positioning crisis
    "信用传导": "crisis",
    "仓位主导": "crisis",
    "HARD_VETO": "crisis",
    "CRISIS": "crisis",
    "危机": "crisis",
    # liquidity squeeze — Scheme C softer floor
    "LIQUIDITY_SQUEEZE": "squeeze",
    "SQUEEZE": "squeeze",
    "流动性挤兑": "squeeze",
    "挤兑": "squeeze",
    # tight
    "久期压力": "tight",
    "美元压力": "tight",
    "RISK_OFF": "tight",
    "TIGHT": "tight",
    "TRANSITION": "tight",
    "紧缩": "tight",
    "偏紧": "tight",
    "压力": "tight",
    "过渡": "tight",
    # unconfirmed
    "分裂": "unconfirmed",
    "未确认": "unconfirmed",
    "横盘": "unconfirmed",
    "混合": "unconfirmed",
    "UNCONFIRMED": "unconfirmed",
    "预热": "unconfirmed",
    # risk_on
    "分母端宽松": "risk_on",
    "RISK_ON": "risk_on",
    "宽松": "risk_on",
}


def _ceilings_from_cfg(block: Mapping[str, Any]) -> Dict[str, float]:
    crisis = block.get("crisis") or {}
    squeeze = block.get("squeeze") or {}
    # backward compat: if squeeze missing, use max(crisis, 0.20) style default
    crisis_c = float(crisis.get("ceiling", 0.10))
    squeeze_c = float(squeeze.get("ceiling", max(crisis_c, 0.20)))
    return {
        "crisis": crisis_c,
        "squeeze": squeeze_c,
        "tight": float((block.get("tight") or {}).get("ceiling", 0.35)),
        "unconfirmed": float((block.get("unconfirmed") or {}).get("ceiling", 0.55)),
        "risk_on": float((block.get("risk_on") or {}).get("ceiling", 0.80)),
        "default": float(block.get("default", 0.55)),
    }


def classify_denom_tier(
    state: Optional[str],
    block: Optional[Mapping[str, Any]] = None,
    state_tier_map: Optional[Mapping[str, str]] = None,
) -> str:
    s = (state or "").replace(" ", "")
    s_upper = s.upper()
    mapping = dict(DEFAULT_STATE_TIER_MAP)
    if state_tier_map:
        mapping.update({str(k): str(v) for k, v in state_tier_map.items()})

    for kw, tier in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        if not kw:
            continue
        if kw.isascii():
            if kw.upper() in s_upper:
                return tier if tier in TIER_RANK else "default"
        elif kw in s:
            return tier if tier in TIER_RANK else "default"

    block = block or {}
    # keyword fallback: order crisis, squeeze, unconfirmed, tight, risk_on
    for tier in ("crisis", "squeeze", "unconfirmed", "tight", "risk_on"):
        spec = block.get(tier) or {}
        for kw in spec.get("keywords") or []:
            if not kw:
                continue
            if str(kw).isascii():
                if str(kw).upper() in s_upper:
                    return tier
            elif str(kw) in s:
                return tier
    if "确认" in s and "未确认" not in s:
        return "risk_on"
    return "default"


def tier_to_ceiling(tier: str, block: Mapping[str, Any]) -> float:
    caps = _ceilings_from_cfg(block)
    if tier in caps:
        return float(caps[tier])
    return float(caps["default"])


def _exit_days_for_held(
    held_ceiling: float,
    raw_ceiling: float,
    block: Mapping[str, Any],
    hyst_cfg: Mapping[str, Any],
) -> int:
    """When loosening, exit delay can depend on how tight we were."""
    base_exit = int(hyst_cfg.get("confirm_exit", 2))
    caps = _ceilings_from_cfg(block)
    # identify held tier by matching ceiling
    held_tier = "default"
    best = 1e9
    for t, c in caps.items():
        if t == "default":
            continue
        if abs(c - held_ceiling) < 1e-9 and abs(c - held_ceiling) < best:
            held_tier = t
            best = 0.0
    if held_tier == "crisis":
        return int(hyst_cfg.get("crisis_exit", base_exit))
    if held_tier == "squeeze":
        return int(hyst_cfg.get("squeeze_exit", base_exit))
    return base_exit


def apply_ceiling_hysteresis(
    raw_ceilings: Sequence[float],
    *,
    confirm_enter: int = 1,
    confirm_exit: int = 2,
    block: Optional[Mapping[str, Any]] = None,
    hyst_cfg: Optional[Mapping[str, Any]] = None,
) -> List[float]:
    if not raw_ceilings:
        return []
    confirm_enter = max(1, int(confirm_enter))
    confirm_exit = max(1, int(confirm_exit))
    block = block or {}
    hyst_cfg = dict(hyst_cfg or {})
    hyst_cfg.setdefault("confirm_exit", confirm_exit)

    held = float(raw_ceilings[0])
    streak_tight = 0
    streak_loose = 0
    pending_tight = held
    pending_loose = held
    out: List[float] = []
    for raw in raw_ceilings:
        r = float(raw)
        if r < held - 1e-12:
            streak_loose = 0
            if abs(r - pending_tight) > 1e-12:
                pending_tight = r
                streak_tight = 1
            else:
                streak_tight += 1
            if streak_tight >= confirm_enter:
                held = pending_tight
                streak_tight = 0
        elif r > held + 1e-12:
            streak_tight = 0
            need = _exit_days_for_held(held, r, block, hyst_cfg)
            need = max(1, int(need))
            if abs(r - pending_loose) > 1e-12:
                pending_loose = r
                streak_loose = 1
            else:
                streak_loose += 1
            if streak_loose >= need:
                held = pending_loose
                streak_loose = 0
        else:
            streak_tight = 0
            streak_loose = 0
            pending_tight = held
            pending_loose = held
        out.append(held)
    return out


def bind_denom_ceiling(
    state: Optional[str],
    *,
    cfg_block: Optional[Mapping[str, Any]] = None,
    hyst_cfg: Optional[Mapping[str, Any]] = None,
    prev_ceiling: Optional[float] = None,
    loose_streak: int = 0,
    tight_streak: int = 0,
) -> Dict[str, Any]:
    block = dict(cfg_block or {})
    hc = dict(hyst_cfg or {})
    confirm_enter = int(hc.get("confirm_enter", 1))
    confirm_exit = int(hc.get("confirm_exit", 2))
    state_map = hc.get("state_tier_map")
    tier = classify_denom_tier(state, block, state_map if isinstance(state_map, dict) else None)
    raw = tier_to_ceiling(tier, block)

    if prev_ceiling is None:
        return {
            "tier": tier,
            "raw_ceiling": raw,
            "ceiling": raw,
            "tight_streak": 0,
            "loose_streak": 0,
            "hysteresis": "init",
            "confirm_enter": max(1, confirm_enter),
            "confirm_exit": max(1, confirm_exit),
        }

    held = float(prev_ceiling)
    ts, ls = int(tight_streak), int(loose_streak)
    note = "hold"
    if raw < held - 1e-12:
        ls = 0
        ts += 1
        if ts >= max(1, confirm_enter):
            held = raw
            ts = 0
            note = "enter_tighter"
        else:
            note = "pending_tighter"
    elif raw > held + 1e-12:
        ts = 0
        need = _exit_days_for_held(held, raw, block, {**hc, "confirm_exit": confirm_exit})
        ls += 1
        if ls >= max(1, need):
            held = raw
            ls = 0
            note = "exit_looser"
        else:
            note = "pending_looser"
    else:
        ts = 0
        ls = 0
        note = "hold"

    return {
        "tier": tier,
        "raw_ceiling": raw,
        "ceiling": held,
        "tight_streak": ts,
        "loose_streak": ls,
        "hysteresis": note,
        "confirm_enter": max(1, confirm_enter),
        "confirm_exit": max(1, confirm_exit),
    }


def series_bind(
    states: Sequence[str],
    cfg_block: Mapping[str, Any],
    hyst_cfg: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    hc = dict(hyst_cfg or {})
    confirm_enter = int(hc.get("confirm_enter", 1))
    confirm_exit = int(hc.get("confirm_exit", 2))
    state_map = hc.get("state_tier_map") if isinstance(hc.get("state_tier_map"), dict) else None
    raws = []
    tiers = []
    for st in states:
        tier = classify_denom_tier(st, cfg_block, state_map)
        tiers.append(tier)
        raws.append(tier_to_ceiling(tier, cfg_block))
    helds = apply_ceiling_hysteresis(
        raws,
        confirm_enter=confirm_enter,
        confirm_exit=confirm_exit,
        block=cfg_block,
        hyst_cfg=hc,
    )
    out = []
    for st, tier, raw, held in zip(states, tiers, raws, helds):
        out.append({"state": st, "tier": tier, "raw_ceiling": raw, "ceiling": held})
    return out
