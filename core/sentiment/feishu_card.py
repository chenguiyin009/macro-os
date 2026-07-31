"""Feishu observation card renderer for sentiment shadow (markdown)."""

from __future__ import annotations

from typing import Optional, Tuple

from core.sentiment.models import SentimentShadowSnapshot


def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    if abs(x) <= 1.5:
        return f"{x:.0%}"
    return f"{x:.1f}%"


def _fmt_num(x: Optional[float], nd: int = 2) -> str:
    if x is None:
        return "n/a"
    return f"{x:.{nd}f}"


def _delta_line(curr: SentimentShadowSnapshot, prev: SentimentShadowSnapshot) -> str:
    parts = []
    if prev.cycle.stage != curr.cycle.stage:
        parts.append(f"stage {prev.cycle.stage}→{curr.cycle.stage}")
    else:
        parts.append(f"stage {curr.cycle.stage}")

    def pair(label, a, b, as_pct: bool = False):
        if a is None and b is None:
            return
        arrow = ""
        if a is not None and b is not None and a != b:
            try:
                arrow = " ↑" if b > a else " ↓"
            except TypeError:
                arrow = ""
        if as_pct:
            fa = _fmt_pct(a) if a is not None else "n/a"
            fb = _fmt_pct(b) if b is not None else "n/a"
        else:
            fa = "n/a" if a is None else str(a)
            fb = "n/a" if b is None else str(b)
        parts.append(f"{label} {fa}→{fb}{arrow}")

    pair("开板", prev.margin_top100_open_board_n, curr.margin_top100_open_board_n)
    pair(
        "释放率",
        prev.deleveraging_release_rate,
        curr.deleveraging_release_rate,
        as_pct=True,
    )
    pair(
        "新增跌停",
        prev.margin_top100_new_limit_down_n,
        curr.margin_top100_new_limit_down_n,
    )
    return "**Δ 上一日** " + " | ".join(parts)


def render_observation_card(
    snap: SentimentShadowSnapshot,
    previous: Optional[SentimentShadowSnapshot] = None,
) -> Tuple[str, str]:
    """Return (title, lark_md body)."""
    title = f"🔭 Macro OS 观测 | CN Tech Cycle | {snap.session} | {snap.as_of}"
    dd = snap.cycle.drawdowns or snap.global_semi_drawdowns or {}
    flags = " ".join(f"`{f}`" for f in snap.corroboration_flags) or "`(none)`"
    bullets = "\n".join(f"- {b}" for b in snap.narrative_bullets) or "- （无）"
    checks = "\n".join(f"{i}. {c}" for i, c in enumerate(snap.watch_checklist, 1))

    runner = (
        f", runner-up {snap.cycle.stage_runner_up}"
        if snap.cycle.stage_runner_up
        else ""
    )
    lines = [
        (
            f"**周期** t0={snap.cycle.t0_date or 'TBD'} | "
            f"阶段 **{snap.cycle.stage}** ({snap.cycle.stage_confidence:.0%}{runner}) | "
            f"反弹性质 **{snap.cycle.rebound_type}**"
        ),
        (
            f"**全球回撤** SOXX {_fmt_pct(dd.get('soxx'))} / "
            f"KR {_fmt_pct(dd.get('kr_semi'))} / A半 {_fmt_pct(dd.get('a_semi'))}"
        ),
        (
            f"**锚点** 上证 {_fmt_num(snap.sse_last, 2)} vs {snap.sse_anchor_px:g} "
            f"({_fmt_num(snap.sse_anchor_distance_bp, 1)} bp) | {snap.sse_anchor_state}"
        ),
        f"**体制** {snap.market_regime_label} | 干预 **{snap.intervention_mode}**",
        (
            f"**温度** {snap.sentiment_temperature:.0f}/100 | 结构 {snap.structure_quality} "
            f"| 救援阶梯 {snap.rescue_sequence_stage}"
        ),
        (
            f"**去杠杆** Top100跌停 {snap.margin_top100_limit_down_n} | "
            f"开板 {snap.margin_top100_open_board_n} | "
            f"新增 {snap.margin_top100_new_limit_down_n} | "
            f"释放率 {_fmt_pct(snap.deleveraging_release_rate)}"
        ),
        (
            f"**传导** {snap.liquidity_transmission_stage} "
            f"(score={_fmt_num(snap.liquidity_transmission_score)}) | "
            f"lag_days={_fmt_num(snap.transmission_lag_days, 1)}"
        ),
        (
            f"**路径** {snap.intraday_path} | 反弹质量 {snap.bounce_quality} | "
            f"共振 {_fmt_num(snap.sector_index_resonance)}"
        ),
        "",
        f"**Flags:** {flags}",
    ]
    if previous is not None:
        lines.extend(["", _delta_line(snap, previous)])
    lines.extend(
        [
            "",
            "**读法**",
            bullets,
            "",
            "**观察清单（不构成交易指令）**",
            checks,
            "",
            "**边界:** Shadow only · 不修改 Kernel · 不改变 risk_budget",
            (
                f"**Source:** {snap.source} | Quality: {snap.quality} | "
                f"Confidence: {snap.confidence:.2f}"
            ),
        ]
    )
    return title, "\n".join(lines)
