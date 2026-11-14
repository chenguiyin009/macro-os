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
    fill_a_share_from_sina,
    BarStatus,
    annotate_source_gap,
    basket_members_for,
    classify_missing_next_session,
    ensure_bars,
    load_close_series,
    load_fourleg,
    load_spx,
    load_tech,
    next_session_need_through,
    next_session_return,
    next_weekday,
    prior_report_as_of,
    relative_return,
    session_return,
    series_last_date,
    theme_forward_return,
    theme_is_basket,
)
from rules import (  # noqa: E402
    OFFICIAL_RS_HORIZON,
    a_share_column_hit,
    direction_hit,
    pine_top3_hit,
    relative_strength_hit,
    semi_a_share_gate_integrity_hit,
    semi_a_share_gate_outcome_hit,
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
    ready_5d = d5 is not None and d5 <= done
    return {
        "prediction_as_of": as_of.isoformat(),
        "completed_session": done.isoformat(),
        "actual_1d_date": d1.isoformat() if d1 else None,
        "actual_5d_date": d5.isoformat() if d5 else None,
        "spy_1d": ret1,
        "spy_5d": ret5,
        "ready_1d": d1 is not None and d1 <= done,
        "ready_5d": ready_5d,
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


def _collect_theme_tickers(tech) -> List[str]:
    tickers: List[str] = []
    if not tech:
        return tickers
    rows = list(tech.strongest or []) + list(tech.weakest or [])
    # Also refresh members of any state-selected basket, plus selected proxy.
    for row in rows:
        if theme_is_basket(row):
            for m in basket_members_for(row):
                if m not in tickers:
                    tickers.append(m)
        else:
            px = row.get("proxy")
            if px and px != "篮子" and px not in tickers:
                tickers.append(px)
    return tickers


def _theme_rets(
    themes: List[Dict[str, Any]],
    as_of: date,
    horizon: int,
    target: Optional[date],
) -> Tuple[List[Optional[float]], List[str], List[str]]:
    rets: List[Optional[float]] = []
    names: List[str] = []
    skipped: List[str] = []
    for row in themes:
        label = str(row.get("name") or row.get("key") or row.get("proxy") or "?")
        r, _d, reason = theme_forward_return(row, as_of, horizon, target=target)
        if r is None:
            skipped.append(f"{label}（{reason or '无法测'}）")
            continue
        rets.append(r)
        names.append(label)
    return rets, names, skipped


def _refresh_a_share_bars(
    as_of: date,
    *,
    force_refresh: bool = False,
) -> Tuple[Dict[str, str], Dict[str, BarStatus], date]:
    """Refresh A/HK caches through the first session after as_of, cache-first."""
    expected = next_weekday(as_of)
    status: Dict[str, BarStatus] = {}
    sources: Dict[str, str] = {}
    # Per-ticker need_through: skip network when the next session is already cached.
    for t in A_SHARE_TICKERS:
        series = load_close_series(t)
        need = next_session_need_through(series, as_of, expected=expected)
        one_status: Dict[str, BarStatus] = {}
        src = ensure_bars(
            [t],
            force_refresh=force_refresh,
            need_through=need,
            status_out=one_status,
        )
        sources.update(src)
        status.update(one_status)
        st = one_status.get(t)
        series_after = load_close_series(t)
        last = (st.last_date if st else None) or series_last_date(series_after)
        has_need = last is not None and last >= need
        if st is not None and st.skipped_cache and has_need:
            continue
        if not has_need:
            # 2026-11-14：Yahoo 最新日常为 NaN → 用 Sina 日K 只追加缺失日期（与对A结构标签同一逻辑）
            added, serr = fill_a_share_from_sina(t, need)
            if added:
                sources[t] = f"{src.get(t) or 'cache'}+sina"
                last = series_last_date(load_close_series(t))
                status[t] = BarStatus(source=sources[t], last_date=last,
                                      notes=(list(st.notes) if st else []) + [f"sina 追加 {','.join(d.isoformat() for d in added)}"])
                if last is not None and last >= need:
                    continue
            if st is None or added:
                st = status.get(t) or BarStatus(source=src.get(t) or "none", last_date=last)
                status[t] = st
            if serr:
                st.notes.append(f"sina: {serr}")
            annotate_source_gap(t, need, st)
    return sources, status, expected


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
    for extra in _collect_theme_tickers(tech):
        if extra not in tickers:
            tickers.append(extra)
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
    qqq_5d = actuals.get("QQQ", {}).get("ret5")
    ready_5d = bool(windows.get("ready_5d"))
    target_1d = _parse_date(windows["actual_1d_date"]) if windows.get("actual_1d_date") else None
    target_5d = _parse_date(windows["actual_5d_date"]) if windows.get("actual_5d_date") else None

    hits = []
    # --- 四腿 ---
    if fl:
        hits.append(
            direction_hit(
                fl.posture,
                spy_1d,
                bias=fl.bias,
                bench="SPY",
                binding=fl.binding,
                denom_ceiling=fl.denom_ceiling,
            ).to_dict()
        )
        if qqq_1d is not None:
            hits.append(
                direction_hit(
                    fl.posture,
                    qqq_1d,
                    bias=fl.bias,
                    bench="QQQ",
                    binding=fl.binding,
                    denom_ceiling=fl.denom_ceiling,
                ).to_dict()
            )
    else:
        hits.append(
            {
                "name": "四腿方向",
                "hit": None,
                "detail": f"缺 daily_macro_{as_of}.json",
                "suggestion": "确认晨报四腿已产出再校准",
                "status": "abstain",
            }
        )

    tech_rs_meta: Dict[str, Any] = {}
    # --- 科技相对强弱（按状态选主题；官方 5D；1D 只观察） ---
    if tech:
        strong_1d, strong_names, strong_skip = _theme_rets(tech.strongest, as_of, 1, target_1d)
        weak_1d, weak_names, weak_skip = _theme_rets(tech.weakest, as_of, 1, target_1d)
        strong_5d, strong_names_5, strong_skip_5 = _theme_rets(
            tech.strongest, as_of, OFFICIAL_RS_HORIZON, target_5d
        )
        weak_5d, weak_names_5, weak_skip_5 = _theme_rets(
            tech.weakest, as_of, OFFICIAL_RS_HORIZON, target_5d
        )
        # Official when 5D window is ready; otherwise record 1D as 观察 only.
        if ready_5d:
            rs = relative_strength_hit(
                "科技",
                strong_5d,
                weak_5d,
                qqq_5d,
                horizon=OFFICIAL_RS_HORIZON,
                official=True,
                strong_names=strong_names_5,
                weak_names=weak_names_5,
            )
        else:
            rs = relative_strength_hit(
                "科技",
                strong_1d,
                weak_1d,
                qqq_1d,
                horizon=1,
                official=False,
                strong_names=strong_names,
                weak_names=weak_names,
            )
        hits.append(rs.to_dict())
        tech_rs_meta = {
            "status": rs.status,
            "official_horizon": OFFICIAL_RS_HORIZON,
            "ready_5d": ready_5d,
            "strong_names_1d": strong_names,
            "weak_names_1d": weak_names,
            "strong_names_5d": strong_names_5,
            "weak_names_5d": weak_names_5,
            "skipped_1d": strong_skip + weak_skip,
            "skipped_5d": strong_skip_5 + weak_skip_5,
            "rets_1d": {"strong": strong_1d, "weak": weak_1d, "qqq": qqq_1d},
            "rets_5d": {"strong": strong_5d, "weak": weak_5d, "qqq": qqq_5d},
        }
        am = tech.a_share_mapping or {}
        smh_rel1 = relative_return(smh, qqq, as_of, 1) if smh is not None and qqq is not None else None
        smh_rel5 = (
            relative_return(smh, qqq, as_of, OFFICIAL_RS_HORIZON)
            if smh is not None and qqq is not None
            else None
        )
        hits.append(
            semi_a_share_gate_integrity_hit(
                bool(am.get("force_semi_downgrade")),
                am.get("semi_chip_stance"),
                selected_score=tech.selected_score,
                avg_score=tech.avg_score,
                selected_rel5_pct=tech.selected_rel5_pct,
                selected_state=tech.selected_state,
                triggers=am.get("triggers"),
            ).to_dict()
        )
        hits.append(
            semi_a_share_gate_outcome_hit(
                bool(am.get("force_semi_downgrade")),
                am.get("semi_chip_stance"),
                smh_rel5,
                smh_rel1=smh_rel1,
                ready_5d=ready_5d and smh_rel5 is not None,
            ).to_dict()
        )
        tech_rs_meta["smh_rel1"] = smh_rel1
        tech_rs_meta["smh_rel5"] = smh_rel5
    else:
        hits.append(
            {
                "name": "科技相对强弱",
                "hit": None,
                "detail": f"缺 tech_rotation_{as_of}.json",
                "suggestion": None,
                "status": "abstain",
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
                "name": "标普 pine Top3（笔记·不驱动C）",
                "hit": None,
                "detail": f"缺 daily_abc / sector_rotation for {as_of}",
                "suggestion": None,
                "status": "abstain",
            }
        )

    # --- 对A列（与美股 1D 校准分开；勿混写「美股defend偏早」） ---
    a_sources, a_status, expected_a = _refresh_a_share_bars(as_of, force_refresh=force_refresh)
    sources = {**sources, **{f"a:{k}": v for k, v in a_sources.items()}}
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
    skip_reason = None
    if cyb_1d is None and hs_1d is None:
        # Prefer 沪深300 status, then 创业板.
        for tk, series, ret in (
            ("510300.SS", hs_s, hs_1d),
            ("159915.SZ", cyb_s, cyb_1d),
        ):
            st = a_status.get(tk) or BarStatus(source=a_sources.get(tk) or "none")
            last = st.last_date or series_last_date(series)
            skip_reason = classify_missing_next_session(
                as_of=as_of,
                expected=expected_a,
                last_date=last,
                source=st.source,
                notes=st.notes,
                persist_error=st.persist_error,
                today=date.today(),
            )
            break
    am = (tech.a_share_mapping if tech else {}) or {}
    a_col_hit = a_share_column_hit(
        fl.posture if fl else None,
        am.get("semi_chip_stance"),
        bool(am.get("force_semi_downgrade")),
        cyb_1d,
        hs_1d,
        bias=fl.bias if fl else None,
        skip_reason=skip_reason,
    ).to_dict()
    a_share_column = {
        "prediction_as_of": as_of.isoformat(),
        "posture": fl.posture if fl else None,
        "bias": fl.bias if fl else None,
        "semi_chip_stance": am.get("semi_chip_stance"),
        "force_semi_downgrade": bool(am.get("force_semi_downgrade")),
        "structure_label": am.get("structure_label"),
        "expected_next_weekday": expected_a.isoformat(),
        "need_through": {
            t: (a_status.get(t).source if a_status.get(t) else a_sources.get(t))
            for t in A_SHARE_TICKERS
        },
        "bar_status": {
            t: {
                "source": (a_status.get(t).source if a_status.get(t) else a_sources.get(t)),
                "last_date": (
                    a_status[t].last_date.isoformat()
                    if a_status.get(t) and a_status[t].last_date
                    else (series_last_date(load_close_series(t)).isoformat() if series_last_date(load_close_series(t)) else None)
                ),
                "notes": list(a_status[t].notes) if a_status.get(t) else [],
                "persist_error": a_status[t].persist_error if a_status.get(t) else None,
                "skipped_cache": bool(a_status[t].skipped_cache) if a_status.get(t) else False,
            }
            for t in A_SHARE_TICKERS
        },
        "skip_reason": skip_reason,
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

    # Build 命中 / 落空 / 可改点 — official only (观察/弃权不计入)
    hit_lines = [h for h in hits if h.get("hit") is True and h.get("status") != "observe"]
    miss_lines = [h for h in hits if h.get("hit") is False and h.get("status") != "observe"]
    suggestions = [
        h["suggestion"]
        for h in hits
        if h.get("suggestion") and h.get("status") != "observe"
    ]
    a_sug = (a_col_hit or {}).get("suggestion")
    if a_sug:
        suggestions.append(a_sug)

    # Cap 1–2 each for prose
    note = {
        "schema": "gybot.calibration.v2",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "prediction_as_of": as_of.isoformat(),
        "actual_1d_date": windows.get("actual_1d_date"),
        "actual_5d_date": windows.get("actual_5d_date"),
        "completed_session": windows.get("completed_session"),
        "ready_1d": windows.get("ready_1d"),
        "ready_5d": windows.get("ready_5d"),
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
                "denom_ceiling": fl.denom_ceiling,
                "stance": fl.stance,
            },
            "tech": None
            if not tech
            else {
                "path": tech.path,
                "strongest": tech.strongest,
                "weakest": tech.weakest,
                "themes_count": len(tech.themes or []),
                "selected_theme": tech.selected_theme,
                "selected_rel5_pct": tech.selected_rel5_pct,
                "selected_score": tech.selected_score,
                "avg_score": tech.avg_score,
                "selected_state": tech.selected_state,
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
        "tech_rs": tech_rs_meta,
        "hits": hits,
        "hit_summary": [h["detail"] for h in hit_lines[:2]],
        "miss_summary": [h["detail"] for h in miss_lines[:2]],
        "suggestions": suggestions[:4],
        "a_share_column": a_share_column,
        "disclaimer": "校准只出建议，不改打分；不改生产 C / 四腿 min / pine 默认。教学/数据参考，不构成投资建议。对A列与美股1D分列。科技相对强弱官方窗口为5个交易日；1D只记观察。",
    }
    return note


def render_md(note: Dict[str, Any]) -> str:
    as_of = note["prediction_as_of"]
    act = note.get("actual_1d_date") or "—"
    act5 = note.get("actual_5d_date") or "—"
    ready5 = "是" if note.get("ready_5d") else "否（5D尚未齐，科技/半导结果记观察）"
    lines = [
        "---",
        "tags:",
        "  - gybot",
        "  - 校准",
        f"as_of: {as_of}",
        f"actual_1d: {act}",
        f"actual_5d: {act5}",
        f"schema: {note.get('schema') or 'gybot.calibration.v2'}",
        "---",
        "",
        f"# 校准笔记 · 预判 {as_of} → 实际 1D {act} ｜ 官方5D {act5}",
        "",
        f"- 生成：{note.get('generated_at')}（Asia/Shanghai）",
        f"- 最新完整美股现金收盘：{note.get('completed_session')}",
        f"- 1D就绪：{'是' if note.get('ready_1d') else '否（缺次日 bar）'} ｜ 5D就绪：{ready5}",
        "",
        "> 校准只出建议，不改打分；生产 C / 四腿 min / pine 默认不动。科技强弱与半导结果以 5 个交易日为官方窗口；1D 只记观察，不算命中/落空。",
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
        def _fmt_theme(x: Dict[str, Any]) -> str:
            st = x.get("state") or ""
            px = x.get("proxy") or ""
            return f"{x.get('name')}({px}/{st})"

        strong = "、".join(_fmt_theme(x) for x in (tech.get("strongest") or []))
        weak = "、".join(_fmt_theme(x) for x in (tech.get("weakest") or []))
        am = tech.get("a_share_mapping") or {}
        lines += [
            f"- **科技（按状态，含篮子）**：强 {strong or '无明确偏强'} ｜ 弱 {weak or '无明确撤出'}",
            (
                f"- **对A半导门控**：{am.get('semi_chip_stance')} "
                f"(force_downgrade={am.get('force_semi_downgrade')}"
                + (
                    f"；触发={'+'.join(am.get('triggers') or [])}"
                    if am.get("triggers")
                    else ""
                )
                + (
                    "；仅刚负"
                    if am.get("shadow_weak_downgrade")
                    else ""
                )
                + (
                    f"；预判rel5={tech.get('selected_rel5_pct'):+.2f}%"
                    if tech.get("selected_rel5_pct") is not None
                    else ""
                )
                + (
                    f"；score={tech.get('selected_score')} avg={tech.get('avg_score')} state={tech.get('selected_state')}"
                    if tech.get("selected_score") is not None or tech.get("avg_score") is not None
                    else ""
                )
                + ")"
            ),
        ]
        tr = note.get("tech_rs") or {}
        skipped = tr.get("skipped_5d") or tr.get("skipped_1d") or []
        if skipped:
            lines.append("- **主题跳过（无法测）**：" + "；".join(skipped[:8]))
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
        lines.append(f"- **标普 pine Top3（笔记·不驱动C）**：{top or '—'}")
        if cw:
            lines.append(f"- **C 权重（只读）**：{cw}")
    else:
        lines.append("- **标普**：缺文件")

    # Actuals snapshot
    act1 = note.get("actuals_1d") or {}
    show = ["SPY", "QQQ", "SMH", "IGV", "SKYY", "CIBR", "XLE", "XLP", "XLU"]
    bits = [f"{t} {_fmt_pct(act1.get(t))}" for t in show if t in act1]
    lines += ["", "## 实际 1D（现金收盘）", "", "- " + " ｜ ".join(bits), ""]
    if note.get("ready_5d"):
        act5d = note.get("actuals_5d") or {}
        bits5 = [f"{t} {_fmt_pct(act5d.get(t))}" for t in ("SPY", "QQQ", "SMH") if t in act5d]
        if bits5:
            lines += ["## 实际 5D（官方窗口）", "", "- " + " ｜ ".join(bits5), ""]

    lines += ["## 命中（官方）", ""]
    hs = note.get("hit_summary") or []
    if hs:
        for i, s in enumerate(hs[:2], 1):
            lines.append(f"{i}. {s}")
    else:
        lines.append("（无）")
    lines += ["", "## 落空（官方）", ""]
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
        act_map = ac.get("actuals") or {}
        lines.append(
            f"- **预判 as_of**：{ac.get('prediction_as_of')} ｜ "
            f"posture=`{ac.get('posture')}` ｜ 半导=`{ac.get('semi_chip_stance')}` ｜ "
            f"force_downgrade={ac.get('force_semi_downgrade')}"
        )
        if ac.get("structure_label"):
            lines.append(f"- **结构标签（观测）**：{ac.get('structure_label')}")
        lines.append(
            f"- **次日 A/港**（actual≈{ac.get('actual_a_date')}，期望工作日 {ac.get('expected_next_weekday')}）："
            f"创业板 {_fmt_pct(act_map.get('创业板'))} ｜ "
            f"沪深300 {_fmt_pct(act_map.get('沪深300'))} ｜ "
            f"恒指 {_fmt_pct(act_map.get('恒指'))} ｜ "
            f"恒科 {_fmt_pct(act_map.get('恒科'))}"
        )
        if ac.get("skip_reason"):
            lines.append(f"- **数据缺口**：{ac.get('skip_reason')}")
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
        "## 规则明细",
        "",
    ]
    for h in note.get("hits") or []:
        mark = {True: "✅", False: "❌", None: "➖"}.get(h.get("hit"), "➖")
        status = h.get("status") or ""
        status_bit = f"（{status}）" if status else ""
        lines.append(f"- {mark} **{h.get('name')}**{status_bit}：{h.get('detail')}")
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
    if actual_1d and f"actual_1d: {actual_1d}" in text and "schema: gybot.calibration.v2" in text:
        return True
    # also treat same as_of with ready v2 content as done
    return (
        "校准笔记" in text
        and actual_1d is not None
        and actual_1d in text
        and "schema: gybot.calibration.v2" in text
    )


def _has_official_5d(note: Dict[str, Any]) -> bool:
    if not note.get("ready_5d"):
        return False
    for h in note.get("hits") or []:
        name = h.get("name") or ""
        if "主题相对强弱" in name and h.get("status") == "official":
            return True
        if "门控结果" in name and h.get("status") == "official":
            return True
    return False


def pending_5d_as_ofs(completed: date) -> List[date]:
    """as_of dates whose 5th US cash session is now complete but still 观察."""
    out: List[date] = []
    spy = load_close_series("SPY")
    for p in sorted(OUT_DIR.glob("calibration_20*.json")):
        if "_v1_archive" in str(p):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        raw = d.get("prediction_as_of")
        if not raw:
            continue
        try:
            as_of = date.fromisoformat(str(raw))
        except ValueError:
            continue
        if spy is None:
            continue
        _r5, d5 = session_return(spy, as_of, OFFICIAL_RS_HORIZON)
        if d5 is None or d5 > completed:
            continue
        if _has_official_5d(d):
            continue
        out.append(as_of)
    return out


def write_note(as_of: date, note: Dict[str, Any], *, dry_run: bool = False) -> Tuple[Path, Path, str]:
    md = render_md(note)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / f"calibration_{as_of.isoformat()}.json"
    md_path = staging_path(as_of)
    if not dry_run:
        json_path.write_text(json.dumps(note, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(md, encoding="utf-8")
    hits = [h for h in (note.get("hits") or []) if h.get("hit") is True and h.get("status") != "observe"]
    misses = [h for h in (note.get("hits") or []) if h.get("hit") is False and h.get("status") != "observe"]
    sugs = note.get("suggestions") or []
    hs = note.get("hit_summary") or []
    ms = note.get("miss_summary") or []
    summary = (
        f"校准 {as_of}→{note.get('actual_1d_date')}："
        f"命中{len(hits)} · 落空{len(misses)}"
        f"{'；5D官方已回填' if note.get('ready_5d') else '；科技/半导结果仍为观察'}；"
        f"要点命中={hs[0] if hs else '无'}；"
        f"要点落空={ms[0] if ms else '无'}；"
        f"建议={sugs[0] if sugs else '无'}。"
    )
    return json_path, md_path, summary


def backfill_ready_5d(
    completed: date,
    *,
    force_refresh: bool = False,
    dry_run: bool = False,
) -> List[str]:
    """Update JSON/MD for as_of dates whose official 5D window just completed."""
    messages: List[str] = []
    for as_of in pending_5d_as_ofs(completed):
        note = run_calibration(as_of, force_refresh=force_refresh, completed=completed)
        json_path, md_path, summary = write_note(as_of, note, dry_run=dry_run)
        messages.append(f"5D回填 {as_of}：{summary} JSON={json_path} MD={md_path}")
        print(messages[-1])
    return messages


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
    ap.add_argument(
        "--backfill-5d-only",
        action="store_true",
        help="Only backfill as_of dates whose 5D official window is now complete",
    )
    args = ap.parse_args(argv)

    os.environ.setdefault("SECTOR_PROXY", "off")
    import bar_sources as bs

    completed = _parse_date(args.completed) if args.completed else bs.completed_us_cash_session()

    # Always try 5D official backfill first so a staged 1D note does not stay 观察 forever.
    # Cache-first: need_through skips yfinance when the 5th session is already on disk.
    ensure_bars(["SPY", "QQQ", "SMH"], force_refresh=args.force_refresh, need_through=completed)
    backfill_ready_5d(completed, force_refresh=args.force_refresh, dry_run=args.dry_run)
    if args.backfill_5d_only:
        return 0

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
    json_path, md_path, summary = write_note(as_of, note, dry_run=args.dry_run)
    print(summary)
    print(f"JSON: {json_path}")
    print(f"MD: {md_path}")
    if args.dry_run:
        print("--- MD preview ---")
        print(render_md(note))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
