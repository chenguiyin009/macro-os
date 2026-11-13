# -*- coding: utf-8 -*-
"""Testable calibration rules. Suggestions only — never mutate scores / C / pine / min."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 1D direction threshold: below this |ret| counts as "chop" for 观望
CHOP_1D = 0.003  # 0.3%
# Strong move that makes 观望 look like a miss
WAIT_MISS_1D = 0.008  # 0.8%

# Official tech / semi-outcome window. 1D is 观察 only and never counts as 命中/落空.
OFFICIAL_RS_HORIZON = 5
# 5-session SMH-QQQ move large enough to call the economic stance inconsistent.
# Chosen from the 10-08..11-05 window: median |SMH-QQQ 5D| ≈ 3.1%, so 2%
# filters single-day noise without treating every modest drift as a miss.
SEMI_OUTCOME_CLEAR_5D = 0.02

# Mirrors tech-rotation production state labels (detection only; do not change production).
STRONG_STATES = frozenset({"确认进入", "拥挤主升", "早期轮动"})
WEAK_STATES = frozenset({"资金撤出", "派发/撤出"})

# Production a_share_mapping_from_selected constants (detect mis-application only).
A_SHARE_AVG_LAG = 15.0
REL5_JUST_NEG_FLOOR = -2.0  # percent units, same as tech rel5_pct

BULLISH = {"risk_on", "risk_on_observe", "lean_long", "long", "buy", "participate", "offense"}
BEARISH = {"risk_off", "defend", "lean_short", "short", "sell", "defense", "de_risk"}
NEUTRAL = {"wait", "observe", "mixed", "none", "neutral", "hold", "repair"}


@dataclass
class HitResult:
    name: str
    hit: Optional[bool]  # None = abstain / 观察 / insufficient data
    detail: str
    suggestion: Optional[str] = None
    status: Optional[str] = None  # official | observe | abstain

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def map_posture(posture: Optional[str], bias: Optional[str] = None) -> str:
    """Return 'bullish' | 'bearish' | 'neutral'."""
    for raw in (posture, bias):
        if not raw:
            continue
        key = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
        if key in BULLISH or key.startswith("risk_on"):
            return "bullish"
        if key in BEARISH or key.startswith("risk_off"):
            return "bearish"
        if key in NEUTRAL:
            return "neutral"
    return "neutral"


def _binding_has_nq(binding: Optional[Sequence[str]]) -> bool:
    if not binding:
        return False
    return any("NQ" in str(b).upper() for b in binding)


def direction_hit(
    posture: Optional[str],
    ret_1d: Optional[float],
    *,
    bias: Optional[str] = None,
    bench: str = "SPY",
    chop: float = CHOP_1D,
    wait_miss: float = WAIT_MISS_1D,
    binding: Optional[Sequence[str]] = None,
    denom_ceiling: Optional[float] = None,
) -> HitResult:
    """预判偏多/偏空/观望 vs 实际涨跌."""
    name = f"四腿方向({bench})"
    if ret_1d is None:
        return HitResult(name, None, "缺 1D 收益，跳过", None, status="abstain")
    side = map_posture(posture, bias)
    pct = ret_1d * 100
    if side == "bullish":
        hit = ret_1d > 0
        detail = f"预判偏多 · {bench} 1D {pct:+.2f}%"
        sug = None if hit else "偏多日实际下跌：复核 binding/分母天花板是否应压过观察层偏多表述"
    elif side == "bearish":
        hit = ret_1d < 0
        detail = f"预判偏空/防守 · {bench} 1D {pct:+.2f}%"
        if hit:
            sug = None
        elif _binding_has_nq(binding) and (
            denom_ceiling is not None and float(denom_ceiling) >= 0.8
        ):
            # 分母本身宽松，真正收紧的是 NQ 硬否决 → 勿写成「分母纪律」
            sug = "防守姿态但指数上涨：检查是否过早 defend，或 NQ硬否决近阈误伤"
        else:
            sug = "防守姿态但指数上涨：检查是否过早 defend，或仅分母纪律误伤"
    else:
        # 观望：窄幅震荡算命中；大波动算落空（本可更鲜明）
        if abs(ret_1d) <= wait_miss:
            hit = True
            detail = f"预判观望 · {bench} 1D {pct:+.2f}%（|Δ|≤{wait_miss*100:.1f}% 视为震荡一致）"
            sug = None
        else:
            hit = False
            detail = f"预判观望 · {bench} 1D {pct:+.2f}%（波动偏大）"
            sug = "观望日出现明确方向：考虑在 synthesis.bias 与 plain_advice 对齐时更早表态"
    return HitResult(name, hit, detail, sug, status="official")


def select_themes_by_state(
    themes: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Strong/weak by *state*, not ETF-only score rank.

    Strong = 确认进入 / 拥挤主升 / 早期轮动.
    Weak = 资金撤出 / 派发/撤出.
    中性观察 and 分数够、动能不足 are skipped (not mid-pack fallback).
    """
    strong: List[Dict[str, Any]] = []
    weak: List[Dict[str, Any]] = []
    for t in themes or []:
        st = str(t.get("state") or "").strip()
        if st in STRONG_STATES:
            strong.append(dict(t))
        elif st in WEAK_STATES:
            weak.append(dict(t))
    strong.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    weak.sort(key=lambda x: float(x.get("score") or 0))
    return strong, weak


