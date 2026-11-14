# -*- coding: utf-8 -*-
"""
trinity_levels.py — Phase 0/1：指数/ETF Trinity 关键位 + 执行滤网系数
================================================================
Phase 0：对 QQQ/SPY/SOXX（及可选主题 ETF）输出结构关键位摘要（不改仓）。
Phase 1：在分数闸 OFF 时，用结构分对「卫星仓」打 0.55~1.0 执行系数；
         BEAR/WARN 由分数闸绝对优先，Trinity 不得加仓对抗。

依赖：macro-os/trinity/structure_parser.py + yfinance OHLC
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
MOS = HERE / "macro-os"
if str(MOS) not in sys.path:
    sys.path.insert(0, str(MOS))

from trinity.structure_parser import StructureParser, Pivot  # noqa: E402

# 执行滤网系数（仅作用于卫星，且仅分数闸 OFF）
MULT_HIGH = 1.00   # 结构完整 + 回抽确认
MULT_MID = 0.85    # 中性
MULT_LOW = 0.60    # 混沌/逆结构
MULT_FLOOR = 0.55


@dataclass
class TrinityLevelSnapshot:
    symbol: str
    as_of: str
    close: float
    ma55: float
    bb_mid: float
    dist_ma55_pct: float
    dist_bb_mid_pct: float
    structure_type: str
    structure_score: float
    d_complete: bool
    d_score: float
    near_d3: bool
    pullback_confirm: bool
    against_structure: bool
    tier: str
    last_pivots: list
    note: str
    exec_mult: float  # 1.0 = 满配卫星

    def to_dict(self) -> dict:
        return asdict(self)


def _ohlc(symbol: str, start: str, end: str) -> pd.DataFrame:
    raw = yf.download(symbol, start=start, end=end, auto_adjust=True, progress=False, threads=False)
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"no data {symbol}")
    if isinstance(raw.columns, pd.MultiIndex):
        df = pd.DataFrame({
            "Open": raw["Open"].iloc[:, 0] if isinstance(raw["Open"], pd.DataFrame) else raw["Open"],
            "High": raw["High"].iloc[:, 0] if isinstance(raw["High"], pd.DataFrame) else raw["High"],
            "Low": raw["Low"].iloc[:, 0] if isinstance(raw["Low"], pd.DataFrame) else raw["Low"],
            "Close": raw["Close"].iloc[:, 0] if isinstance(raw["Close"], pd.DataFrame) else raw["Close"],
        })
    else:
        df = raw[["Open", "High", "Low", "Close"]].copy()
    df = df.dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def _bb_mid(close: pd.Series, n: int = 20) -> pd.Series:
    return close.rolling(n).mean()


def analyze_symbol(symbol: str, end: Optional[str] = None, lookback_days: int = 420,
                   threshold: float = 0.03) -> TrinityLevelSnapshot:
    end_d = end or datetime.now().strftime("%Y-%m-%d")
    start_d = (pd.Timestamp(end_d) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    df = _ohlc(symbol, start_d, end_d)
    if len(df) < 60:
        raise RuntimeError(f"{symbol} bars too few: {len(df)}")

    close = df["Close"]
    high = df["High"].tolist()
    low = df["Low"].tolist()
    px = float(close.iloc[-1])
    ma55 = float(close.rolling(55).mean().iloc[-1])
    bb = _bb_mid(close, 20)
    bb_mid = float(bb.iloc[-1]) if pd.notna(bb.iloc[-1]) else px
    dist_ma = (px / ma55 - 1.0) if ma55 > 0 else 0.0
    dist_bb = (px / bb_mid - 1.0) if bb_mid > 0 else 0.0

    parser = StructureParser(threshold=threshold)
    pivots = parser.find_pivots(high, low)
    structure = parser.classify(pivots)
    d_ok, d_score, d_ev = parser.evaluate_d_structure(pivots)

    # 结构分：classify 的 completeness 与 d_score 混合
    st_score = float(getattr(structure, "completeness", 0.0) or 0.0)
    if st_score <= 0 and hasattr(structure, "score"):
        st_score = float(structure.score or 0.0)
    # fallback
    if st_score <= 0:
        st_score = float(d_score)

    st_type = str(getattr(structure, "structure_type", None) or getattr(structure, "type", None) or "UNKNOWN")
    if hasattr(st_type, "value"):
        st_type = st_type.value
    st_type = str(st_type)

    tier = "LOW"
    if st_score >= 0.8 or d_score >= 0.85:
        tier = "HIGH"
    elif st_score >= 0.55 or d_score >= 0.7:
        tier = "MEDIUM"

    # 近 D3：最近 pivot 为低点且距今不太远，或价格靠近最近低点 pivot
    near_d3 = False
    last_piv = []
    for p in pivots[-6:]:
        last_piv.append({"i": p.index, "px": round(p.price, 2), "h": p.is_high})
    if len(pivots) >= 3:
        last = pivots[-1]
        bars_since = len(df) - 1 - last.index
        if (not last.is_high) and bars_since <= 12:
            near_d3 = True
        if last.is_high and len(pivots) >= 2 and (not pivots[-2].is_high):
            # 刚离开低点回抽
            if abs(px / pivots[-2].price - 1.0) <= 0.03:
                near_d3 = True

    # 回抽确认：价格在 MA55 或 BB 中轨附近（±2.5%）且未破
    pullback = (abs(dist_ma) <= 0.025 and px >= ma55 * 0.985) or (
        abs(dist_bb) <= 0.02 and px >= bb_mid * 0.99
    )

    # 逆结构：近段 pivot 显示下行破位且价格在 MA55 下较深
    against = False
    if len(pivots) >= 4 and pivots[-1].is_high is False:
        # 新低
        prev_lows = [p.price for p in pivots[:-1] if not p.is_high]
        if prev_lows and pivots[-1].price < min(prev_lows[-2:] if len(prev_lows) >= 2 else prev_lows):
            against = True
    if dist_ma < -0.04 and d_score < 0.5:
        against = True

    # 执行系数（更可分：延伸过热打折，破位/逆结构深折，回抽确认才满配）
    if against or tier == "LOW" or st_score < 0.45:
        mult = MULT_LOW
        note = "结构偏混沌/逆结构 → 卫星打折"
    elif dist_ma < -0.03:
        mult = MULT_LOW
        note = "价格显著低于MA55 → 卫星打折"
    elif dist_ma > 0.06 and not pullback:
        mult = 0.70
        note = "相对MA55延伸偏大且未回抽 → 卫星七折"
    elif dist_ma > 0.04 and not pullback:
        mult = MULT_MID
        note = "轻度延伸、未回抽 → 卫星中等"
    elif tier == "HIGH" and pullback and (near_d3 or d_ok):
        mult = MULT_HIGH
        note = "结构完整 + 回抽/近D3 → 卫星可满配"
    elif tier == "HIGH" and pullback:
        mult = 0.95
        note = "结构完整 + 回抽 → 卫星接近满配"
    elif tier == "HIGH":
        mult = MULT_MID
        note = "结构完整但未回抽确认 → 卫星中等"
    elif pullback and st_score >= 0.55:
        mult = 0.90
        note = "中性结构 + 回抽 → 卫星九折"
    else:
        mult = MULT_MID
        note = "中性结构"

    mult = float(max(MULT_FLOOR, min(1.0, mult)))

    return TrinityLevelSnapshot(
        symbol=symbol,
        as_of=str(df.index[-1].date()),
        close=round(px, 4),
        ma55=round(ma55, 4),
        bb_mid=round(bb_mid, 4),
        dist_ma55_pct=round(dist_ma * 100, 2),
        dist_bb_mid_pct=round(dist_bb * 100, 2),
        structure_type=st_type,
        structure_score=round(float(st_score), 3),
        d_complete=bool(d_ok),
        d_score=round(float(d_score), 3),
        near_d3=bool(near_d3),
        pullback_confirm=bool(pullback),
        against_structure=bool(against),
        tier=tier,
        last_pivots=last_piv,
        note=note,
        exec_mult=mult,
    )


def analyze_many(symbols: list[str], end: Optional[str] = None) -> dict[str, TrinityLevelSnapshot]:
    out = {}
    for s in symbols:
        try:
            out[s] = analyze_symbol(s, end=end)
        except Exception as e:
            out[s] = None  # type: ignore
            print(f"  ! trinity {s}: {e}", file=sys.stderr)
    return out


def index_exec_mult(snapshots: dict, prefer: str = "QQQ") -> tuple[float, str]:
    """科技侧执行系数：优先 QQQ，其次 SOXX。"""
    for key in (prefer, "SOXX", "SPY"):
        snap = snapshots.get(key)
        if snap is not None:
            return snap.exec_mult, f"{key}:{snap.tier}/{snap.exec_mult:.2f} ({snap.note})"
    return 1.0, "无 Trinity 快照→系数1.0"


def format_levels_md(snapshots: dict, title: str = "## 指数 Trinity 关键位") -> str:
    lines = [
        f"{title}\n",
        "> Phase 0 只读：StructureParser pivots + D 结构 + MA55/布林中轨。"
        "Phase 1 执行系数仅在分数闸 **OFF** 时折扣卫星；**BEAR/WARN 分数闸优先**。\n",
        "| 标的 | 收盘 | 结构 | 结构分 | D完整/分 | 近D3 | 回抽 | MA55距 | BB中轨距 | 层级 | 执行系数 | 说明 |",
        "|---|---:|---|---:|---|---|---|---:|---:|---|---:|---|",
    ]
    for sym in ("QQQ", "SPY", "SOXX"):
        s = snapshots.get(sym)
        if s is None:
            lines.append(f"| {sym} | — | — | — | — | — | — | — | — | — | — | 无数据 |")
            continue
        lines.append(
            f"| {s.symbol} | {s.close:.2f} | {s.structure_type} | {s.structure_score:.2f} | "
            f"{'Y' if s.d_complete else 'N'}/{s.d_score:.2f} | "
            f"{'Y' if s.near_d3 else 'N'} | {'Y' if s.pullback_confirm else 'N'} | "
            f"{s.dist_ma55_pct:+.2f}% | {s.dist_bb_mid_pct:+.2f}% | {s.tier} | "
            f"**{s.exec_mult:.2f}** | {s.note} |"
        )
    # optional others
    for sym, s in snapshots.items():
        if sym in ("QQQ", "SPY", "SOXX") or s is None:
            continue
        lines.append(
            f"| {s.symbol} | {s.close:.2f} | {s.structure_type} | {s.structure_score:.2f} | "
            f"{'Y' if s.d_complete else 'N'}/{s.d_score:.2f} | "
            f"{'Y' if s.near_d3 else 'N'} | {'Y' if s.pullback_confirm else 'N'} | "
            f"{s.dist_ma55_pct:+.2f}% | {s.dist_bb_mid_pct:+.2f}% | {s.tier} | "
            f"{s.exec_mult:.2f} | {s.note} |"
        )
    lines.append("")
    # human summary
    q = snapshots.get("QQQ")
    s = snapshots.get("SPY")
    if q:
        lines.append(
            f"- **QQQ 读法**：现价 {q.close:.2f}，相对 MA55 {q.dist_ma55_pct:+.2f}%、"
            f"布林中轨 {q.dist_bb_mid_pct:+.2f}%；结构 {q.structure_type}/分 {q.structure_score:.2f}，"
            f"{'近D3/拐点区' if q.near_d3 else '非近D3'}，"
            f"{'回抽确认' if q.pullback_confirm else '未回抽确认'}，"
            f"{'逆结构风险' if q.against_structure else '未判逆结构'} → 执行系数 **{q.exec_mult:.2f}**。"
        )
    if s:
        lines.append(
            f"- **SPY 读法**：现价 {s.close:.2f}，MA55 {s.dist_ma55_pct:+.2f}%；"
            f"结构 {s.structure_type}/分 {s.structure_score:.2f} → 系数 **{s.exec_mult:.2f}**。"
        )
    lines.append("")
    return "\n".join(lines)


def apply_sat_mult(weights: list[dict], mult: float, gate_state: str,
                   core_tickers: set[str] | None = None) -> tuple[list[dict], str]:
    """Phase1：仅 OFF 时对非核心、非BIL 的卫星权重乘 mult，剩余进 BIL。"""
    core_tickers = core_tickers or {"QQQ", "SPY"}
    if gate_state != "OFF" or abs(mult - 1.0) < 1e-9:
        return weights, f"滤网未改仓（gate={gate_state}, mult={mult:.2f}）"

    w = {x["ticker"]: float(x["pct"]) / 100.0 for x in weights}
    names = {x["ticker"]: x.get("name", x["ticker"]) for x in weights}
    sat_keys = [k for k in w if k not in core_tickers and k != "BIL"]
    sat_sum = sum(w[k] for k in sat_keys)
    if sat_sum <= 1e-12:
        return weights, "无卫星可调"

    new_sat = sat_sum * mult
    released = sat_sum - new_sat
    for k in sat_keys:
        w[k] = w[k] / sat_sum * new_sat
    w["BIL"] = w.get("BIL", 0.0) + released
    # renormalize tiny drift
    s = sum(w.values())
    w = {k: v / s for k, v in w.items() if v > 1e-8}
    rows = [{"ticker": k, "name": names.get(k, k), "pct": round(v * 100, 1)} for k, v in w.items()]
    # sort: core first, then sat, bil last
    def key(r):
        t = r["ticker"]
        if t in core_tickers:
            return (0, t)
        if t == "BIL":
            return (2, t)
        return (1, t)
    rows.sort(key=key)
    return rows, f"OFF+Trinity 卫星×{mult:.2f}，释放 {released*100:.1f}%→BIL"


# --------- 向量化回测用：逐日结构系数（简化，避免每日全量 classify 过慢可抽样月末） ---------
def daily_exec_mult_series(symbol: str, start: str, end: str, threshold: float = 0.03) -> pd.Series:
    """生成每日 exec_mult（用滚动窗口末值）。偏慢但可接受 ~几年日线。"""
    df = _ohlc(symbol, start, end)
    parser = StructureParser(threshold=threshold)
    close = df["Close"]
    ma55 = close.rolling(55).mean()
    bb = close.rolling(20).mean()
    mults = []
    # 为速度：每 5 个交易日重算一次结构，ffill
    recalc_every = 5
    last_mult = 1.0
    highs = df["High"].values
    lows = df["Low"].values
    for i in range(len(df)):
        if i < 60:
            mults.append(np.nan)
            continue
        if (i - 60) % recalc_every != 0 and i != len(df) - 1:
            mults.append(last_mult)
            continue
        px = float(close.iloc[i])
        m55 = float(ma55.iloc[i]) if pd.notna(ma55.iloc[i]) else px
        bm = float(bb.iloc[i]) if pd.notna(bb.iloc[i]) else px
        dist_ma = px / m55 - 1.0 if m55 else 0.0
        dist_bb = px / bm - 1.0 if bm else 0.0
        # 只用到 i 的历史
        piv = parser.find_pivots(list(highs[: i + 1]), list(lows[: i + 1]))
        d_ok, d_score, _ = parser.evaluate_d_structure(piv)
        st = parser.classify(piv)
        st_score = float(getattr(st, "completeness", 0.0) or 0.0) or float(d_score)
        tier = "LOW"
        if st_score >= 0.8 or d_score >= 0.85:
            tier = "HIGH"
        elif st_score >= 0.55 or d_score >= 0.7:
            tier = "MEDIUM"
        near = False
        if piv:
            last = piv[-1]
            if (not last.is_high) and (i - last.index) <= 12:
                near = True
        pullback = (abs(dist_ma) <= 0.025 and px >= m55 * 0.985) or (
            abs(dist_bb) <= 0.02 and px >= bm * 0.99
        )
        against = (dist_ma < -0.03) or (dist_ma < -0.04 and d_score < 0.5)
        if against or tier == "LOW" or st_score < 0.45:
            mult = MULT_LOW
        elif dist_ma > 0.06 and not pullback:
            mult = 0.70
        elif dist_ma > 0.04 and not pullback:
            mult = MULT_MID
        elif tier == "HIGH" and pullback and (near or d_ok):
            mult = MULT_HIGH
        elif tier == "HIGH" and pullback:
            mult = 0.95
        elif tier == "HIGH":
            mult = MULT_MID
        elif pullback and st_score >= 0.55:
            mult = 0.90
        else:
            mult = MULT_MID
        last_mult = float(max(MULT_FLOOR, min(1.0, mult)))
        mults.append(last_mult)
    s = pd.Series(mults, index=df.index)
    return s.ffill().fillna(1.0)
