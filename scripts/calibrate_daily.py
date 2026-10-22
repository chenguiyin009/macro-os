#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily prediction-vs-actual calibration across 四腿 / 科技 / 标普.

- Reads structured conclusions from existing report JSON.
- Pulls ETF bars via bar_sources (SECTOR_PROXY=off).
- Emits Chinese 校准笔记 (JSON + MD) to staging. Suggestions only —
  never mutates production C / 四腿 min / pine defaults / scores.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from loaders import (  # noqa: E402
    ensure_bars,
    load_close_series,
    load_fourleg,
    load_spx,
    load_tech,
    next_session_return,
    prior_report_as_of,
    relative_return,
    session_return,
)
from rules import (  # noqa: E402
    a_share_column_hit,
    direction_hit,
    pine_top3_hit,
    relative_strength_hit,
    semi_a_share_gate_hit,
)

STAGING_DIR = Path(
    os.environ.get(
        "CALIB_STAGING",
        "/workspace/obsidian-staging/GYbot日报/校准",
    )
)
OUT_DIR = ROOT / "output"
TZ = ZoneInfo("Asia/Shanghai")

CORE_TICKERS = [
    "SPY", "QQQ", "SMH", "IGV", "SKYY", "CIBR",
    "XLE", "XLP", "XLU", "XLK",
]
A_SHARE_TICKERS = [
    "159915.SZ",  # 创业板ETF（指数直链常缺历史）
    "510300.SS",  # 沪深300ETF
    "^HSI",       # 恒指
    "3032.HK",    # 恒科 ETF 代理
]


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{x * 100:+.2f}%"


def resolve_windows(
    as_of: date,
    *,
    completed: Optional[date] = None,
) -> Dict[str, Any]:
    """Actual window: as_of → next completed session (1D), optional 5D."""
    import bar_sources as bs

    done = completed or bs.completed_us_cash_session()
    spy = load_close_series("SPY")
    ret1, d1 = (None, None)
    ret5, d5 = (None, None)
    if spy is not None:
        ret1, d1 = session_return(spy, as_of, 1)
        ret5, d5 = session_return(spy, as_of, 5)
    return {
        "prediction_as_of": as_of.isoformat(),
        "completed_session": done.isoformat(),
        "actual_1d_date": d1.isoformat() if d1 else None,
        "actual_5d_date": d5.isoformat() if d5 else None,
        "spy_1d": ret1,
        "spy_5d": ret5,
        "ready_1d": d1 is not None and d1 <= done,
    }