def relative_strength_hit(
    label: str,
    strong_rets: Sequence[Optional[float]],
    weak_rets: Sequence[Optional[float]],
    bench_ret: Optional[float],
    *,
    horizon: int = OFFICIAL_RS_HORIZON,
    official: Optional[bool] = None,
    strong_names: Optional[Sequence[str]] = None,
    weak_names: Optional[Sequence[str]] = None,
) -> HitResult:
    """明确强弱主题相对基准。官方窗口为 5 个交易日；1D 只记观察，不计命中/落空。"""
    name = f"主题相对强弱({label})"
    is_official = official if official is not None else int(horizon) >= OFFICIAL_RS_HORIZON
    window = f"{horizon}D"
    strong_ok = [r > bench_ret for r in strong_rets if r is not None] if bench_ret is not None else []
    weak_ok = [r < bench_ret for r in weak_rets if r is not None] if bench_ret is not None else []
    n_strong = len(strong_ok)
    n_weak = len(weak_ok)

    def _names(prefix: str, names: Optional[Sequence[str]]) -> str:
        if not names:
            return ""
        return prefix + "、".join(str(n) for n in names if n)

    named = (_names("强:", strong_names) + _names(" 弱:", weak_names)).strip()
    named_bit = f" · {named}" if named else ""

    if bench_ret is None:
        return HitResult(
            name,
            None,
            f"缺基准 {window}，{'观察' if not is_official else '跳过'}{named_bit}",
            None,
            status="observe" if not is_official else "abstain",
        )
    if not strong_ok and not weak_ok:
        return HitResult(
            name,
            None,
            f"无明确强弱主题的可测收益（已排除中性观察；篮子缺成员则跳过该主题）{named_bit}",
            None,
            status="abstain",
        )
    hits = sum(strong_ok) + sum(weak_ok)
    total = n_strong + n_weak
    majority = hits >= max(1, (total + 1) // 2)
    detail = (
        f"强者跑赢 {sum(strong_ok)}/{n_strong} · "
        f"弱者跑输 {sum(weak_ok)}/{n_weak} · "
        f"基准{window} {bench_ret*100:+.2f}%{named_bit}"
    )
    if not is_official:
        return HitResult(
            name,
            None,
            f"观察（{window}，官方窗口为{OFFICIAL_RS_HORIZON}D，不计命中/落空）· {detail}",
            None,
            status="observe",
        )
    sug = None
    if not majority:
        sug = (
            f"{label}：明确强弱主题 {OFFICIAL_RS_HORIZON} 日相对基准不符（官方）；"
            "复核主题状态与篮子成员收益，不把中性观察当次日必须跑赢"
        )
    return HitResult(name, majority, f"官方{window} · {detail}", sug, status="official")


def expected_semi_mapping(
    selected_score: Optional[float],
    avg_score: Optional[float],
    selected_rel5_pct: Optional[float],
    selected_state: Optional[str] = None,
    selected_state_code: Optional[int] = None,
) -> Dict[str, Any]:
    """Recompute production a_share_mapping decision from as_of inputs.

    Mirrors tech-rotation a_share_mapping_from_selected. Detection only —
    this must not be used to change the production gate.
    selected_rel5_pct is in percent units (same as tech JSON rel5_pct).
    """
    score_lag = (
        selected_score is not None
        and avg_score is not None
        and selected_score <= (avg_score - A_SHARE_AVG_LAG)
    )
    rel5_neg = selected_rel5_pct is not None and selected_rel5_pct < 0
    rel5_just_neg = rel5_neg and selected_rel5_pct is not None and selected_rel5_pct > REL5_JUST_NEG_FLOOR
    force_downgrade = bool(score_lag or rel5_neg)
    state_is_confirm = selected_state_code == 2 or (
        selected_state is not None and str(selected_state).strip() == "确认进入"
    )
    semi_bullish_ok = (
        bool(state_is_confirm)
        and selected_rel5_pct is not None
        and selected_rel5_pct >= 0
    )
    if force_downgrade:
        stance = "降预期/谨慎"
    elif semi_bullish_ok:
        stance = "可偏多（确认进入且 rel5>=0）"
    else:
        stance = "中性对照（未达偏多门槛）"
    triggers: List[str] = []
    if score_lag:
        triggers.append(f"score<=avg-{A_SHARE_AVG_LAG:.0f}")
    if rel5_neg:
        triggers.append("rel5<0")
    return {
        "force_semi_downgrade": force_downgrade,
        "semi_chip_stance": stance,
        "semi_bullish_ok": bool(semi_bullish_ok),
        "triggers": triggers,
        "score_lag": bool(score_lag),
        "rel5_neg": bool(rel5_neg),
        "shadow_weak_downgrade": bool(force_downgrade and (not score_lag) and rel5_just_neg),
    }


def _stance_kind(stance: Optional[str], force_downgrade: bool) -> str:
    if force_downgrade or (stance is not None and ("降" in str(stance) or "谨慎" in str(stance))):
        return "downgrade"
    if stance is not None and "偏多" in str(stance):
        return "bullish"
    return "neutral"


def semi_a_share_gate_integrity_hit(
    force_downgrade: bool,
    stance: Optional[str],
    *,
    selected_score: Optional[float] = None,
    avg_score: Optional[float] = None,
    selected_rel5_pct: Optional[float] = None,
    selected_state: Optional[str] = None,
    selected_state_code: Optional[int] = None,
    triggers: Optional[Sequence[str]] = None,
    inputs_missing: bool = False,
) -> HitResult:
    """Same-morning integrity: was force_semi_downgrade applied per production rule text?

    Rule text: 落后均分≥15 或 rel5<0 → 降级；半导偏多须确认进入且 rel5>=0.
    Does not judge next-day SMH-QQQ and does not change the production gate.
    """
    name = "半导对A门控完整性"
    if inputs_missing or (
        selected_score is None and avg_score is None and selected_rel5_pct is None
    ):
        return HitResult(name, None, "缺所选主题分数/均分/rel5，无法核对完整性", None, status="abstain")
    expected = expected_semi_mapping(
        selected_score,
        avg_score,
        selected_rel5_pct,
        selected_state=selected_state,
        selected_state_code=selected_state_code,
    )
    exp_force = bool(expected["force_semi_downgrade"])
    exp_kind = _stance_kind(expected["semi_chip_stance"], exp_force)
    got_kind = _stance_kind(stance, bool(force_downgrade))
    force_ok = bool(force_downgrade) == exp_force
    stance_ok = got_kind == exp_kind
    trig = list(triggers or [])
    exp_trig = list(expected["triggers"])
    # Trigger set is informational; force/stance mismatch is the official miss.
    detail = (
        f"输入 score={_fmt_num(selected_score)} avg={_fmt_num(avg_score)} "
        f"rel5={_fmt_pct_points(selected_rel5_pct)} state={selected_state or '—'} · "
        f"映射 force={bool(force_downgrade)} stance={stance or '—'} 触发={'+'.join(trig) or '无'} · "
        f"规则期望 force={exp_force} stance={expected['semi_chip_stance']} "
        f"触发={'+'.join(exp_trig) or '无'}"
    )
    if force_ok and stance_ok:
        return HitResult(name, True, detail + " → 与规则一致", None, status="official")
    if bool(force_downgrade) and not exp_force:
        kind = "误触发"
        sug = "半导对A门控完整性：输入未达降级条件但映射已降级（误触发）；不改生产硬门控，只记规则误用"
    elif (not bool(force_downgrade)) and exp_force:
        kind = "漏判"
        sug = "半导对A门控完整性：输入已达落后均分≥15或rel5<0但映射未降级（漏判）；不改生产硬门控，只记规则误用"
    else:
        kind = "口径不符"
        sug = "半导对A门控完整性：force 与姿态口径和规则期望不一致；不改生产硬门控，只记规则误用"
    return HitResult(name, False, detail + f" → {kind}", sug, status="official")


def semi_a_share_gate_outcome_hit(
    force_downgrade: bool,
    stance: Optional[str],
    smh_rel5: Optional[float],
    *,
    smh_rel1: Optional[float] = None,
    ready_5d: bool = False,
    clear_threshold: float = SEMI_OUTCOME_CLEAR_5D,
) -> HitResult:
    """Economic stance vs 5-session SMH-QQQ. Official only when 5D is ready.

    Miss only if the 5D relative move is clearly opposite the stance
    (|rel5| >= clear_threshold). 1D rel1 is never counted as 漏判.
    """
    name = "半导对A门控结果(5D)"
    downgrade = bool(force_downgrade) or (
        stance is not None and ("降" in str(stance) or "谨慎" in str(stance))
    )
    stance_txt = "降预期" if downgrade else "未降级"
    obs_1d = f"1D rel1={_fmt(smh_rel1)}（观察，不计）"
    if not ready_5d or smh_rel5 is None:
        return HitResult(
            name,
            None,
            f"观察 · 门控={stance_txt} · 5D尚未就绪 · {obs_1d}",
            None,
            status="observe",
        )
    rel5 = float(smh_rel5)
    thr = float(clear_threshold)
    if downgrade and rel5 >= thr:
        hit = False
        why = f"不一致（降预期但 SMH-QQQ 5D 明确偏强 {rel5*100:+.2f}%≥{thr*100:.0f}%）"
        sug = (
            f"半导对A门控 5 日结果：降预期后 SMH 相对明确偏强（|{thr*100:.0f}%| 阈值）；"
            "属跟踪指标滞后，不把次日 1D 当成漏判，也不改生产硬门控"
        )
    elif (not downgrade) and rel5 <= -thr:
        hit = False
        why = f"不一致（未降级但 SMH-QQQ 5D 明确偏弱 {rel5*100:+.2f}%≤-{thr*100:.0f}%）"
        sug = (
            f"半导对A门控 5 日结果：未降级但 SMH 相对明确偏弱（|{thr*100:.0f}%| 阈值）；"
            "属跟踪指标滞后，不把次日 1D 当成漏判，也不改生产硬门控"
        )
    else:
        hit = True
        why = f"一致（5D {rel5*100:+.2f}% 未达到与姿态相反的明确阈值 ±{thr*100:.0f}%）"
        sug = None
    detail = f"官方5D · 门控={stance_txt} · SMH-QQQ rel5={_fmt(rel5)} · {obs_1d} → {why}"
    return HitResult(name, hit, detail, sug, status="official")


# Backward-compatible name used by older tests: do not use for official scoring.
# Kept as a thin wrapper around the outcome 观察/官方 split so leftover imports
# do not silently revive the 1D 漏判 question.
def semi_a_share_gate_hit(
    force_downgrade: bool,
    stance: Optional[str],
    smh_rel1: Optional[float],
    smh_rel5: Optional[float],
    *,
    triggers: Optional[Sequence[str]] = None,
    shadow_weak_downgrade: bool = False,
    pred_rel5_pct: Optional[float] = None,
    ready_5d: Optional[bool] = None,
    selected_score: Optional[float] = None,
    avg_score: Optional[float] = None,
    selected_state: Optional[str] = None,
) -> HitResult:
    """Deprecated wrapper. Official path is integrity + 5D outcome."""
    del triggers, shadow_weak_downgrade, pred_rel5_pct, selected_score, avg_score, selected_state
    ready = bool(ready_5d) if ready_5d is not None else smh_rel5 is not None
    return semi_a_share_gate_outcome_hit(
        force_downgrade,
        stance,
        smh_rel5,
        smh_rel1=smh_rel1,
        ready_5d=ready,
    )


def _fmt(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{x*100:+.2f}%"


def _fmt_num(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{x:.2f}"


def _fmt_pct_points(x: Optional[float]) -> str:
    """Format a value already stored in percent points (tech rel5_pct)."""
    if x is None:
        return "—"
    return f"{x:+.2f}%"


def pine_top3_hit(
    top3_rets: Sequence[Tuple[str, Optional[float]]],
    bench_ret: Optional[float],
) -> HitResult:
    """标普 pine Top3 次日相对 SPY（笔记·不驱动C）。"""
    name = "标普 pine Top3（笔记·不驱动C）"
    if bench_ret is None:
        return HitResult(name, None, "缺 SPY 1D", None, status="abstain")
    pairs = [(t, r) for t, r in top3_rets if r is not None]
    if not pairs:
        return HitResult(name, None, "Top3 无可用 1D", None, status="abstain")
    beats = [(t, r) for t, r in pairs if r > bench_ret]
    hit = len(beats) >= (len(pairs) + 1) // 2
    detail = (
        f"跑赢SPY {len(beats)}/{len(pairs)} · "
        + " · ".join(f"{t} {_fmt(r)}" for t, r in pairs)
        + f" · SPY {_fmt(bench_ret)}"
    )
    # 落空建议仅笔记，永不暗示改生产 C / pine 默认
    sug = (
        None
        if hit
        else "标普 pine Top3（笔记·不驱动C）次日多数跑输：仅作笔记，不改生产 C/pine 默认"
    )
    return HitResult(name, hit, detail, sug, status="official")


def a_share_column_hit(
    posture: Optional[str],
    semi_stance: Optional[str],
    force_downgrade: bool,
    cyb_ret: Optional[float],
    hs300_ret: Optional[float],
    *,
    bias: Optional[str] = None,
    skip_reason: Optional[str] = None,
) -> HitResult:
    """对A列校准：前日报对A姿态 vs 下一A股交易日宽基/成长。

    与「美股 defend 偏早」分开；本规则只评 A/港相关实际，不写美股建议。
    """
    name = "对A列(姿态vs次日A)"
    side = map_posture(posture, bias)
    downgrade = bool(force_downgrade) or (
        semi_stance is not None and ("降" in str(semi_stance) or "谨慎" in str(semi_stance))
    )
    if cyb_ret is None and hs300_ret is None:
        reason = skip_reason or "缺次日 A 股收益"
        return HitResult(name, None, f"{reason}（对A列跳过）", None, status="abstain")

    # 综合：优先沪深300，辅以创业板
    bench = hs300_ret if hs300_ret is not None else cyb_ret
    assert bench is not None
    detail_bits = []
    if hs300_ret is not None:
        detail_bits.append(f"沪深300 {_fmt(hs300_ret)}")
    if cyb_ret is not None:
        detail_bits.append(f"创业板 {_fmt(cyb_ret)}")
    stance_txt = f"四腿={posture or '—'}·半导={semi_stance or '—'}·降预期={downgrade}"
    detail = f"{stance_txt} → 次日 " + " / ".join(detail_bits)

    # 降预期或防守：期望次日 A 不强（<=0 或窄幅）
    if downgrade or side == "bearish":
        hit = bench <= WAIT_MISS_1D  # 未大涨即视为方向不矛盾
        sug = None
        if not hit:
            sug = (
                "对A降预期/防守但次日A偏强：检查是否过早降预期（对A列）；"
                "勿与「美股defend偏早」混写"
            )
        return HitResult(name, hit, detail + (" → 一致" if hit else " → 不一致"), sug, status="official")

    if side == "bullish" and not downgrade:
        hit = bench >= -WAIT_MISS_1D
        sug = None
        if not hit:
            sug = "对A偏多但次日A偏弱：复核对A映射是否应更谨慎（对A列专用建议）"
        return HitResult(name, hit, detail + (" → 一致" if hit else " → 不一致"), sug, status="official")

    # 中性
    if abs(bench) <= WAIT_MISS_1D:
        return HitResult(name, True, detail + " → 震荡一致", None, status="official")
    return HitResult(
        name,
        False,
        detail + " → 中性日波动偏大",
        "对A中性日出现明确方向：考虑在对A结构标签中更早标注（不改美股校准）",
        status="official",
    )
