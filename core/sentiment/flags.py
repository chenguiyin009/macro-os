"""Flag builders and narrative helpers."""

from __future__ import annotations

from typing import List

from core.sentiment import types as T
from core.sentiment.models import SentimentShadowSnapshot


def build_flags(snap: SentimentShadowSnapshot, thr: dict) -> List[str]:
    flags: List[str] = []
    stage = snap.cycle.stage
    if stage and stage != "UNKNOWN":
        flags.append(f"{T.FLAG_CYCLE_PREFIX}{stage}")

    if snap.intervention_mode == T.InterventionMode.DRAG_NOT_LIFT.value:
        flags.append(T.FLAG_DRAG_NOT_LIFT)

    ld = snap.margin_top100_limit_down_n
    if ld is not None and ld >= int(thr.get("margin_stress_limit_down", 10)):
        flags.append(T.FLAG_MARGIN_TOP100_STRESS)
    else:
        bld = snap.limit_down_consecutive_n
        if bld is not None and bld >= int(thr.get("board_stress_consecutive", 30)):
            flags.append(T.FLAG_BOARD_STRESS)

    rr = snap.deleveraging_release_rate
    if rr is not None and rr >= float(thr.get("margin_release_rate_high", 0.7)):
        flag = (
            T.FLAG_MARGIN_PRESSURE_RELEASE
            if snap.margin_top100_limit_down_n is not None
            else T.FLAG_BOARD_RELEASE
        )
        flags.append(flag)

    ts = snap.liquidity_transmission_stage
    if ts == T.TransmissionStage.HS300_ONLY.value:
        flags.append(T.FLAG_TRANSMIT_HS300)
    elif ts == T.TransmissionStage.TO_CHINEXT.value:
        flags.extend([T.FLAG_TRANSMIT_HS300, T.FLAG_TRANSMIT_CHINEXT])
    elif ts == T.TransmissionStage.TO_CSI1000.value:
        flags.extend(
            [T.FLAG_TRANSMIT_HS300, T.FLAG_TRANSMIT_CHINEXT, T.FLAG_TRANSMIT_CSI1000]
        )

    if snap.csi1000_liquidity_injection is not None and snap.csi1000_liquidity_injection < 0.35:
        if ts in (
            T.TransmissionStage.TO_CHINEXT.value,
            T.TransmissionStage.HS300_ONLY.value,
            T.TransmissionStage.UNKNOWN.value,
        ):
            flags.append(T.FLAG_CSI1000_NEED_LIQUIDITY)

    if snap.cycle.rebound_type == T.ReboundType.TACTICAL.value:
        flags.append(T.FLAG_REBOUND_TACTICAL)
        flags.append(T.FLAG_BOUNCE_NOT_REVERSAL)
        flags.append(T.FLAG_RIGHT_SIDE_WAIT)
    elif snap.cycle.rebound_type == T.ReboundType.CYCLICAL.value:
        flags.append(T.FLAG_REBOUND_CYCLICAL_CANDIDATE)

    if snap.intraday_path == T.IntradayPath.GAP_UP_FADE.value:
        flags.append(T.FLAG_GAP_UP_FADE)

    if (snap.star_chinext_etf_bid or 0) >= 0.55 or (
        snap.intraday_path == T.IntradayPath.LATE_BID_SQUEEZE.value
    ):
        flags.append(T.FLAG_LATE_BROAD_ETF_BID)

    if snap.market_regime_label == "INDEX_BID_TECH_PANIC":
        flags.append(T.FLAG_INDEX_BID_TECH_PANIC)

    if snap.market_regime_label == "CROWDED_UNWIND" or (
        snap.cross_section_corr_tech is not None
        and snap.cross_section_corr_tech >= float(thr.get("corr_crowded", 0.7))
    ):
        flags.append(T.FLAG_CROWDED_UNWIND)

    if snap.sse_anchor_state == T.AnchorState.IN_ZONE.value:
        flags.append(T.FLAG_SSE_ANCHOR_TEST)
    elif snap.sse_anchor_state == T.AnchorState.BREAK_TEST.value:
        flags.append(T.FLAG_SSE_ANCHOR_BREAK)

    if snap.rescue_sequence_stage in (
        T.RescueSequenceStage.SELLDOWN.value,
        T.RescueSequenceStage.PROTECT_INDEX.value,
        T.RescueSequenceStage.STABILIZE_BROAD.value,
    ):
        flags.append(T.FLAG_WAIT_BROAD_BETA)

    if snap.us_mega_tech_path == "RALLY_FADE":
        flags.append(T.FLAG_US_RALLY_FADE)

    if snap.kr_a_divergence in ("KR_WEAK_A_WEAKER", "KR_STRONG_A_WEAK"):
        flags.append(T.FLAG_KR_DRAG)

    if snap.cycle.stage == T.CycleStage.S3.value:
        flags.append(T.FLAG_NO_MAINLINE)

    seen = set()
    out: List[str] = []
    for f in flags:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def build_narratives(snap: SentimentShadowSnapshot) -> List[str]:
    bullets: List[str] = []
    st = snap.cycle.stage
    rt = snap.cycle.rebound_type
    conf = snap.cycle.stage_confidence
    bullets.append(f"周期阶段 {st}（置信 {conf:.0%}），反弹性质 {rt}。")
    if rt == T.ReboundType.TACTICAL.value:
        bullets.append("当前默认交易性反弹：反弹不等于反转，右侧未确认前不加弹性仓。")
    if snap.intervention_mode == T.InterventionMode.DRAG_NOT_LIFT.value:
        bullets.append("干预模式偏托而不举：托住流动性优先，不视为当日 V 反。")
    if snap.deleveraging_release_rate is not None:
        if snap.margin_top100_limit_down_n is not None:
            label = "融资重仓释放率"
            ld_disp = snap.margin_top100_limit_down_n
            open_disp = snap.margin_top100_open_board_n
            new_disp = snap.margin_top100_new_limit_down_n
        else:
            label = "连板跌停释放率(通达信替代)"
            ld_disp = snap.limit_down_consecutive_n
            open_disp = snap.limit_down_open_n
            new_disp = snap.limit_down_new_n
        bullets.append(
            f"{label} {snap.deleveraging_release_rate:.0%} "
            f"(跌停 {ld_disp}/开板 {open_disp}/新增 {new_disp})。"
        )
    if snap.liquidity_transmission_stage not in ("UNKNOWN", None, ""):
        lag = snap.transmission_lag_days
        lag_s = f"，传导滞后约 {lag} 日" if lag is not None else ""
        bullets.append(f"流动性传导阶段 {snap.liquidity_transmission_stage}{lag_s}。")
    if snap.intraday_path == T.IntradayPath.GAP_UP_FADE.value:
        bullets.append("日内路径高开泄气，资金对反弹追价意愿偏弱。")
    return bullets[:6]


def build_checklist(snap: SentimentShadowSnapshot, hints: dict) -> List[str]:
    h300 = float(hints.get("hs300_offset", -0.005))
    hcy = float(hints.get("chinext_offset", -0.01))
    h1k = float(hints.get("csi1000_offset", -0.02))
    return [
        "融资 Top100 开板是否持续、新增跌停是否维持低位（人类观察，非自动交易）",
        "宽基 ETF 托底是连续行为还是一日尾盘",
        (
            "传导是否稳定到中证1000；人类纪律档位叙事："
            f"300 {h300:.1%} / 创业 {hcy:.1%} / 1000 {h1k:.1%}（系统不下单）"
        ),
        "有无带头板块与指数共振（右侧确认）",
        "外盘波动与科技财报/CapEx 是否仍压制内部科技",
        f"上证相对锚点 {snap.sse_anchor_px:g} 的守住质量",
    ]
