# -*- coding: utf-8 -*-
"""
trinity_stock_sleeve.py — Phase 2：个股 Trinity 卫星
====================================================
从 industry_pack 的 trinity_eligible / candidates 拉取美股标的，
用 trinity_levels 结构滤网筛选，硬约束：

  个股合计权重 ≤ min(STOCK_CAP, 当日 Book C 卫星预算)

规则：
  - 仅科技账本（tech A/C/B）；标普默认不做个股袖
  - 分数闸 BEAR：个股权重 = 0（闸绝对优先）
  - WARN：个股预算随 C 卫星减半后的额度，且 ≤ STOCK_CAP_WARN
  - OFF：个股预算 ≤ min(STOCK_CAP_OFF, C 卫星目标 × 结构系数后的可用卫星)
  - 个股权重从「ETF 卫星」中按比例挤出，不碰核心 QQQ/SPY
  - Trinity 不得加仓对抗 BEAR；不得抬升总权益超过闸后预算

不自动下单。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
DESK = HERE / "trading-desk"
PACK_DIR = DESK / "data" / "industry_packs"

# 硬顶：相对整本权益的比例
STOCK_CAP_OFF = 0.15   # 总权益 ≤15%
STOCK_CAP_WARN = 0.10  # WARN 更紧
STOCK_CAP_BEAR = 0.0
MAX_NAMES = 3
MIN_EXEC_MULT = 0.85   # 结构过弱不入选
DEFAULT_PACKS = ("tech_semi",)

# 回测默认美股池（pack 过瘦时兜底；仍需结构过线）
FALLBACK_US_SYMBOLS = [
    ("NVDA", "英伟达"),
    ("AVGO", "博通"),
    ("AMD", "超威"),
    ("MSFT", "微软"),
    ("META", "Meta"),
    ("GOOGL", "谷歌"),
    ("AMZN", "亚马逊"),
    ("TSM", "台积电"),
    ("AAPL", "苹果"),
]


def _is_us_ticker(sym: str) -> bool:
    s = (sym or "").strip().upper()
    if not s:
        return False
    # 排除 A 股/港股等：带点后缀
    if "." in s:
        return False
    if re.fullmatch(r"[A-Z]{1,5}", s):
        return True
    return False


def load_pack_candidates(pack_ids: list[str] | tuple[str, ...] | None = None) -> list[dict]:
    """合并 industry pack candidates + trinity_eligible leaders（仅美股）。"""
    pack_ids = list(pack_ids or DEFAULT_PACKS)
    out: list[dict] = []
    seen: set[str] = set()

    desk_packs: dict = {}
    desk_path = DESK / "desk_data.json"
    if desk_path.exists():
        try:
            desk = json.loads(desk_path.read_text(encoding="utf-8"))
            raw = desk.get("industryPacks") or {}
            if isinstance(raw, dict):
                desk_packs = raw
        except Exception:
            desk_packs = {}

    for pid in pack_ids:
        blobs: list[dict] = []
        p = desk_packs.get(pid) if desk_packs else None
        if isinstance(p, dict) and not p.get("missing"):
            blobs.append(p)
        fp = PACK_DIR / f"{pid}.json"
        if fp.exists():
            try:
                blobs.append(json.loads(fp.read_text(encoding="utf-8")))
            except Exception:
                pass
        if not blobs:
            continue
        # 用最后一个完整 pack（文件优先已在后）
        pack = blobs[-1]
        stance = str(pack.get("stance") or "neutral").lower()
        if stance in {"no_touch"}:
            continue

        cands = list(pack.get("candidates") or [])
        for L in pack.get("leaders") or []:
            if not isinstance(L, dict):
                continue
            if L.get("trinity_eligible"):
                cands.append(
                    {
                        "symbol": L.get("symbol"),
                        "name": L.get("name"),
                        "reason": "leader trinity_eligible",
                        "priority": 50,
                        "source": "leader",
                        "trinity_eligible": True,
                    }
                )
            # 非 eligible 的 etf 跳过
        for c in cands:
            if not isinstance(c, dict):
                continue
            sym = str(c.get("symbol") or "").strip().upper()
            if not sym or sym in seen:
                continue
            if not _is_us_ticker(sym):
                continue
            # ETF 角色跳过
            role = str(c.get("role") or "").lower()
            if role == "etf" or sym in {"SMH", "SOXX", "QQQ", "SPY", "XLK", "IGV", "BOTZ", "AIQ", "CIBR", "SKYY"}:
                continue
            seen.add(sym)
            out.append(
                {
                    "symbol": sym,
                    "name": c.get("name") or sym,
                    "reason": c.get("reason") or "",
                    "priority": int(c.get("priority") or 999),
                    "source": c.get("source") or "pack",
                    "pack_id": pid,
                    "trinity_eligible": bool(c.get("trinity_eligible", True)),
                }
            )

    if not out:
        # pack 无可用美股时兜底
        for i, (sym, name) in enumerate(FALLBACK_US_SYMBOLS):
            out.append(
                {
                    "symbol": sym,
                    "name": name,
                    "reason": "fallback_pool",
                    "priority": i + 1,
                    "source": "fallback",
                    "pack_id": "fallback",
                    "trinity_eligible": True,
                }
            )
    out.sort(key=lambda x: int(x.get("priority") or 999))
    return out


def stock_budget_cap(gate_state: str, c_sat_available: float) -> float:
    """个股硬顶 = min(档位顶, 当日 C 卫星可用)。"""
    st = (gate_state or "OFF").upper()
    if st == "BEAR":
        return 0.0
    cap = STOCK_CAP_WARN if st == "WARN" else STOCK_CAP_OFF
    return float(max(0.0, min(cap, max(0.0, c_sat_available))))


def c_sat_available_pct(gate_state: str, trinity_mult: float = 1.0) -> float:
    """Book C 在闸+Phase1 后的卫星总预算（比例 0~1）。"""
    st = (gate_state or "OFF").upper()
    core, sat = 0.70, 0.30
    if st == "BEAR":
        return 0.0
    if st == "WARN":
        return sat * 0.50  # WARN_SAT_FRAC
    # OFF：Phase1 可打折
    m = float(trinity_mult) if trinity_mult else 1.0
    m = min(1.0, max(0.0, m))
    return sat * m


def screen_stocks(
    candidates: list[dict],
    end: Optional[str] = None,
    max_names: int = MAX_NAMES,
    min_mult: float = MIN_EXEC_MULT,
) -> list[dict]:
    """结构筛：逆结构/低 tier 剔除；按 exec_mult、structure_score 排序取 TopN。"""
    try:
        from trinity_levels import analyze_many
    except Exception as e:
        return [{"error": f"trinity_levels不可用: {e}"}]

    syms = [c["symbol"] for c in candidates]
    meta = {c["symbol"]: c for c in candidates}
    snaps = analyze_many(syms, end=end)
    scored: list[dict] = []
    for sym, snap in snaps.items():
        if snap is None:
            continue
        d = snap.to_dict() if hasattr(snap, "to_dict") else dict(snap)
        if d.get("against_structure"):
            continue
        mult = float(d.get("exec_mult") or 0.0)
        if mult < min_mult:
            continue
        tier = str(d.get("tier") or "")
        if tier == "LOW":
            continue
        base = meta.get(sym, {"symbol": sym, "name": sym})
        scored.append(
            {
                **base,
                "exec_mult": mult,
                "structure_score": float(d.get("structure_score") or 0.0),
                "structure_type": d.get("structure_type"),
                "tier": tier,
                "pullback_confirm": bool(d.get("pullback_confirm")),
                "note": d.get("note") or "",
                "close": d.get("close"),
            }
        )
    scored.sort(
        key=lambda x: (
            float(x.get("exec_mult") or 0),
            float(x.get("structure_score") or 0),
            -int(x.get("priority") or 999),
        ),
        reverse=True,
    )
    return scored[:max_names]


def allocate_equal(names: list[dict], budget: float) -> list[dict]:
    """等权拆预算 → weight rows pct。"""
    if budget <= 1e-12 or not names:
        return []
    n = len(names)
    each = budget / n
    rows = []
    for x in names:
        rows.append(
            {
                "ticker": x["symbol"],
                "name": x.get("name") or x["symbol"],
                "pct": round(each * 100, 1),
                "kind": "stock",
                "exec_mult": x.get("exec_mult"),
                "tier": x.get("tier"),
                "note": x.get("note") or "",
            }
        )
    return rows


def apply_stock_sleeve(
    weights: list[dict],
    stock_rows: list[dict],
    budget: float,
    core_tickers: set[str] | None = None,
) -> tuple[list[dict], str]:
    """
    从 ETF 卫星挤出 budget 给个股；核心不动；不足则缩小个股。
    weights: [{ticker,name,pct}, ...]
    stock_rows: allocate_equal 结果（pct 已按 budget）
    """
    core_tickers = core_tickers or {"QQQ", "SPY"}
    if budget <= 1e-12 or not stock_rows:
        return weights, "个股袖未启用（预算0或无标的）"

    w = {x["ticker"]: float(x["pct"]) / 100.0 for x in weights}
    names = {x["ticker"]: x.get("name", x["ticker"]) for x in weights}
    kinds = {}
    for x in weights:
        tkr = x["ticker"]
        if tkr in core_tickers:
            kinds[tkr] = "core"
        elif tkr == "BIL":
            kinds[tkr] = "cash"
        else:
            kinds[tkr] = x.get("kind") or "etf"

    sat_keys = [k for k in w if k not in core_tickers and k != "BIL"]
    sat_sum = sum(w[k] for k in sat_keys)
    if sat_sum <= 1e-12:
        return weights, "无ETF卫星可挤出给个股"

    use = min(budget, sat_sum)
    # 缩放个股至 use
    raw = {r["ticker"]: float(r["pct"]) / 100.0 for r in stock_rows}
    rs = sum(raw.values()) or 1.0
    raw = {k: v / rs * use for k, v in raw.items()}
    for r in stock_rows:
        names[r["ticker"]] = r.get("name") or r["ticker"]
        kinds[r["ticker"]] = "stock"

    # ETF 卫星按比例缩到 sat_sum - use
    remain_etf = sat_sum - use
    scale = remain_etf / sat_sum if sat_sum > 0 else 0.0
    for k in sat_keys:
        w[k] = w[k] * scale
    for k, v in raw.items():
        w[k] = w.get(k, 0.0) + v

    s = sum(w.values())
    if s > 0:
        w = {k: v / s for k, v in w.items() if v > 1e-8}

    def sort_key(t):
        if t in core_tickers:
            return (0, t)
        if kinds.get(t) == "stock":
            return (1, t)
        if t == "BIL":
            return (3, t)
        return (2, t)

    rows = []
    for k, v in sorted(w.items(), key=lambda kv: sort_key(kv[0])):
        if k in core_tickers:
            kind = "core"
        elif k == "BIL":
            kind = "cash"
        else:
            kind = kinds.get(k, "etf")
        rows.append({"ticker": k, "name": names.get(k, k), "pct": round(v * 100, 1), "kind": kind})
    # 审计：个股合计
    stock_sum = sum(float(r["pct"]) for r in rows if r.get("kind") == "stock")
    note = f"Phase2个股袖 {stock_sum:.1f}%（硬顶预算{use*100:.1f}%）←挤出ETF卫星"
    return rows, note


def build_phase2_bundle(
    gate_state: str,
    trinity_mult: float = 1.0,
    pack_ids: list[str] | None = None,
    end: Optional[str] = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """
    返回：
      enabled, budget, candidates_all, selected, stock_rows, md, note
    """
    st = (gate_state or "OFF").upper()
    c_sat = c_sat_available_pct(st, trinity_mult if st == "OFF" else 1.0)
    # WARN 时 C 卫星已是半仓，不再乘 trinity（闸优先）
    budget = stock_budget_cap(st, c_sat)

    base = {
        "enabled": bool(enabled),
        "gate_state": st,
        "c_sat_available": round(c_sat, 4),
        "budget": round(budget, 4),
        "cap_off": STOCK_CAP_OFF,
        "cap_warn": STOCK_CAP_WARN,
        "candidates_all": [],
        "selected": [],
        "stock_rows": [],
        "md": "",
        "note": "",
    }
    if not enabled:
        base["note"] = "Phase2关闭"
        base["md"] = "### Phase2 个股卫星\n\n> 关闭\n"
        return base
    if budget <= 1e-12:
        base["note"] = f"预算0（gate={st}）"
        base["md"] = (
            f"### Phase2 个股卫星\n\n"
            f"> 闸 **{st}** → 个股预算 **0**（BEAR/无卫星时不做个股；分数闸优先）\n"
        )
        return base

    cands = load_pack_candidates(pack_ids)
    base["candidates_all"] = [
        {"symbol": c["symbol"], "name": c.get("name"), "pack_id": c.get("pack_id"), "reason": c.get("reason")}
        for c in cands
    ]
    selected = screen_stocks(cands, end=end)
    if selected and "error" in selected[0]:
        base["note"] = selected[0]["error"]
        base["md"] = f"### Phase2 个股卫星\n\n> 失败：{selected[0]['error']}\n"
        return base
    if not selected:
        base["note"] = "候选均未过结构线"
        base["md"] = (
            f"### Phase2 个股卫星\n\n"
            f"> 预算上限 **{budget*100:.1f}%**（≤C卫星 {c_sat*100:.1f}% 且 ≤档位顶）；"
            f"候选 {len(cands)} 只均未过结构/逆结构线 → **空仓个股**\n"
        )
        return base

    rows = allocate_equal(selected, budget)
    base["selected"] = selected
    base["stock_rows"] = rows
    base["note"] = (
        f"选中 {','.join(r['ticker'] for r in rows)} 合计 {budget*100:.1f}%"
    )
    lines = [
        "### Phase2 个股卫星\n",
        f"> 硬约束：个股合计 ≤ **min(档位顶, C卫星预算)** = **{budget*100:.1f}%**"
        f"（C卫星可用 {c_sat*100:.1f}% · OFF顶{STOCK_CAP_OFF*100:.0f}% / WARN顶{STOCK_CAP_WARN*100:.0f}%）",
        f"> 来源：industry_pack `{','.join(pack_ids or DEFAULT_PACKS)}` · trinity_eligible/candidates · 结构过线",
        f"> 闸 **{st}** · 指数 Trinity 系数 {float(trinity_mult):.2f}（仅 OFF 影响 C 卫星可用）\n",
        "| 标的 | 名称 | 目标% | tier | exec_mult | 结构分 | 备注 |",
        "|---|---|---:|---|---:|---:|---|",
    ]
    for s, r in zip(selected, rows):
        lines.append(
            f"| **{r['ticker']}** | {r['name']} | {r['pct']:.1f} | {s.get('tier','')} | "
            f"{float(s.get('exec_mult') or 0):.2f} | {float(s.get('structure_score') or 0):.2f} | "
            f"{(s.get('note') or '')[:28]} |"
        )
    lines.append("")
    lines.append(
        f"- 操作：从 **ETF 卫星** 等比挤出 {budget*100:.1f}% 给上表；**不碰核心**；"
        f"BEAR 时本袖清零。不自动下单。\n"
    )
    base["md"] = "\n".join(lines)
    return base


def apply_phase2_to_book_weights(
    weights: list[dict],
    phase2: dict,
    gate_state: str,
    book_id: str,
    core_tickers: set[str] | None = None,
) -> tuple[list[dict], str]:
    """把 phase2 stock_rows 写入某 book 权重。"""
    if not phase2 or not phase2.get("enabled"):
        return weights, "Phase2关"
    if (gate_state or "").upper() == "BEAR":
        return weights, "BEAR优先·个股0"
    rows = phase2.get("stock_rows") or []
    budget = float(phase2.get("budget") or 0.0)
    if not rows or budget <= 0:
        return weights, phase2.get("note") or "无个股"
    # Book A 的卫星=权益，仍受同一绝对预算硬顶（≤C卫星口径）
    return apply_stock_sleeve(weights, rows, budget, core_tickers=core_tickers)


# ---- 回测辅助：月末动量 TopK 股票收益，袖权重 = budget 序列 ----
def monthly_stock_top_returns(
    symbols: list[str],
    start: str,
    end: str,
    top_k: int = 3,
    lookback_days: int = 126,
) -> "object":
    """
    近似 Phase2 选股引擎（无未来函数）：
      每个再平衡日用过去 lookback 日收益排序，等权持有 TopK 至下一再平衡。
    返回与交易日对齐的组合日收益 Series。
    """
    import numpy as np
    import pandas as pd
    import yfinance as yf

    raw = yf.download(
        symbols,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        threads=False,
        group_by="ticker",
    )
    # normalize to wide close
    if isinstance(raw.columns, pd.MultiIndex):
        # yfinance multi: (ticker, OHLCV) or (OHLCV, ticker)
        if "Close" in raw.columns.get_level_values(0):
            px = raw["Close"].copy()
        elif "Close" in raw.columns.get_level_values(1):
            px = raw.xs("Close", axis=1, level=1).copy()
        else:
            px = raw.iloc[:, raw.columns.get_level_values(1) == "Close"]
            px.columns = px.columns.get_level_values(0)
    else:
        px = raw[["Close"]].copy() if "Close" in raw.columns else raw
        if px.shape[1] == 1:
            px.columns = [symbols[0]]

    px = px.dropna(how="all").ffill()
    px.index = pd.to_datetime(px.index).tz_localize(None)
    rets = px.pct_change().fillna(0.0)

    # 月末再平衡（用月初生效：信号用上月末）
    months = px.resample("ME").last().index
    hold = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    for i, dt in enumerate(months):
        # 信号日：该月最后可用日
        if dt not in px.index:
            # 找 ≤dt 最近
            sub = px.index[px.index <= dt]
            if len(sub) == 0:
                continue
            sig_dt = sub[-1]
        else:
            sig_dt = dt
        loc = px.index.get_loc(sig_dt)
        if isinstance(loc, slice):
            continue
        if loc < lookback_days:
            continue
        window = px.iloc[loc - lookback_days : loc + 1]
        mom = window.iloc[-1] / window.iloc[0] - 1.0
        mom = mom.dropna().sort_values(ascending=False)
        picks = list(mom.head(top_k).index)
        if not picks:
            continue
        # 持有到下一信号日
        if i + 1 < len(months):
            nxt = months[i + 1]
            mask = (hold.index > sig_dt) & (hold.index <= nxt)
        else:
            mask = hold.index > sig_dt
        w = 1.0 / len(picks)
        for t in picks:
            hold.loc[mask, t] = w

    port = (hold * rets).sum(axis=1)
    return port.fillna(0.0)


if __name__ == "__main__":
    # 快速自检
    b = build_phase2_bundle("OFF", trinity_mult=1.0)
    print(b["note"])
    print(b["md"][:800])
