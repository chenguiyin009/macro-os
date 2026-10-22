# -*- coding: utf-8 -*-
"""Testable calibration rules. Suggestions only — never mutate scores / C / pine / min."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 1D direction threshold: below this |ret| counts as "chop" for 观望
CHOP_1D = 0.003  # 0.3%
# Strong move that makes 观望 look like a miss
WAIT_MISS_1D = 0.008  # 0.8%

BULLISH = {"risk_on", "risk_on_observe", "lean_long", "long", "buy", "participate", "offense"}
BEARISH = {"risk_off", "defend", "lean_short", "short", "sell", "defense", "de_risk"}
NEUTRAL = {"wait", "observe", "mixed", "none", "neutral", "hold", "repair"}


@dataclass
class HitResult:
    name: str
    hit: Optional[bool]  # None = abstain / insufficient data
    detail: str
    suggestion: Optional[str] = None

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


def direction_hit(
    posture: Optional[str],
    ret_1d: Optional[float],
    *,
    bias: Optional[str] = None,
    bench: str = "SPY",
    chop: float = CHOP_1D,
    wait_miss: float = WAIT_MISS_1D,
) -> HitResult:
    """预判偏多/偏空/观望 vs 实际涨跌."""
    name = f"四腿方向({bench})"
    if ret_1d is None:
        return HitResult(name, None, "缺 1D 收益，跳过", None)
    side = map_posture(posture, bias)
    pct = ret_1d * 100
    if side == "bullish":
        hit = ret_1d > 0
        detail = f"预判偏多 · {bench} 1D {pct:+.2f}%"
        sug = None if hit else "偏多日实际下跌：复核 binding/分母天花板是否应压过观察层偏多表述"
    elif side == "bearish":
        hit = ret_1d < 0
        detail = f"预判偏空/防守 · {bench} 1D {pct:+.2f}%"
        sug = None if hit else "防守姿态但指数上涨：检查是否过早 defend，或仅分母纪律误伤"
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
    return HitResult(name, hit, detail, sug)


def relative_strength_hit(
    label: str,
    strong_rets: Sequence[Optional[float]],
    weak_rets: Sequence[Optional[float]],
    bench_ret: Optional[float],
) -> HitResult:
    """主题相对强弱：强者应跑赢基准，弱者应跑输."""
    name = f"主题相对强弱({label})"
    if bench_ret is None:
        return HitResult(name, None, "缺基准 1D，跳过", None)
    strong_ok = [r > bench_ret for r in strong_rets if r is not None]
    weak_ok = [r < bench_ret for r in weak_rets if r is not None]
    if not strong_ok and not weak_ok:
        return HitResult(name, None, "强弱代理无可用收益", None)
    hits = sum(strong_ok) + sum(weak_ok)
    total = len(strong_ok) + len(weak_ok)
    hit = hits >= max(1, (total + 1) // 2)  # majority
    detail = (
        f"强者跑赢 {sum(strong_ok)}/{len(strong_ok) or 0} · "
        f"弱者跑输 {sum(weak_ok)}/{len(weak_ok) or 0} · "
        f"基准1D {bench_ret*100:+.2f}%"
    )
    sug = None
    if not hit:
        sug = f"{label}：Top/Bottom 相对强弱与次日不符，复核排序引擎或拥挤惩罚是否过拟合短窗"
    return HitResult(name, hit, detail, sug)


def semi_a_share_gate_hit(
    force_downgrade: bool,
    stance: Optional[str],
    smh_rel1: Optional[float],
    smh_rel5: Optional[float],
) -> HitResult:
    """对A半导降预期是否与 SMH 相对 QQQ 强弱一致."""
    name = "半导对A门控 vs SMH相对"
    downgrade = bool(force_downgrade) or (
        stance is not None and ("降" in stance or "谨慎" in stance)
    )
    if smh_rel1 is None and smh_rel5 is None:
        return HitResult(name, None, "缺 SMH 相对收益", None)

    def _neg(x: Optional[float]) -> bool:
        return x is not None and x < 0

    def _pos(x: Optional[float]) -> bool:
        return x is not None and x >= 0

    rel_note = (
        f"SMH-QQQ rel1={_fmt(smh_rel1)} rel5={_fmt(smh_rel5)} · "
        f"门控={'降预期' if downgrade else '未降级'}"
    )
    if downgrade:
        # 一致：至少一窗相对偏弱，或两窗都不强
        consistent = _neg(smh_rel1) or _neg(smh_rel5)
        if smh_rel1 is not None and smh_rel5 is not None and smh_rel1 > 0.005 and smh_rel5 > 0.01:
            consistent = False
        detail = f"{rel_note} → {'一致' if consistent else '不一致（美股半导相对并不弱）'}"
        sug = None if consistent else "降预期日 SMH 相对并不弱：检查 score/rel5 阈值是否过敏感"
        return HitResult(name, consistent, detail, sug)
    # 未降级：期望相对不弱
    consistent = _pos(smh_rel1) or (smh_rel1 is None and _pos(smh_rel5))
    if smh_rel1 is not None and smh_rel1 < -0.005 and (smh_rel5 is None or smh_rel5 < 0):
        consistent = False
    detail = f"{rel_note} → {'一致' if consistent else '不一致（相对偏弱却未降级）'}"
    sug = None if consistent else "未降级但 SMH 相对偏弱：复核 a_share_mapping 触发是否漏判"
    return HitResult(name, consistent, detail, sug)


def _fmt(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{x*100:+.2f}%"


def pine_top3_hit(
    top3_rets: Sequence[Tuple[str, Optional[float]]],
    bench_ret: Optional[float],
) -> HitResult:
    """标普 pine Top3 次日相对 SPY."""
    name = "标普pine Top3相对"
    if bench_ret is None:
        return HitResult(name, None, "缺 SPY 1D", None)
    pairs = [(t, r) for t, r in top3_rets if r is not None]
    if not pairs:
        return HitResult(name, None, "Top3 无可用 1D", None)
    beats = [(t, r) for t, r in pairs if r > bench_ret]
    hit = len(beats) >= (len(pairs) + 1) // 2
    detail = (
        f"跑赢SPY {len(beats)}/{len(pairs)} · "
        + " · ".join(f"{t} {_fmt(r)}" for t, r in pairs)
        + f" · SPY {_fmt(bench_ret)}"
    )
    sug = None if hit else "pine Top3 次日多数跑输：建议仅作笔记，不改生产 C/pine 默认"
    return HitResult(name, hit, detail, sug)


def a_share_column_hit(
    posture: Optional[str],
    semi_stance: Optional[str],
    force_downgrade: bool,
    cyb_ret: Optional[float],
    hs300_ret: Optional[float],
    *,
    bias: Optional[str] = None,
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
        return HitResult(name, None, "缺次日 A 股收益，跳过", None)

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
        return HitResult(name, hit, detail + (" → 一致" if hit else " → 不一致"), sug)

    if side == "bullish" and not downgrade:
        hit = bench >= -WAIT_MISS_1D
        sug = None
        if not hit:
            sug = "对A偏多但次日A偏弱：复核对A映射是否应更谨慎（对A列专用建议）"
        return HitResult(name, hit, detail + (" → 一致" if hit else " → 不一致"), sug)

    # 中性
    if abs(bench) <= WAIT_MISS_1D:
        return HitResult(name, True, detail + " → 震荡一致", None)
    return HitResult(
        name,
        False,
        detail + " → 中性日波动偏大",
        "对A中性日出现明确方向：考虑在对A结构标签中更早标注（不改美股校准）",
    )

