"""Scheme A dual-budget re-risk: defense ceiling + permit + stepped target.

See docs/research/2026-08-21-scheme-a-dual-budget-design.md
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


@dataclass
class ReRiskParams:
    enabled: bool = True
    offense_cap: float = 0.80
    max_step_up: float = 0.10
    step_confirm_days: int = 3
    min_hold_after_up_days: int = 2
    tech_min_for_permit: float = 0.50
    require_not_lh_ll: bool = True
    require_ret20_nonneg: bool = True
    block_both_tight: bool = True
    block_ppo_block_add: bool = True
    # denom tiers that allow permit
    permit_tiers: Tuple[str, ...] = ("unconfirmed", "risk_on", "default")


def params_from_mapping(cfg: Optional[Mapping[str, Any]] = None) -> ReRiskParams:
    base = asdict(ReRiskParams())
    # dataclass with tuple — asdict ok
    if not cfg:
        return ReRiskParams()
    kw = {}
    for k in base:
        if k in cfg and cfg[k] is not None:
            kw[k] = cfg[k]
    if "permit_tiers" in kw and isinstance(kw["permit_tiers"], list):
        kw["permit_tiers"] = tuple(kw["permit_tiers"])
    return ReRiskParams(**{**base, **kw})


def _f(v: Any, default: Optional[float] = None) -> Optional[float]:
    if v is None:
        return default
    try:
        x = float(v)
        if x != x:
            return default
        return x
    except (TypeError, ValueError):
        return default


def evaluate_permit(
    *,
    denom_tier: Optional[str],
    denom_hyst: Optional[str] = None,
    tech_ceiling: Optional[float] = None,
    ppo_gate: Optional[str] = None,
    ppo_flags: Optional[Mapping[str, Any]] = None,
    rates_shadow: Optional[Mapping[str, Any]] = None,
    curve_skew: Optional[str] = None,
    qqq_ret_20: Optional[float] = None,
    params: Optional[ReRiskParams] = None,
) -> Dict[str, Any]:
    """Return permit bool + blockers (empty blockers => permit)."""
    p = params or ReRiskParams()
    blockers: List[str] = []
    tier = (denom_tier or "unknown").lower() if denom_tier else "unknown"
    # normalize chinese tiers from denom_ceiling
    tier_map = {
        "unconfirmed": "unconfirmed",
        "risk_on": "risk_on",
        "tight": "tight",
        "crisis": "crisis",
        "default": "default",
        "unknown": "unknown",
        "legacy": "unconfirmed",
    }
    tier_n = tier_map.get(tier, tier)

    if tier_n == "crisis" or tier_n == "tight":
        blockers.append(f"denom_tier_{tier_n}")
    elif tier_n not in p.permit_tiers and tier_n not in ("unconfirmed", "risk_on", "default"):
        blockers.append(f"denom_tier_{tier_n}")

    if denom_hyst == "pending_looser" and tier_n in ("tight", "crisis"):
        # still held tight — already blocked by tier; keep explicit
        if "denom_tier_tight" not in blockers and "denom_tier_crisis" not in blockers:
            blockers.append("denom_pending_looser")

    tc = _f(tech_ceiling)
    if tc is not None and tc <= p.tech_min_for_permit + 1e-12:
        blockers.append("tech_stress")

    if p.block_ppo_block_add and ppo_gate == "BLOCK_ADD":
        blockers.append("ppo_block_add")

    flags = dict(ppo_flags or {})
    if p.require_not_lh_ll and flags.get("lh_ll") is True:
        blockers.append("lh_ll")
    # if structure missing, do not block on lh_ll
    if p.require_ret20_nonneg:
        r20 = _f(qqq_ret_20)
        if r20 is not None and r20 < 0:
            blockers.append("ret20_neg")

    skew = curve_skew or (rates_shadow or {}).get("skew")
    # skew may live on ppo
    if skew is None and flags.get("structure_source"):
        pass
    if p.block_both_tight and skew == "both_tight":
        blockers.append("both_tight")
    if (rates_shadow or {}).get("engaged") and skew in ("both_tight", "ten_easy_thirty_tight"):
        # engaged extreme: block size-up when 30y still hot alone is softer — only both_tight hard
        pass


    permit = len(blockers) == 0
    return {"risk_on_permit": permit, "permit_blockers": blockers}


def step_target(
    *,
    prev_target: Optional[float],
    defense_ceiling: float,
    offense_cap: float,
    permit: bool,
    permit_streak: int,
    days_since_up: int = 999,
    params: Optional[ReRiskParams] = None,
) -> Dict[str, Any]:
    """Fast cut to defense; slow step-up only when permit streak sufficient."""
    p = params or ReRiskParams()
    defense = float(max(0.0, min(1.0, defense_ceiling)))
    offense = float(max(0.0, min(1.0, offense_cap)))
    top = min(defense, offense)

    if prev_target is None:
        prev = defense
    else:
        prev = float(max(0.0, min(1.0, prev_target)))

    # 1) Defense always binds downward immediately
    if defense + 1e-12 < prev:
        return {
            "target_budget": round(defense, 4),
            "prev_target": round(prev, 4),
            "step": round(defense - prev, 4),
            "action": "cut_defense",
            "permit_streak": 0 if not permit else int(permit_streak),
            "days_since_up": int(days_since_up) + 1,
        }

    # 2) No permit: hold at min(prev, defense) — no raise
    if not permit or not p.enabled:
        held = min(prev, defense)
        return {
            "target_budget": round(held, 4),
            "prev_target": round(prev, 4),
            "step": round(held - prev, 4),
            "action": "hold_no_permit" if p.enabled else "hold_disabled",
            "permit_streak": 0,
            "days_since_up": int(days_since_up) + 1,
        }

    # 3) Permit path: need streak and chill after previous up
    streak = int(permit_streak)
    if streak < int(p.step_confirm_days):
        return {
            "target_budget": round(min(prev, top), 4),
            "prev_target": round(prev, 4),
            "step": 0.0,
            "action": "wait_confirm",
            "permit_streak": streak,
            "days_since_up": int(days_since_up) + 1,
        }

    if int(days_since_up) < int(p.min_hold_after_up_days):
        return {
            "target_budget": round(min(prev, top), 4),
            "prev_target": round(prev, 4),
            "step": 0.0,
            "action": "wait_hold_after_up",
            "permit_streak": streak,
            "days_since_up": int(days_since_up) + 1,
        }

    # step up toward top
    desired = min(top, prev + float(p.max_step_up))
    if desired <= prev + 1e-12:
        return {
            "target_budget": round(min(prev, top), 4),
            "prev_target": round(prev, 4),
            "step": 0.0,
            "action": "at_cap",
            "permit_streak": streak,
            "days_since_up": int(days_since_up) + 1,
        }

    return {
        "target_budget": round(desired, 4),
        "prev_target": round(prev, 4),
        "step": round(desired - prev, 4),
        "action": "step_up",
        "permit_streak": streak,
        "days_since_up": 0,
    }


def compute_re_risk(
    *,
    defense_ceiling: float,
    prev_state: Optional[Mapping[str, Any]] = None,
    denom_tier: Optional[str] = None,
    denom_hyst: Optional[str] = None,
    tech_ceiling: Optional[float] = None,
    ppo: Optional[Mapping[str, Any]] = None,
    rates_shadow: Optional[Mapping[str, Any]] = None,
    qqq_ret_20: Optional[float] = None,
    curve_skew: Optional[str] = None,
    params: Optional[ReRiskParams] = None,
) -> Dict[str, Any]:
    """Full day snapshot for consolidator."""
    p = params or ReRiskParams()
    prev_state = dict(prev_state or {})
    prev_target = _f(prev_state.get("target_budget"), default=None)
    prev_streak = int(prev_state.get("permit_streak") or 0)
    days_since_up = int(prev_state.get("days_since_up") if prev_state.get("days_since_up") is not None else 999)

    ppo = dict(ppo or {})
    flags = dict(ppo.get("flags") or {})
    skew = curve_skew or ppo.get("skew")
    if qqq_ret_20 is None:
        qqq_ret_20 = _f((ppo.get("metrics") or {}).get("qqq_ret_20"))

    perm = evaluate_permit(
        denom_tier=denom_tier,
        denom_hyst=denom_hyst,
        tech_ceiling=tech_ceiling,
        ppo_gate=ppo.get("gate"),
        ppo_flags=flags,
        rates_shadow=rates_shadow,
        curve_skew=skew,
        qqq_ret_20=qqq_ret_20,
        params=p,
    )
    permit = bool(perm["risk_on_permit"])
    streak = (prev_streak + 1) if permit else 0

    stepped = step_target(
        prev_target=prev_target if prev_target is not None else defense_ceiling,
        defense_ceiling=float(defense_ceiling),
        offense_cap=float(p.offense_cap),
        permit=permit,
        permit_streak=streak,
        days_since_up=days_since_up,
        params=p,
    )

    return {
        "enabled": bool(p.enabled),
        "defense_ceiling": round(float(defense_ceiling), 4),
        "offense_cap": round(float(p.offense_cap), 4),
        "risk_on_permit": permit,
        "permit_blockers": list(perm["permit_blockers"]),
        "permit_streak": int(stepped["permit_streak"] if not permit else streak),
        "target_budget": stepped["target_budget"],
        "prev_target": stepped["prev_target"],
        "step": stepped["step"],
        "action": stepped["action"],
        "days_since_up": stepped["days_since_up"],
        "params": {
            "max_step_up": p.max_step_up,
            "step_confirm_days": p.step_confirm_days,
            "min_hold_after_up_days": p.min_hold_after_up_days,
            "tech_min_for_permit": p.tech_min_for_permit,
        },
    }