def collect_actuals(as_of: date, tickers: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for t in tickers:
        s = load_close_series(t)
        if s is None:
            out[t] = {"ret1": None, "ret5": None}
            continue
        r1, _ = session_return(s, as_of, 1)
        r5, _ = session_return(s, as_of, 5)
        out[t] = {"ret1": r1, "ret5": r5}
    return out


def run_calibration(
    as_of: date,
    *,
    force_refresh: bool = False,
    completed: Optional[date] = None,
) -> Dict[str, Any]:
    os.environ.setdefault("SECTOR_PROXY", "off")
    import bar_sources as bs

    if completed is None:
        completed = bs.completed_us_cash_session()

    # Gather tickers from predictions first (may expand list)
    fl = load_fourleg(as_of.isoformat())
    tech = load_tech(as_of.isoformat())
    spx = load_spx(as_of.isoformat())

    tickers = list(CORE_TICKERS)
    if tech:
        for row in tech.strongest + tech.weakest:
            px = row.get("proxy")
            if px and px != "篮子" and px not in tickers:
                tickers.append(px)
    if spx:
        for row in spx.pine_top3:
            tk = row.get("ticker")
            if tk and tk not in tickers:
                tickers.append(tk)

    sources = ensure_bars(tickers, force_refresh=force_refresh, need_through=completed or as_of)
    windows = resolve_windows(as_of, completed=completed)
    actuals = collect_actuals(as_of, tickers)

    qqq = load_close_series("QQQ")
    spy_s = load_close_series("SPY")
    smh = load_close_series("SMH")
    spy_1d = actuals.get("SPY", {}).get("ret1")
    qqq_1d = actuals.get("QQQ", {}).get("ret1")

    hits = []
    # --- 四腿 ---
    if fl:
        hits.append(
            direction_hit(fl.posture, spy_1d, bias=fl.bias, bench="SPY").to_dict()
        )
        if qqq_1d is not None:
            hits.append(
                direction_hit(fl.posture, qqq_1d, bias=fl.bias, bench="QQQ").to_dict()
            )
    else:
        hits.append(
            {
                "name": "四腿方向",
                "hit": None,
                "detail": f"缺 daily_macro_{as_of}.json",
                "suggestion": "确认晨报四腿已产出再校准",
            }
        )

    # --- 科技相对强弱 ---
    if tech:
        strong_rets = [actuals.get(r["proxy"], {}).get("ret1") for r in tech.strongest if r.get("proxy") != "篮子"]
        weak_rets = [actuals.get(r["proxy"], {}).get("ret1") for r in tech.weakest if r.get("proxy") != "篮子"]
        hits.append(
            relative_strength_hit("科技", strong_rets, weak_rets, qqq_1d).to_dict()
        )
        am = tech.a_share_mapping or {}
        smh_rel1 = relative_return(smh, qqq, as_of, 1) if smh is not None and qqq is not None else None
        smh_rel5 = relative_return(smh, qqq, as_of, 5) if smh is not None and qqq is not None else None
        hits.append(
            semi_a_share_gate_hit(
                bool(am.get("force_semi_downgrade")),
                am.get("semi_chip_stance"),
                smh_rel1,
                smh_rel5,
            ).to_dict()
        )
    else:
        hits.append(
            {
                "name": "科技相对强弱",
                "hit": None,
                "detail": f"缺 tech_rotation_{as_of}.json",
                "suggestion": None,
            }
        )

    # --- 标普 pine Top3 ---
    if spx and spx.pine_top3:
        pairs = [
            (r.get("ticker") or r.get("name") or "?", actuals.get(r.get("ticker") or "", {}).get("ret1"))
            for r in spx.pine_top3
        ]
        hits.append(pine_top3_hit(pairs, spy_1d).to_dict())
    else:
        hits.append(
            {
                "name": "标普pine Top3相对",
                "hit": None,
                "detail": f"缺 daily_abc / sector_rotation for {as_of}",
                "suggestion": None,
            }
        )

    # --- 对A列（与美股 1D 校准分开；勿混写「美股defend偏早」） ---
    ensure_bars(list(A_SHARE_TICKERS), force_refresh=force_refresh, need_through=None)
    cyb_s = load_close_series("159915.SZ")
    hs_s = load_close_series("510300.SS")
    hsi_s = load_close_series("^HSI")
    hstech_s = load_close_series("3032.HK")
    cyb_1d, cyb_d = (next_session_return(cyb_s, as_of) if cyb_s is not None else (None, None))
    hs_1d, hs_d = (next_session_return(hs_s, as_of) if hs_s is not None else (None, None))
    hsi_1d, hsi_d = (next_session_return(hsi_s, as_of) if hsi_s is not None else (None, None))
    hstech_1d, hstech_d = (
        next_session_return(hstech_s, as_of) if hstech_s is not None else (None, None)
    )
    am = (tech.a_share_mapping if tech else {}) or {}
    a_col_hit = a_share_column_hit(
        fl.posture if fl else None,
        am.get("semi_chip_stance"),
        bool(am.get("force_semi_downgrade")),
        cyb_1d,
        hs_1d,
        bias=fl.bias if fl else None,
    ).to_dict()
    a_share_column = {
        "prediction_as_of": as_of.isoformat(),
        "posture": fl.posture if fl else None,
        "bias": fl.bias if fl else None,
        "semi_chip_stance": am.get("semi_chip_stance"),
        "force_semi_downgrade": bool(am.get("force_semi_downgrade")),
        "structure_label": am.get("structure_label"),
        "actual_a_date": (
            (hs_d or cyb_d or hsi_d or hstech_d).isoformat()
            if (hs_d or cyb_d or hsi_d or hstech_d)
            else None
        ),
        "actuals": {
            "创业板": cyb_1d,
            "沪深300": hs_1d,
            "恒指": hsi_1d,
            "恒科": hstech_1d,
        },
        "actual_dates": {
            "创业板": cyb_d.isoformat() if cyb_d else None,
            "沪深300": hs_d.isoformat() if hs_d else None,
            "恒指": hsi_d.isoformat() if hsi_d else None,
            "恒科": hstech_d.isoformat() if hstech_d else None,
        },
        "hit": a_col_hit,
        "note": "对A列校准与美股 1D 分开；建议文案勿与「美股defend偏早」混写。",
    }

    # Build 命中 / 落空 / 可改点
    hit_lines = [h for h in hits if h.get("hit") is True]
    miss_lines = [h for h in hits if h.get("hit") is False]
    suggestions = [h["suggestion"] for h in hits if h.get("suggestion")]

    # Cap 1–2 each for prose
    note = {
        "schema": "gybot.calibration.v1",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "prediction_as_of": as_of.isoformat(),
        "actual_1d_date": windows.get("actual_1d_date"),
        "actual_5d_date": windows.get("actual_5d_date"),
        "completed_session": windows.get("completed_session"),
        "ready_1d": windows.get("ready_1d"),
        "bar_sources": sources,
        "inputs": {
            "fourleg": None
            if not fl
            else {
                "path": fl.path,
                "posture": fl.posture,
                "bias": fl.bias,
                "binding": fl.binding,
                "combined_budget": fl.combined_budget,
                "stance": fl.stance,
            },
            "tech": None
            if not tech
            else {
                "path": tech.path,
                "strongest": tech.strongest,
                "weakest": tech.weakest,
                "selected_theme": tech.selected_theme,
                "a_share_mapping": tech.a_share_mapping,
            },
            "spx": None
            if not spx
            else {
                "path": spx.path,
                "pine_top3": spx.pine_top3,
                "c_weights": spx.c_weights,
            },
        },
        "actuals_1d": {k: v.get("ret1") for k, v in actuals.items()},
        "actuals_5d": {k: v.get("ret5") for k, v in actuals.items()},
        "hits": hits,
        "hit_summary": [h["detail"] for h in hit_lines[:2]],
        "miss_summary": [h["detail"] for h in miss_lines[:2]],
        "suggestions": suggestions[:4],
        "a_share_column": a_share_column,
        "disclaimer": "校准只出建议，不改打分；不改生产 C / 四腿 min / pine 默认。教学/数据参考，不构成投资建议。对A列与美股1D分列。",
    }
    return note


def render_md(note: Dict[str, Any]) -> str:
    as_of = note["prediction_as_of"]
    act = note.get("actual_1d_date") or "—"
    lines = [
        "---",
        "tags:",
        "  - gybot",
        "  - 校准",
        f"as_of: {as_of}",
        f"actual_1d: {act}",
        "---",
        "",
        f"# 校准笔记 · 预判 {as_of} → 实际 1D {act}",
        "",
        f"- 生成：{note.get('generated_at')}（Asia/Shanghai）",
        f"- 最新完整美股现金收盘：{note.get('completed_session')}",
        f"- 就绪：{'是' if note.get('ready_1d') else '否（缺次日 bar）'}",
        "",
        "> 校准只出建议，不改打分；生产 C / 四腿 min / pine 默认不动。",
        "",
        "## 输入摘要",
        "",
    ]
    fl = (note.get("inputs") or {}).get("fourleg")
    if fl:
        lines += [
            f"- **四腿**：posture=`{fl.get('posture')}` bias=`{fl.get('bias')}` "
            f"binding={fl.get('binding')} budget={fl.get('combined_budget')}",
        ]
    else:
        lines.append("- **四腿**：缺文件")
    tech = (note.get("inputs") or {}).get("tech")
    if tech:
        strong = "、".join(
            f"{x.get('name')}({x.get('proxy')})" for x in (tech.get("strongest") or [])[:3]
        )
        weak = "、".join(
            f"{x.get('name')}({x.get('proxy')})" for x in (tech.get("weakest") or [])[:2]
        )
        am = tech.get("a_share_mapping") or {}
        lines += [
            f"- **科技**：强 {strong or '—'} ｜ 弱 {weak or '—'}",
            f"- **对A半导门控**：{am.get('semi_chip_stance')} "
            f"(force_downgrade={am.get('force_semi_downgrade')})",
        ]
    else:
        lines.append("- **科技**：缺文件")
    spx = (note.get("inputs") or {}).get("spx")
    if spx:
        top = "、".join(
            f"{x.get('ticker')}({x.get('score')})" for x in (spx.get("pine_top3") or [])[:3]
        )
        cw = " · ".join(
            f"{x.get('ticker')} {x.get('pct')}%" for x in (spx.get("c_weights") or [])[:6]
        )
        lines.append(f"- **标普 pine Top3**：{top or '—'}")
        if cw:
            lines.append(f"- **C 权重（只读）**：{cw}")
    else:
        lines.append("- **标普**：缺文件")

    # Actuals snapshot
    act1 = note.get("actuals_1d") or {}
    show = ["SPY", "QQQ", "SMH", "IGV", "SKYY", "CIBR", "XLE", "XLP", "XLU"]
    bits = [f"{t} {_fmt_pct(act1.get(t))}" for t in show if t in act1]
    lines += ["", "## 实际 1D（现金收盘）", "", "- " + " ｜ ".join(bits), ""]

    lines += ["## 命中", ""]
    hs = note.get("hit_summary") or []
    if hs:
        for i, s in enumerate(hs[:2], 1):
            lines.append(f"{i}. {s}")
    else:
        lines.append("（无）")
    lines += ["", "## 落空", ""]
    ms = note.get("miss_summary") or []
    if ms:
        for i, s in enumerate(ms[:2], 1):
            lines.append(f"{i}. {s}")
    else:
        lines.append("（无）")
    lines += ["", "## 可改点（建议 · 不改分）", ""]
    sugs = note.get("suggestions") or []
    if sugs:
        for i, s in enumerate(sugs, 1):
            lines.append(f"{i}. {s}")
    else:
        lines.append("- 暂无可改点；规则样本偏少时保持观察。")
    ac = note.get("a_share_column") or {}
    lines += ["", "## 对A列校准（与美股1D分开）", ""]
    if ac:
        act = ac.get("actuals") or {}
        lines.append(
            f"- **预判 as_of**：{ac.get('prediction_as_of')} ｜ "
            f"posture=`{ac.get('posture')}` ｜ 半导=`{ac.get('semi_chip_stance')}` ｜ "
            f"force_downgrade={ac.get('force_semi_downgrade')}"
        )
        if ac.get("structure_label"):
            lines.append(f"- **结构标签（观测）**：{ac.get('structure_label')}")
        lines.append(
            f"- **次日 A/港**（actual≈{ac.get('actual_a_date')}）："
            f"创业板 {_fmt_pct(act.get('创业板'))} ｜ "
            f"沪深300 {_fmt_pct(act.get('沪深300'))} ｜ "
            f"恒指 {_fmt_pct(act.get('恒指'))} ｜ "
            f"恒科 {_fmt_pct(act.get('恒科'))}"
        )
        ah = ac.get("hit") or {}
        mark = {True: "✅", False: "❌", None: "➖"}.get(ah.get("hit"), "➖")
        lines.append(f"- {mark} **{ah.get('name') or '对A列'}**：{ah.get('detail')}")
        if ah.get("suggestion"):
            lines.append(f"- **对A建议（勿混美股）**：{ah['suggestion']}")
        lines.append(f"- _{ac.get('note')}_")
    else:
        lines.append("- （无对A列数据）")

    lines += [
        "",
        "## 规则明细（美股1D等）",
        "",
    ]
    for h in note.get("hits") or []:
        mark = {True: "✅", False: "❌", None: "➖"}.get(h.get("hit"), "➖")
        lines.append(f"- {mark} **{h.get('name')}**：{h.get('detail')}")
    lines += [
        "",
        "---",
        "",
        note.get("disclaimer") or "",
        "",
    ]
    return "\n".join(lines)


def staging_path(as_of: date) -> Path:
    return STAGING_DIR / f"{as_of.isoformat()}.md"


def already_done(as_of: date, actual_1d: Optional[str]) -> bool:
    p = staging_path(as_of)
    if not p.exists():
        return False
    text = p.read_text(encoding="utf-8")
    if actual_1d and f"actual_1d: {actual_1d}" in text:
        return True
    # also treat same as_of with ready content as done
    return "校准笔记" in text and actual_1d is not None and actual_1d in text


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Daily calibration note (suggestions only)")
    ap.add_argument(
        "--as-of",
        help="Prediction as_of (YYYY-MM-DD). Default: prior US business day before completed session.",
    )
    ap.add_argument(
        "--completed",
        help="Override completed US cash session (YYYY-MM-DD).",
    )
    ap.add_argument("--force-refresh", action="store_true", help="Force bar refresh")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even if staging already has this window",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print only, no write")
    args = ap.parse_args(argv)

    os.environ.setdefault("SECTOR_PROXY", "off")
    import bar_sources as bs

    completed = _parse_date(args.completed) if args.completed else bs.completed_us_cash_session()
    as_of = _parse_date(args.as_of) if args.as_of else prior_report_as_of(completed)

    # Quick readiness: need 1D bar after as_of
    # Refresh core bars first for readiness check
    ensure_bars(["SPY", "QQQ"], force_refresh=args.force_refresh, need_through=completed)
    windows = resolve_windows(as_of, completed=completed)
    if not windows.get("ready_1d"):
        msg = "校准无新数据"
        print(msg)
        print(
            json.dumps(
                {
                    "status": "no_new_data",
                    "reason": "missing_1d_bar",
                    "prediction_as_of": as_of.isoformat(),
                    "completed_session": completed.isoformat(),
                    "actual_1d_date": windows.get("actual_1d_date"),
                },
                ensure_ascii=False,
            )
        )
        return 0

    if not args.force and already_done(as_of, windows.get("actual_1d_date")):
        print("校准无新数据")
        print(
            json.dumps(
                {
                    "status": "no_new_data",
                    "reason": "already_staged",
                    "prediction_as_of": as_of.isoformat(),
                    "staging": str(staging_path(as_of)),
                },
                ensure_ascii=False,
            )
        )
        return 0

    note = run_calibration(as_of, force_refresh=args.force_refresh, completed=completed)
    md = render_md(note)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / f"calibration_{as_of.isoformat()}.json"
    md_path = staging_path(as_of)

    if not args.dry_run:
        json_path.write_text(json.dumps(note, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(md, encoding="utf-8")

    # One-line Chinese summary for chat / parent
    hits = note.get("hit_summary") or []
    misses = note.get("miss_summary") or []
    sugs = note.get("suggestions") or []
    summary = (
        f"校准 {as_of}→{note.get('actual_1d_date')}："
        f"命中{len([h for h in note.get('hits') or [] if h.get('hit') is True])} · "
        f"落空{len([h for h in note.get('hits') or [] if h.get('hit') is False])}；"
        f"要点命中={hits[0] if hits else '无'}；"
        f"要点落空={misses[0] if misses else '无'}；"
        f"建议={sugs[0] if sugs else '无'}。"
    )
    print(summary)
    print(f"JSON: {json_path}")
    print(f"MD: {md_path}")
    if args.dry_run:
        print("--- MD preview ---")
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
