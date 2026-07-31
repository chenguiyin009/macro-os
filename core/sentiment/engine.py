"""Sentiment shadow engine — pure aggregation (V2.0 Phase 0)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.sentiment.flags import build_checklist, build_flags, build_narratives
from core.sentiment.models import CycleBlock, SentimentRawInput, SentimentShadowSnapshot
from core.sentiment.types import (
    AnchorState,
    BounceQuality,
    CycleStage,
    DataQuality,
    InterventionMode,
    IntradayPath,
    ReboundType,
    RescueSequenceStage,
    SnapshotSource,
    StructureQuality,
    TransmissionStage,
)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def compute_anchor(
    sse_last: Optional[float], anchor: float, buffer_pct: float
) -> Tuple[Optional[float], str, Optional[float]]:
    if sse_last is None or anchor <= 0:
        return None, AnchorState.UNKNOWN.value, None
    dist_bp = (sse_last / anchor - 1.0) * 10000.0
    rel = sse_last / anchor - 1.0
    if abs(rel) <= buffer_pct:
        state = AnchorState.IN_ZONE.value
    elif rel < -buffer_pct:
        state = AnchorState.BREAK_TEST.value
    else:
        state = AnchorState.ABOVE_BUFFER.value
    # hold quality unknown without path; caller may override
    return dist_bp, state, None


def compute_release_rate(
    limit_down_n: Optional[int], open_n: Optional[int]
) -> Optional[float]:
    if limit_down_n is None or open_n is None:
        return None
    # interpret: open_board among the stressed set; use open/(open+still) if still unknown
    # Phase0 convention: open_n is opens from the limit-down set; still = max(limit_down - open, 0) if open<=limit
    if open_n < 0 or limit_down_n < 0:
        return None
    if open_n == 0 and limit_down_n == 0:
        return 1.0
    # Prefer open/(limit_down) when open tracks that cohort (07-21: 20/21)
    denom = max(limit_down_n, open_n)
    if denom <= 0:
        return None
    return _clamp(open_n / float(denom))


def _resolve_stress_pair(raw: SentimentRawInput):
    """Source-preference for the deleveraging/forced-sell stress probe.

    Prefer the margin Top100 universe when both its fields are present; otherwise
    fall back to the board-behavior substitute (consecutive limit-down + open-board)
    sourced from tdx_screener. Returns (limit_down_n, open_n, source_tag).
    Original margin path is untouched when margin data exists.
    """
    if (
        raw.margin_top100_limit_down_n is not None
        and raw.margin_top100_open_board_n is not None
    ):
        return (
            raw.margin_top100_limit_down_n,
            raw.margin_top100_open_board_n,
            "margin",
        )
    if (
        raw.limit_down_consecutive_n is not None
        and raw.limit_down_open_n is not None
    ):
        return (
            raw.limit_down_consecutive_n,
            raw.limit_down_open_n,
            "board",
        )
    return None, None, None


def compute_deleveraging_stress(
    limit_down_n: Optional[int],
    new_ld: Optional[int],
    release_rate: Optional[float],
    forced: Optional[float],
    thr_ld: int,
) -> Optional[float]:
    if limit_down_n is None and forced is None:
        return None
    score = 0.0
    parts = 0
    if limit_down_n is not None:
        score += _clamp(limit_down_n / float(max(thr_ld, 1)))
        parts += 1
    if new_ld is not None:
        score += _clamp(new_ld / 15.0)
        parts += 1
    if release_rate is not None:
        score += 1.0 - release_rate
        parts += 1
    if forced is not None:
        score += _clamp(forced)
        parts += 1
    if parts == 0:
        return None
    return _clamp(score / parts)


def compute_transmission(
    hs300: Optional[float],
    chinext: Optional[float],
    csi1000: Optional[float],
) -> Tuple[str, Optional[float]]:
    def ok(x: Optional[float], t: float = 0.55) -> bool:
        return x is not None and x >= t

    if ok(csi1000) and ok(chinext) and ok(hs300):
        stage = TransmissionStage.TO_CSI1000.value
    elif ok(chinext) and ok(hs300):
        stage = TransmissionStage.TO_CHINEXT.value
    elif ok(hs300):
        stage = TransmissionStage.HS300_ONLY.value
    elif hs300 is None and chinext is None and csi1000 is None:
        stage = TransmissionStage.UNKNOWN.value
    else:
        stage = TransmissionStage.FAILED.value

    vals = [v for v in (hs300, chinext, csi1000) if v is not None]
    score = sum(vals) / len(vals) if vals else None
    return stage, score


def locate_cycle(raw: SentimentRawInput, thr: dict) -> CycleBlock:
    ld, open_n, stress_src = _resolve_stress_pair(raw)
    release = compute_release_rate(ld, open_n)
    new_ld = (
        raw.margin_top100_new_limit_down_n
        if stress_src == "margin"
        else raw.limit_down_new_n
    )
    corr = raw.cross_section_corr_tech
    ad = raw.advance_decline_tech
    resonance = raw.sector_index_resonance
    vol = raw.volume_thrust
    path = raw.intraday_path or ""

    stress_n = (
        int(thr.get("margin_stress_limit_down", 10))
        if stress_src == "margin"
        else int(thr.get("board_stress_consecutive", 30))
    )
    release_high = float(thr.get("margin_release_rate_high", 0.7))
    res_s4 = float(thr.get("resonance_s4_min", 0.65))

    signals: list[str] = []
    # score each stage lightly for confidence / runner-up
    scores = {
        CycleStage.S0.value: 0.1,
        CycleStage.S1.value: 0.1,
        CycleStage.S2.value: 0.1,
        CycleStage.S3.value: 0.1,
        CycleStage.S4.value: 0.05,
    }

    if ld is not None and ld >= stress_n and (release is None or release < 0.5):
        scores[CycleStage.S1.value] += 0.45
        scores[CycleStage.S2.value] += 0.15
        signals.append(
            "margin_top100_stress" if stress_src == "margin" else "board_stress"
        )
    if corr is not None and corr >= float(thr.get("corr_crowded", 0.7)):
        scores[CycleStage.S1.value] += 0.2
        signals.append("high_cross_section_corr")
    if ad is not None and ad < -0.3:
        scores[CycleStage.S1.value] += 0.15
        scores[CycleStage.S2.value] += 0.05

    if release is not None and release >= release_high:
        scores[CycleStage.S2.value] += 0.4
        scores[CycleStage.S3.value] += 0.2
        signals.append("deleveraging_release")
    if new_ld is not None and new_ld <= 5 and (ld or 0) >= 5:
        scores[CycleStage.S2.value] += 0.15
        scores[CycleStage.S3.value] += 0.15
        signals.append("new_limit_down_cooled")
    if (raw.gjd_proxy_score or 0) >= 0.5 or (raw.star_chinext_etf_bid or 0) >= 0.5:
        scores[CycleStage.S2.value] += 0.15
        signals.append("etf_bid_support")
    if path in (IntradayPath.DEEP_V.value, IntradayPath.LATE_BID_SQUEEZE.value):
        scores[CycleStage.S2.value] += 0.1

    # grind: release done, thin volume, weak resonance
    if release is not None and release >= release_high and (vol is None or vol < 0):
        scores[CycleStage.S3.value] += 0.2
        signals.append("thin_post_release")
    if raw.no_mainline_hint:
        scores[CycleStage.S3.value] += 0.25
        signals.append("no_mainline")

    # S4 strict
    if (
        resonance is not None
        and resonance >= res_s4
        and vol is not None
        and vol > 0.05
        and (raw.volume_for_bounce or vol) > 0.05
    ):
        scores[CycleStage.S4.value] += 0.5
        signals.append("resonance_volume")
    else:
        scores[CycleStage.S4.value] *= 0.3

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    stage, best = ordered[0]
    runner, second = ordered[1]
    total = sum(max(v, 0.0) for v in scores.values()) or 1.0
    conf = _clamp(best / total)
    # boundary softener
    if best - second < 0.12:
        conf = min(conf, 0.55)
        signals.append("stage_boundary_soft")

    # rebound type
    if stage == CycleStage.S4.value and conf >= 0.55 and (resonance or 0) >= res_s4:
        rebound = ReboundType.CYCLICAL.value
    elif stage in (CycleStage.S2.value, CycleStage.S3.value, CycleStage.S1.value):
        if (raw.star_chinext_etf_bid or 0) >= 0.45 or path in (
            IntradayPath.DEEP_V.value,
            IntradayPath.GAP_DOWN_RECOVERY.value,
            IntradayPath.LATE_BID_SQUEEZE.value,
            IntradayPath.GAP_UP_FADE.value,
        ):
            rebound = ReboundType.TACTICAL.value
        elif stage == CycleStage.S1.value and (release is None or release < 0.4):
            rebound = ReboundType.NONE.value
        else:
            rebound = ReboundType.TACTICAL.value
    else:
        rebound = ReboundType.NONE.value

    dds = {
        "soxx": raw.drawdown_soxx,
        "kr_semi": raw.drawdown_kr_semi,
        "a_semi": raw.drawdown_a_semi,
    }
    return CycleBlock(
        t0_event="META_HARDWARE_SHOCK",
        t0_date=raw.t0_date,
        drawdowns=dds,
        stage=stage,
        stage_runner_up=runner,
        stage_confidence=round(conf, 4),
        rebound_type=rebound,
        stage_switch_signals=signals,
        days_since_t0=None,
    )


def locate_intervention(raw: SentimentRawInput) -> str:
    if raw.intervention_hint:
        return raw.intervention_hint
    gjd = raw.gjd_proxy_score or 0.0
    star = raw.star_chinext_etf_bid or 0.0
    path = raw.intraday_path or ""
    if gjd >= 0.45 or star >= 0.45:
        # lift if strong recovery path + high bid; else drag
        if path in (IntradayPath.GAP_DOWN_RECOVERY.value,) and max(gjd, star) >= 0.75:
            return InterventionMode.LIFT_ATTEMPT.value
        return InterventionMode.DRAG_NOT_LIFT.value
    if gjd == 0 and star == 0 and raw.gjd_proxy_score is None and raw.star_chinext_etf_bid is None:
        return InterventionMode.UNKNOWN.value
    return InterventionMode.NONE.value


def locate_bounce(raw: SentimentRawInput, transmission_stage: str) -> str:
    res = raw.sector_index_resonance
    vol = raw.volume_thrust
    star = raw.star_chinext_etf_bid or 0.0
    path = raw.intraday_path or ""
    if res is not None and res >= 0.65 and vol is not None and vol > 0.05:
        return BounceQuality.BROAD_CONFIRMED.value
    if star >= 0.55 or transmission_stage in (
        TransmissionStage.HS300_ONLY.value,
        TransmissionStage.TO_CHINEXT.value,
        TransmissionStage.TO_CSI1000.value,
    ):
        if path == IntradayPath.GAP_UP_FADE.value or (vol is not None and vol < 0):
            return BounceQuality.INDEX_LED.value
        return BounceQuality.INDEX_LED.value
    if path in (IntradayPath.DEEP_V.value, IntradayPath.GAP_DOWN_RECOVERY.value):
        return BounceQuality.WEAK_DEAD_CAT.value
    if path == IntradayPath.GAP_UP_FADE.value:
        return BounceQuality.WEAK_DEAD_CAT.value
    return BounceQuality.NONE.value


def locate_rescue(stage: str, transmission_stage: str, bounce: str) -> str:
    if stage == CycleStage.S1.value:
        return RescueSequenceStage.SELLDOWN.value
    if transmission_stage == TransmissionStage.HS300_ONLY.value:
        return RescueSequenceStage.PROTECT_INDEX.value
    if transmission_stage in (
        TransmissionStage.TO_CHINEXT.value,
        TransmissionStage.TO_CSI1000.value,
    ):
        if bounce == BounceQuality.BROAD_CONFIRMED.value:
            return RescueSequenceStage.ROTATE_QUALITY.value
        return RescueSequenceStage.STABILIZE_BROAD.value
    if stage in (CycleStage.S3.value,):
        return RescueSequenceStage.STABILIZE_BROAD.value
    if stage == CycleStage.S4.value:
        return RescueSequenceStage.RELEASE_ELASTIC.value
    return RescueSequenceStage.PROTECT_INDEX.value


def market_regime(
    corr: Optional[float],
    intervention: str,
    ad: Optional[float],
    thr: dict,
) -> str:
    if corr is not None and corr >= float(thr.get("corr_crowded", 0.7)):
        if intervention == InterventionMode.DRAG_NOT_LIFT.value and (ad is None or ad < 0):
            return "INDEX_BID_TECH_PANIC"
        return "CROWDED_UNWIND"
    if intervention == InterventionMode.DRAG_NOT_LIFT.value and (ad is None or ad < 0.1):
        return "INDEX_BID_TECH_PANIC"
    if ad is not None and ad > 0.2:
        return "DIFFERENTIATED_REPAIR"
    return "UNKNOWN"


def temperature(raw: SentimentRawInput, release: Optional[float], intervention: str) -> float:
    t = 50.0
    if raw.volume_thrust is not None:
        t += 12.0 * _clamp(raw.volume_thrust + 0.5) - 6.0
    if raw.advance_decline_tech is not None:
        t += 15.0 * raw.advance_decline_tech
    if raw.limit_stress is not None:
        t -= 20.0 * _clamp(raw.limit_stress)
    if raw.intraday_path == IntradayPath.GAP_UP_FADE.value:
        t -= 8.0
    if raw.intraday_path == IntradayPath.GAP_DOWN_RECOVERY.value:
        t += 6.0
    if release is not None:
        t += 10.0 * (release - 0.5)
    # drag support: small thaw only
    if intervention == InterventionMode.DRAG_NOT_LIFT.value:
        t += 3.0
    if (raw.star_chinext_etf_bid or 0) >= 0.55:
        t += 4.0
    return round(_clamp(t, 0.0, 100.0) if False else max(0.0, min(100.0, t)), 2)


def structure_quality(raw: SentimentRawInput, flags_crowded: bool) -> str:
    disp = raw.theme_dispersion
    vol = raw.volume_thrust
    if flags_crowded or (vol is not None and vol < -0.15):
        return StructureQuality.POOR.value
    if disp is not None and disp > 0.02:
        return StructureQuality.MIXED.value
    if vol is not None and vol > 0.1 and (disp is None or disp < 0.015):
        return StructureQuality.GOOD.value
    return StructureQuality.MIXED.value


def compute_anchor_hold_quality(
    dist_bp: Optional[float], intraday_path: str
) -> Optional[float]:
    """Quality of SSE holding its structural anchor (0~1).

    Higher = anchor comfortably held / reclaimed; lower = breaking below.
    Base = distance-to-anchor (bp) mapped to 0~1 (+200bp->~1.0, 0->0.5,
    -200bp->~0.0); intraday path is a small modifier. Returns None when
    distance is unknown.
    """
    if dist_bp is None:
        return None
    q = 0.5 + dist_bp / 400.0
    if intraday_path in (
        IntradayPath.GAP_DOWN_RECOVERY.value,
        IntradayPath.DEEP_V.value,
        IntradayPath.LATE_BID_SQUEEZE.value,
    ):
        q += 0.1
    elif intraday_path == IntradayPath.GAP_UP_FADE.value:
        q -= 0.1
    return round(_clamp(q), 4)


def compute_crowding_unwind(
    corr: Optional[float],
    stress_index: Optional[float],
    ad: Optional[float],
) -> Optional[float]:
    """Crowded-position unwind intensity (0~1), higher = more crowded unwinding.

    Ideal measure is cross-sectional tech correlation (corr). When corr is
    unavailable (westock does not expose it) fall back to a board-stress +
    breadth proxy: forced-sell stress paired with broad breadth deterioration.
    Returns None when neither corr nor any proxy component exists.
    """
    if corr is not None:
        return round(_clamp(corr), 4)
    if stress_index is None and ad is None:
        return None
    s = 0.6 * (stress_index or 0.0) + 0.4 * _clamp(-(ad or 0.0) * 2.0)
    return round(_clamp(s), 4)


def compute_a_vs_global_rel(
    dd_a: Optional[float], dd_soxx: Optional[float]
) -> Optional[str]:
    """Relative posture of A-share semis vs global (SOXX) by drawdown gap.

    Both drawdowns are <=0. A deeper (more negative) A drawdown => A worse.
    Returns A_LAGGARD / A_RESILIENT / A_SYNC, or None when either missing.
    """
    if dd_a is None or dd_soxx is None:
        return None
    rel = dd_a - dd_soxx
    if rel <= -0.05:
        return "A_LAGGARD"
    if rel >= 0.05:
        return "A_RESILIENT"
    return "A_SYNC"


def compute_global_sync_score(drawdowns: Dict[str, Optional[float]]) -> Optional[float]:
    """0~1 synchronization of available global/A semiconductor drawdowns.

    Higher = drawdowns cluster tightly (synchronized global de-rate); lower =
    dispersed. Needs >=2 available drawdowns, else None.
    """
    vals = [v for v in drawdowns.values() if v is not None]
    if len(vals) < 2:
        return None
    spread = max(vals) - min(vals)
    return round(_clamp(1.0 - spread / 0.30), 4)


def _dim_global(dd_soxx: Optional[float], mega_path: Optional[str]) -> Optional[float]:
    if dd_soxx is None and mega_path is None:
        return None
    g = 100.0 * (1.0 + (dd_soxx or 0.0) * 2.0) if dd_soxx is not None else 50.0
    if mega_path == "RALLY_FADE":
        g -= 10.0
    elif mega_path == "RALLY":
        g += 10.0
    return round(_clamp(g, 0.0, 100.0), 2)


def _dim_liquidity(
    release: Optional[float],
    stress: Optional[float],
    gjd: Optional[float],
    etf: Optional[float],
) -> Optional[float]:
    have = False
    v = 50.0
    if release is not None:
        v += 40.0 * (release - 0.5)
        have = True
    if stress is not None:
        v -= 30.0 * stress
        have = True
    if (gjd or 0) >= 0.5 or (etf or 0) >= 0.5:
        v += 12.0
        have = True
    return round(_clamp(v, 0.0, 100.0), 2) if have else None


def _dim_breadth(
    ad: Optional[float], tx_score: Optional[float], limit_stress: Optional[float]
) -> Optional[float]:
    have = False
    v = 50.0
    if ad is not None:
        v += 60.0 * ad
        have = True
    if limit_stress is not None:
        v -= 40.0 * limit_stress
        have = True
    if tx_score is not None:
        v += 30.0 * (tx_score - 0.5)
        have = True
    return round(_clamp(v, 0.0, 100.0), 2) if have else None


def _dim_momentum(
    bounce: str, resonance: Optional[float], rebound: str
) -> Optional[float]:
    base = {
        BounceQuality.NONE.value: 20.0,
        BounceQuality.WEAK_DEAD_CAT.value: 35.0,
        BounceQuality.INDEX_LED.value: 55.0,
        BounceQuality.BROAD_CONFIRMED.value: 80.0,
    }.get(bounce, 40.0)
    m = base
    if resonance is not None:
        m += 20.0 * resonance
    if rebound == ReboundType.CYCLICAL.value:
        m += 10.0
    return round(_clamp(m, 0.0, 100.0), 2)


def compute_ars(
    dims: Dict[str, Optional[float]], weights: Dict[str, float]
) -> Tuple[Optional[float], float]:
    """Aggregate Regime Score (0~100): weighted mean of available G/L/B/S/M dims.

    Missing dims are dropped from the weighted average; coverage (fraction of
    total weight actually present) is returned so callers can down-weight
    confidence. Returns (None, 0.0) when no dimension is available.
    """
    num = 0.0
    wsum = 0.0
    total_w = sum(weights.values()) or 1.0
    for k, v in dims.items():
        if v is None:
            continue
        w = float(weights.get(k, 0.0))
        num += w * v
        wsum += w
    if wsum <= 0:
        return None, 0.0
    return round(num / wsum, 2), round(wsum / total_w, 4)


class SentimentShadowEngine:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        self.thr = dict(self.config.get("thresholds") or {})
        self.anchor = float(self.config.get("sse_anchor_px", 3750))
        self.buffer = float(self.config.get("sse_anchor_buffer_pct", 0.008))
        self.hints = dict(self.config.get("playbook_hints") or {})

    def compute(self, raw: SentimentRawInput) -> SentimentShadowSnapshot:
        used: list[str] = []
        missing: list[str] = []

        def take(name: str, val: Any) -> Any:
            if val is None:
                missing.append(name)
            else:
                used.append(name)
            return val

        take("sse_last", raw.sse_last)
        take("margin_top100_limit_down_n", raw.margin_top100_limit_down_n)
        take("margin_top100_open_board_n", raw.margin_top100_open_board_n)
        take("limit_down_consecutive_n", raw.limit_down_consecutive_n)
        take("limit_down_open_n", raw.limit_down_open_n)
        take("limit_down_new_n", raw.limit_down_new_n)
        take("limit_up_consecutive_n", raw.limit_up_consecutive_n)
        take("limit_up_open_n", raw.limit_up_open_n)
        take("transmission_hs300", raw.transmission_hs300)

        dist_bp, anchor_state, _ = compute_anchor(raw.sse_last, self.anchor, self.buffer)
        ld_c, open_c, stress_src_c = _resolve_stress_pair(raw)
        release = compute_release_rate(ld_c, open_c)
        new_ld_c = (
            raw.margin_top100_new_limit_down_n
            if stress_src_c == "margin"
            else raw.limit_down_new_n
        )
        stress = compute_deleveraging_stress(
            ld_c,
            new_ld_c,
            release,
            raw.forced_selling_proxy,
            int(
                self.thr.get(
                    "margin_stress_limit_down"
                    if stress_src_c == "margin"
                    else "board_stress_consecutive",
                    10 if stress_src_c == "margin" else 30,
                )
            ),
        )
        tx_stage, tx_score = compute_transmission(
            raw.transmission_hs300, raw.transmission_chinext, raw.transmission_csi1000
        )
        cycle = locate_cycle(raw, self.thr)
        intervention = locate_intervention(raw)
        bounce = locate_bounce(raw, tx_stage)
        rescue = locate_rescue(cycle.stage, tx_stage, bounce)
        regime = market_regime(
            raw.cross_section_corr_tech, intervention, raw.advance_decline_tech, self.thr
        )
        temp = temperature(raw, release, intervention)

        # ── derived composites (fills previously-dangling contract fields) ──
        anchor_hold = compute_anchor_hold_quality(dist_bp, raw.intraday_path or "")
        crowding = compute_crowding_unwind(
            raw.cross_section_corr_tech,
            None if stress is None else stress,
            raw.advance_decline_tech,
        )
        a_vs_global = compute_a_vs_global_rel(raw.drawdown_a_semi, raw.drawdown_soxx)
        global_sync = compute_global_sync_score(
            {
                "soxx": raw.drawdown_soxx,
                "kr_semi": raw.drawdown_kr_semi,
                "a_semi": raw.drawdown_a_semi,
            }
        )
        ars_weights = dict(self.config.get("ars_weights") or {})
        if not ars_weights:
            ars_weights = {
                "global": 0.2,
                "liquidity": 0.25,
                "breadth": 0.25,
                "sentiment": 0.15,
                "momentum": 0.15,
            }
        ars_dims = {
            "global": _dim_global(raw.drawdown_soxx, raw.us_mega_tech_path),
            "liquidity": _dim_liquidity(
                release, stress, raw.gjd_proxy_score, raw.star_chinext_etf_bid
            ),
            "breadth": _dim_breadth(raw.advance_decline_tech, tx_score, raw.limit_stress),
            "sentiment": temp,
            "momentum": _dim_momentum(bounce, raw.sector_index_resonance, cycle.rebound_type),
        }
        ars_val, ars_coverage = compute_ars(ars_dims, ars_weights)

        # quality
        has_stress_probe = (
            (
                raw.margin_top100_limit_down_n is not None
                and raw.margin_top100_open_board_n is not None
            )
            or (
                raw.limit_down_consecutive_n is not None
                and raw.limit_down_open_n is not None
            )
        )
        critical_missing = []
        if "sse_last" in missing:
            critical_missing.append("sse_last")
        if not has_stress_probe:
            critical_missing.append("stress_probe")
        if len(critical_missing) >= 2:
            quality = DataQuality.DEGRADED.value
            conf_adj = 0.35
        elif critical_missing:
            quality = DataQuality.PARTIAL.value
            conf_adj = 0.55
        else:
            quality = DataQuality.OK.value
            conf_adj = 0.85

        path = raw.intraday_path or IntradayPath.UNKNOWN.value
        playbook = "UNKNOWN"
        if path == IntradayPath.GAP_UP_FADE.value:
            playbook = "GAP_UP_FADE_WEAK"
        elif path == IntradayPath.GAP_DOWN_RECOVERY.value:
            playbook = "APR2025_GAP_DOWN_TREND"

        snap = SentimentShadowSnapshot(
            as_of=raw.as_of,
            session=raw.session,
            quality=quality,
            confidence=round(min(conf_adj, cycle.stage_confidence + 0.2), 4),
            ttl_seconds=int(
                self.config.get("default_ttl_seconds_intrasession", 1800)
                if raw.session != "CLOSE"
                else self.config.get("default_ttl_seconds_close", 86400)
            ),
            source=raw.source or SnapshotSource.MOCK.value,
            inputs_used=used,
            missing_inputs=missing,
            cycle=cycle,
            sse_anchor_px=self.anchor,
            sse_last=raw.sse_last,
            sse_anchor_distance_bp=None if dist_bp is None else round(dist_bp, 2),
            sse_anchor_state=anchor_state,
            anchor_hold_quality=anchor_hold,
            market_regime_label=regime,
            cross_section_corr_tech=raw.cross_section_corr_tech,
            crowding_unwind_score=crowding,
            margin_top100_limit_down_n=raw.margin_top100_limit_down_n,
            margin_top100_open_board_n=raw.margin_top100_open_board_n,
            margin_top100_new_limit_down_n=raw.margin_top100_new_limit_down_n,
            limit_down_consecutive_n=raw.limit_down_consecutive_n,
            limit_down_open_n=raw.limit_down_open_n,
            limit_down_new_n=raw.limit_down_new_n,
            limit_up_consecutive_n=raw.limit_up_consecutive_n,
            limit_up_open_n=raw.limit_up_open_n,
            margin_list_as_of=raw.margin_list_as_of,
            deleveraging_stress_index=None if stress is None else round(stress, 4),
            deleveraging_release_rate=None if release is None else round(release, 4),
            forced_selling_proxy=raw.forced_selling_proxy,
            intervention_mode=intervention,
            gjd_proxy_score=raw.gjd_proxy_score,
            etf_premium_proxy=raw.etf_premium_proxy,
            star_chinext_etf_bid=raw.star_chinext_etf_bid,
            csi1000_liquidity_injection=raw.csi1000_liquidity_injection,
            transmission_hs300=raw.transmission_hs300,
            transmission_chinext=raw.transmission_chinext,
            transmission_csi1000=raw.transmission_csi1000,
            liquidity_transmission_score=None if tx_score is None else round(tx_score, 4),
            liquidity_transmission_stage=tx_stage,
            transmission_lag_days=raw.transmission_lag_days,
            volume_thrust=raw.volume_thrust,
            advance_decline_tech=raw.advance_decline_tech,
            limit_stress=raw.limit_stress,
            limit_down_count=raw.limit_down_count,
            intraday_path=path,
            gap_open_ret=raw.gap_open_ret,
            gap_hold_score=raw.gap_hold_score,
            theme_dispersion=raw.theme_dispersion,
            rotation_speed=raw.rotation_speed,
            ipo_catalyst_heat=raw.ipo_catalyst_heat,
            kr_semi_ret=raw.kr_semi_ret,
            us_mega_tech_path=raw.us_mega_tech_path,
            kr_a_divergence=raw.kr_a_divergence,
            global_semi_drawdowns={
                "soxx": raw.drawdown_soxx,
                "kr_semi": raw.drawdown_kr_semi,
                "a_semi": raw.drawdown_a_semi,
            },
            global_sync_score=global_sync,
            a_vs_global_rel=a_vs_global,
            growth_etf_flow=raw.growth_etf_flow,
            liquidity_pulse=raw.liquidity_pulse,
            hk_repair_relative=raw.hk_repair_relative,
            sentiment_temperature=temp,
            structure_quality=structure_quality(
                raw, regime in ("CROWDED_UNWIND", "INDEX_BID_TECH_PANIC")
            ),
            bounce_quality=bounce,
            rescue_sequence_stage=rescue,
            rescue_playbook_match=playbook,
            sector_index_resonance=raw.sector_index_resonance,
            ars=ars_val,
        )

        snap.corroboration_flags = build_flags(snap, self.thr)
        snap.narrative_bullets = build_narratives(snap)
        snap.watch_checklist = build_checklist(snap, self.hints)
        # confidence blend
        snap.confidence = round(
            _clamp(0.5 * conf_adj + 0.5 * cycle.stage_confidence), 4
        )
        return snap
