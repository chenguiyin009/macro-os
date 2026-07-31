# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""Daily Macro Consolidated Report -- unify three daily observers.

Merges artifacts under output/:

  1) denominator state  -> denominator_state_<date>.{md,json}
     (denominator_state_daily.py headless port, or TV/MCP md fallback)
  2) tech dampener      -> tech_drawdown_<date>.json + tech_dampener_decision_<date>.md
  3) theme state machine-> theme_state_machine_<date>.{md,json}

Writes:
  output/daily_macro_<date>.md + .json

Includes alignment/bias/quality contract: never claim "一致/共振" when theme
data is stale/degraded. Observation only — not trade signals.

Usage:
    python scripts/daily_macro_consolidated.py --date 2026-07-20
    python scripts/daily_macro_consolidated.py --date 2026-07-20 --force-refresh
    python scripts/daily_macro_consolidated.py --date 2026-07-20 --no-run-theme
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("daily-macro-consolidated")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = REPO_ROOT.parent / "output"

THEME_SCRIPT = REPO_ROOT / "scripts" / "theme_state_machine_daily.py"
TECH_SCRIPT = REPO_ROOT / "scripts" / "daily_tech_dampener.py"
DENOM_SCRIPT = REPO_ROOT / "scripts" / "denominator_state_daily.py"


def _run_script(script: Path, args: list) -> bool:
    cmd = [sys.executable, str(script)] + args
    logger.info("subprocess: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        logger.warning("subprocess timed out: %s", script.name)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("subprocess failed: %s -> %s", script.name, exc)
        return False
    if proc.returncode != 0:
        logger.warning(
            "%s exited %d: %s",
            script.name,
            proc.returncode,
            (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else "",
        )
        return False
    return True


def _newest(report_dir: Path, pattern: str) -> Optional[Path]:
    """Prefer highest YYYY-MM-DD in filename; mtime only as tie-breaker."""
    hits = list(report_dir.glob(pattern))
    if not hits:
        return None

    def _key(p: Path):
        m = re.search(r"(20\d{2}-\d{2}-\d{2})", p.name)
        day = m.group(1) if m else ""
        return (day, p.stat().st_mtime)

    return sorted(hits, key=_key, reverse=True)[0]


def ensure_theme(
    report_dir: Path,
    no_run: bool,
    expected_as_of: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if no_run:
        existing = _newest(report_dir, "theme_state_machine_*.json")
        if existing:
            logger.info("reuse existing theme json (--no-run-theme): %s", existing.name)
            return json.loads(existing.read_text(encoding="utf-8"))
        logger.warning("theme json missing and --no-run-theme set; skipping")
        return None
    args = ["--report-dir", str(report_dir)]
    if expected_as_of:
        args += ["--expected-as-of", expected_as_of]
    if _run_script(THEME_SCRIPT, args):
        produced = _newest(report_dir, "theme_state_machine_*.json")
        if produced:
            return json.loads(produced.read_text(encoding="utf-8"))
    logger.warning("theme state machine unavailable")
    return None


def ensure_tech(report_dir: Path, date_str: str, no_run: bool) -> Optional[Dict[str, Any]]:
    target = report_dir / f"tech_drawdown_{date_str}.json"
    if no_run:
        if target.exists():
            logger.info("reuse existing tech json (--no-run-tech): %s", target.name)
            return json.loads(target.read_text(encoding="utf-8"))
        logger.warning("tech json missing and --no-run-tech set; skipping")
        return None
    if _run_script(TECH_SCRIPT, ["--date", date_str, "--report-dir", str(report_dir)]):
        if target.exists():
            return json.loads(target.read_text(encoding="utf-8"))
    logger.warning("tech dampener unavailable")
    return None


def _normalize_denom(d: Dict[str, Any], date_str: str) -> Dict[str, Any]:
    state = (d.get("state") or d.get("main_state") or "").replace(" ", "")
    return {
        "date": d.get("date") or date_str,
        "main_state": state,
        "state": state,
        "raw_state": (d.get("raw_state") or state or "").replace(" ", ""),
        "confidence_label": d.get("confidence_label", ""),
        "four_quad": d.get("quadrant") or d.get("four_quad") or "",
        "dont_do": d.get("dont_do", ""),
        "trigger_hint": d.get("trigger_hint", ""),
        "z5": d.get("z5", {}),
        "source": "json",
    }


def _parse_denom_md(md: Path, date_str: str) -> Dict[str, Any]:
    text = md.read_text(encoding="utf-8")

    def _field(label: str) -> str:
        m = re.search(rf"\|\s*{re.escape(label)}\s*\|\s*(.+?)\s*\|", text)
        if not m:
            return ""
        val = m.group(1).strip()
        val = re.sub(r"\*\*(.+?)\*\*", r"\1", val)
        val = val.replace("［", "[").replace("］", "]")
        return val

    # Prefer filename date when present
    mdate = re.search(r"(20\d{2}-\d{2}-\d{2})", md.name)
    asof = mdate.group(1) if mdate else date_str
    ms = _field("主状态").replace(" ", "")
    return {
        "date": asof,
        "main_state": ms,
        "state": ms,
        "raw_state": ms,
        "confidence_label": "",
        "four_quad": _field("四象限（官方）"),
        "dont_do": _field("今天不做什么"),
        "trigger_hint": _field("触发提示"),
        "z5": {},
        "source": "md",
    }


def load_denominator(report_dir: Path, date_str: str) -> Optional[Dict[str, Any]]:
    jp = report_dir / f"denominator_state_{date_str}.json"
    cand = jp if jp.exists() else _newest(report_dir, "denominator_state_*.json")
    if cand and cand.exists():
        try:
            d = json.loads(cand.read_text(encoding="utf-8"))
            return _normalize_denom(d, cand.stem.split("_")[-1])
        except Exception as exc:  # noqa: BLE001
            logger.warning("denominator json parse failed (%s): %s", cand.name, exc)
    md = report_dir / f"denominator_state_{date_str}.md"
    if not md.exists():
        md_hit = _newest(report_dir, "denominator_state_*.md")
        md = md_hit if md_hit else md
    if md and md.exists() and "analysis" not in md.name:
        return _parse_denom_md(md, date_str)
    return None


def ensure_denominator(report_dir: Path, date_str: str, no_run: bool) -> Optional[Dict[str, Any]]:
    if no_run:
        return load_denominator(report_dir, date_str)
    if DENOM_SCRIPT.exists() and _run_script(DENOM_SCRIPT, ["--report-dir", str(report_dir)]):
        return load_denominator(report_dir, date_str)
    logger.warning("denominator daily run failed/skipped; fall back to existing artifact")
    return load_denominator(report_dir, date_str)


def _infer_theme_quality(
    theme: Dict[str, Any],
    theme_date: str,
    report_date: str,
) -> Dict[str, Any]:
    """Build quality when old theme JSON lacks quality block.

    Benchmark against the consolidated report date (not denom FRED lag).
    """
    tq = dict(theme.get("quality") or {})
    if tq:
        return tq
    try:
        t_day = dt.date.fromisoformat(str(theme.get("date") or theme_date)[:10])
        e_day = dt.date.fromisoformat(str(report_date)[:10])
        try:
            import numpy as np

            lag = int(np.busday_count(t_day, e_day))
        except Exception:
            lag = max(0, (e_day - t_day).days)
        lag = max(0, lag)
        stale = lag > 1
        return {
            "as_of": t_day.isoformat(),
            "expected_as_of": e_day.isoformat(),
            "lag_days": lag,
            "stale": stale,
            "degraded": False,
            "data_quality": "stale" if stale else "ok",
            "inferred": True,
        }
    except Exception:
        return {}


def synthesize(
    theme: Optional[Dict],
    tech: Optional[Dict],
    denom: Optional[Dict],
    theme_date: str,
    tech_date: str,
    denom_date: Optional[str],
    report_date: Optional[str] = None,
) -> Dict[str, Any]:
    report_date = report_date or tech_date or theme_date

    tech_dd = (tech or {}).get("tech_drawdown")
    dec = (tech or {}).get("decision") or {}
    budget = dec.get("risk_budget")
    damp_active = dec.get("dampener_active")
    if budget is None:
        stress = "未知"
    elif budget <= 0.35:
        stress = "强压制"
    elif budget <= 0.50:
        stress = "中度压制"
    elif budget <= 0.65:
        stress = "轻度压制"
    else:
        stress = "无压制"

    if denom:
        main_state = denom.get("main_state", "")
        dont = denom.get("dont_do", "")
        trigger = denom.get("trigger_hint", "")
        if any(k in main_state for k in ["RISK_OFF", "risk_off", "紧缩", "压力"]):
            denom_tag = "偏紧/压力"
            denom_bias = "risk_off"
        elif any(k in main_state for k in ["未确认", "分裂", "横盘", "混合"]):
            denom_tag = "未确认/横盘"
            denom_bias = "none"
        elif any(k in main_state for k in ["RISK_ON", "risk_on", "宽松", "确认"]):
            # only after excluding 未确认
            if "未确认" in main_state:
                denom_tag = "未确认/横盘"
                denom_bias = "none"
            else:
                denom_tag = "偏松/确认"
                denom_bias = "risk_on"
        else:
            denom_tag = "未确认/横盘"
            denom_bias = "none"
    else:
        main_state = dont = trigger = ""
        denom_tag = "未读取"
        denom_bias = "none"

    dom = (theme or {}).get("dominant_theme")
    leaders = (theme or {}).get("today_leaders") or []
    strength = (theme or {}).get("strength_table") or []
    tq = _infer_theme_quality(theme or {}, theme_date, report_date) if theme else {}

    theme_bias = (
        (dom or {}).get("risk_bias")
        or (theme or {}).get("risk_bias")
        or "none"
    )
    pressure = bool(
        (dom or {}).get("pressure_override")
        or (theme or {}).get("pressure_override")
    )
    if dom:
        narrative = f"有主导叙事：{dom.get('name')}（{dom.get('family')}，{theme_bias}）"
    else:
        narrative = "无主导叙事（等待共振）"

    stale = bool(tq.get("stale"))
    degraded = bool(tq.get("degraded"))
    data_quality = tq.get("data_quality") or (
        "stale_degraded" if stale and degraded else
        "stale" if stale else
        "degraded" if degraded else
        "ok" if theme else "missing"
    )

    eq_down = any(l.get("dir") == "↓" and abs(l.get("z", 0)) >= 1.0 for l in leaders)
    eq_down = eq_down or any(
        ("标普" in s.get("label", "") or "纳指" in s.get("label", ""))
        and s.get("dir") == "↓"
        and s.get("level") in ("强", "异常")
        for s in strength
    )

    tech_bias = (
        "risk_off" if stress not in ("无压制", "未知") else
        ("none" if stress == "未知" else "risk_on")
    )

    if pressure or theme_bias == "risk_off":
        bias = "risk_off"
    elif theme_bias == "risk_on" and tech_bias == "risk_on" and denom_bias != "risk_off":
        bias = "risk_on"
    elif eq_down or tech_bias == "risk_off" or denom_bias == "risk_off":
        bias = "risk_off" if (eq_down and tech_bias == "risk_off") else "mixed"
    elif theme_bias == "mixed":
        bias = "mixed"
    else:
        bias = "none"

    if data_quality in ("stale", "degraded", "stale_degraded", "missing"):
        alignment = "数据降级"
    elif denom_tag == "未确认/横盘" and not dom and stress != "无压制":
        alignment = "未确认"
    elif denom_tag == "未读取" and not dom:
        alignment = "未确认"
    else:
        votes = [b for b in (denom_bias, tech_bias, theme_bias) if b in ("risk_on", "risk_off")]
        if pressure and tech_bias == "risk_off":
            alignment = "共振"
        elif len(votes) >= 2 and len(set(votes)) == 1:
            alignment = "共振"
        elif len(votes) >= 2 and len(set(votes)) > 1:
            alignment = "冲突"
        elif dom and stress != "未知":
            alignment = "部分"
        else:
            alignment = "未确认"

    divergences: list = []
    if data_quality in ("stale", "stale_degraded"):
        divergences.append(
            f"主题 as_of 落后（lag={tq.get('lag_days', '?')}）→ 禁止把跨工具读数说成同日共振"
        )
    if degraded:
        miss = ",".join(tq.get("critical_missing") or tq.get("missing_symbols") or [])
        divergences.append(f"主题关键品种缺失/降级（{miss or 'unknown'}）→ 叙事可信度下降")
    if "偏松" in denom_tag and eq_down:
        divergences.append("分母偏松但股市显著走弱 → 警惕分母数据滞后 / 盘中反转")
    if denom_tag == "未确认/横盘" and not dom and stress != "无压制":
        divergences.append("分母未确认 + 主题无共振 + 减震器激活 → 三重未确认，宜防守")
    if denom_tag == "偏紧/压力" and stress != "无压制" and eq_down:
        divergences.append("分母偏紧 + 减震器压制 + 股市走弱 → 三工具共振偏空，防守基调")
    if pressure:
        divergences.append("主题压力覆盖（美元挤兑/套息平仓）置顶 → 压过普通叙事")

    if alignment == "数据降级":
        consistency = f"数据降级（{bias}）"
    elif alignment == "未确认":
        consistency = f"未确认（{bias}）"
    elif alignment == "冲突":
        consistency = f"冲突（{bias}）"
    elif alignment == "共振":
        side = "偏空" if bias == "risk_off" else ("偏多" if bias == "risk_on" else "混合")
        consistency = f"共振（{side}）"
    else:
        consistency = f"部分对齐（{bias}）"

    if alignment == "数据降级":
        tone = (
            "观察基调：数据降级。主题读数 stale/degraded，"
            "综合结论不可当作同日共振；以分母「今天不做什么」为硬约束，等待数据对齐。"
        )
    elif pressure:
        tone = (
            "观察基调：压力覆盖。主题机压力类置顶（美元挤兑/套息平仓）——"
            "叙事让位给流动性压力，控制敞口，勿用普通主题解释硬扛。"
        )
    elif stress != "无压制" and denom_tag != "偏松/确认" and not dom:
        tone = (
            "观察基调：防守。分母未确认/偏紧，减震器已压低科技敞口，主题无共振 ——"
            "不加新表达，等分母翻转或主题共振。"
        )
    elif stress == "无压制" and dom and theme_bias == "risk_on" and alignment == "共振":
        tone = (
            f"观察基调：中性偏积极。减震器放行（预算 {budget}），主题 risk_on 叙事"
            f"（{dom.get('name')}）—— 可关注共振方向，仍以分母状态机为准。"
        )
    elif stress != "无压制" and dom:
        tone = (
            f"观察基调：谨慎。主题有叙事（{dom.get('name')}，{theme_bias}）但减震器仍压制"
            f"（预算 {budget}）—— 控制仓位，等分母确认。"
        )
    else:
        tone = "观察基调：中性。各工具信号未形成共振，按分母状态机「今天不做什么」执行。"

    return {
        "equity_stress": stress,
        "tech_drawdown": tech_dd,
        "risk_budget": budget,
        "dampener_active": damp_active,
        "denom_tag": denom_tag,
        "main_state": main_state,
        "narrative": narrative,
        "equity_down": eq_down,
        "divergences": divergences,
        "tone": tone,
        "consistency": consistency,
        "alignment": alignment,
        "bias": bias,
        "data_quality": data_quality,
        "pressure_override": pressure,
        "theme_quality": tq,
        "dont_do": dont,
        "trigger_hint": trigger,
        "theme_date": theme_date,
        "tech_date": tech_date,
        "denom_date": denom_date,
    }


def _denom_ceiling(state: Optional[str]) -> float:
    """Map a v1.6 denominator state string to an implied risk-budget ceiling."""
    s = state or ""
    if any(k in s for k in ["HARD_VETO", "危机", "CRISIS", "LIQUIDITY_SQUEEZE", "SQUEEZE"]):
        return 0.10
    if any(k in s for k in ["紧缩", "压力", "偏紧", "TRANSITION", "过渡"]):
        return 0.35
    if any(k in s for k in ["未确认", "分裂", "横盘", "混合"]):
        return 0.55
    if any(k in s for k in ["RISK_ON", "risk_on", "宽松", "确认"]):
        return 0.80
    return 0.55


def _theme_ceiling(theme: Optional[Dict]) -> float:
    """Map a theme-state-machine read to an implied risk-budget ceiling."""
    if not theme:
        return 0.60
    if theme.get("pressure_override"):
        return 0.25
    dom = theme.get("dominant_theme") or {}
    rb = (dom.get("risk_bias") or theme.get("risk_bias") or "")
    if rb == "risk_off":
        return 0.45
    if rb == "risk_on":
        return 0.80
    return 0.60


def compute_combined_budget(denom, tech, theme) -> Dict[str, Any]:
    """Single operative risk-budget ceiling = min across the three instruments.

    Each tool imposes its own ceiling; the binding one is the strictest. This is a
    *synthesis reference number*, not a trade instruction — the kernel remains the
    source of truth in live trading.
    """
    denom_c = _denom_ceiling((denom or {}).get("main_state"))
    tech_c = float(((tech or {}).get("decision") or {}).get("risk_budget", 0.8))
    theme_c = _theme_ceiling(theme)
    combined = min(denom_c, tech_c, theme_c)
    binders = []
    for label, val in (("分母", denom_c), ("减震器", tech_c), ("主题", theme_c)):
        if abs(val - combined) < 1e-9:
            binders.append(label)
    return {
        "denominator_ceiling": round(denom_c, 2),
        "tech_ceiling": round(tech_c, 2),
        "theme_ceiling": round(theme_c, 2),
        "combined_budget": round(combined, 2),
        "binding": binders,
    }


def build_report(
    date_str: str,
    denom: Optional[Dict],
    tech: Optional[Dict],
    theme: Optional[Dict],
    synth: Dict[str, Any],
    combined: Optional[Dict[str, Any]] = None,
) -> str:
    L: list = []
    L.append(f"# 每日宏观三件套 · 统一读数 | {date_str}")
    L.append("")
    L.append("- **观察性质**: 三个独立观察工具的综合读数，非交易信号，非投资建议。")
    L.append(
        f"- **数据截至**: 主题 {synth['theme_date']} ｜ 减震器 {synth['tech_date']} ｜ "
        f"分母 {synth['denom_date'] or '未读取'}"
    )
    L.append("")

    L.append("## 一致性矩阵")
    L.append("")
    L.append("| 维度 | 分母状态机 | 科技减震器 | 主题状态机 |")
    L.append("|---|---|---|---|")
    L.append(
        f"| 风险倾向 | {synth['main_state'] or '未读取'} | "
        f"预算 {synth['risk_budget']}（{synth['equity_stress']}） | {synth['narrative']} |"
    )
    L.append(
        f"| alignment | {synth.get('alignment', '—')} | bias={synth.get('bias', '—')} | "
        f"quality={synth.get('data_quality', '—')} |"
    )
    L.append(f"| 综合判定 | {synth['consistency']} | — | — |")
    L.append("")

    L.append("## ① 分母状态机（资金价格斜率 v1.6）")
    L.append("")
    if denom:
        L.append(f"- **主状态**: {denom.get('main_state', '—')}")
        if denom.get("four_quad"):
            L.append(f"- **四象限（官方）**: {denom['four_quad']}")
        if denom.get("dont_do"):
            L.append(f"- **今天不做什么**: {denom['dont_do']}")
        if denom.get("trigger_hint"):
            L.append(f"- **触发提示**: {denom['trigger_hint']}")
        L.append("")
        L.append(f"> 完整读数见 `denominator_state_{synth['denom_date']}.md`")
    else:
        L.append("> 本次未读取分母状态。综合研判仅基于科技减震器 + 主题状态机。")
    L.append("")

    L.append("## ② 科技减震器（Equity-Stress Overlay）")
    L.append("")
    if tech:
        dec = tech.get("decision") or {}
        dd = tech.get("tech_drawdown")
        L.append(
            f"- **SOXX 20日峰值回撤**: "
            f"{('-%.2f%%' % (abs(dd) * 100)) if dd is not None else '—'}"
        )
        L.append(f"- **内核风险预算**: **{synth['risk_budget']}** （{synth['equity_stress']}）")
        L.append(f"- **减震器状态**: {'激活' if synth['dampener_active'] else '未触发'}")
        L.append(
            f"- **权限 / 原因**: `{dec.get('authority', '—')}` / `{dec.get('reason_code', '—')}`"
        )
        L.append("")
        L.append(
            f"> 完整读数见 `tech_dampener_decision_{date_str}.md` + `tech_drawdown_{date_str}.json`"
        )
    else:
        L.append("> 科技减震器未运行。")
    L.append("")

    L.append("## ③ 主题状态机（v3.1r 日频复刻）")
    L.append("")
    if theme:
        dom = theme.get("dominant_theme")
        if dom:
            L.append(f"- **市场主题**: {dom.get('name')}")
            L.append(
                f"- **家族**: {dom.get('family')} ｜ **置信**: "
                f"n3={dom.get('confidence', {}).get('n3')}, "
                f"持续 {dom.get('confidence', {}).get('persist_days')} 日"
            )
            L.append(
                f"- **risk_bias**: {dom.get('risk_bias') or theme.get('risk_bias')} ｜ "
                f"pressure_override="
                f"{'是' if (dom.get('pressure_override') or theme.get('pressure_override')) else '否'}"
            )
        else:
            L.append("- **市场主题**: 无主导主题 · 未形成共振")
            L.append("- **白话解读**: 各品种没有形成一致方向 —— 等待共振，别硬编故事")
        tq = synth.get("theme_quality") or theme.get("quality") or {}
        if tq:
            L.append(
                f"- **主题数据质量**: `{tq.get('data_quality', '—')}`"
                f" ｜ as_of={tq.get('as_of', theme.get('date'))}"
                f" ｜ lag={tq.get('lag_days', '—')}"
            )
        leaders = theme.get("today_leaders") or []
        if leaders:
            lead_str = "  ".join(f"{l['label']}{l['dir']}{l['z']:+.2f}" for l in leaders)
            L.append(f"- **今日主角**: {lead_str}")
        fam = theme.get("families") or {}
        fam_items = [f"{k}: {('无' if not v else v.get('theme'))}" for k, v in fam.items()]
        L.append(f"- **五题材分区**: {' ｜ '.join(fam_items)}")
        L.append("")
        L.append(f"> 完整读数见 `theme_state_machine_{synth['theme_date']}.md`")
    else:
        L.append("> 主题状态机未运行。")
    L.append("")

    L.append("## ④ 综合研判（跨工具交叉验证）")
    L.append("")
    L.append(f"- **一致性**: {synth['consistency']}")
    L.append(f"- **alignment / bias**: {synth.get('alignment', '—')} / {synth.get('bias', '—')}")
    L.append(
        f"- **数据质量**: {synth.get('data_quality', '—')}"
        f"{' ｜ 压力置顶' if synth.get('pressure_override') else ''}"
    )
    L.append(f"- **股市承压信号**: {'是' if synth['equity_down'] else '否'}")
    if synth["divergences"]:
        L.append("- **背离警示**:")
        for d in synth["divergences"]:
            L.append(f"  - ⚠ {d}")
    else:
        L.append("- **背离警示**: 无")
    L.append("")

    # ⑤ Combined risk-budget ceiling (single operative reference number)
    if combined:
        L.append("## ⑤ 合成风险预算上限（行动参考）")
        L.append("")
        L.append(
            f"- **分母上限**: {combined['denominator_ceiling']} ｜ "
            f"**减震器上限**: {combined['tech_ceiling']} ｜ "
            f"**主题上限**: {combined['theme_ceiling']}"
        )
        L.append(
            f"- **合成上限 = min = {combined['combined_budget']}** "
            f"（约束项: {', '.join(combined['binding'])}）"
        )
        L.append(
            "> 合成上限是三工具各自口径下最严格的风险预算天花板，仅供盘前快速定位防线；"
            "实盘仍以 kernel `decide()` 为准。减震器上限已包含分母状态的隐含约束（分母未确认时不会给满 0.8）。"
        )
        L.append("")

    L.append(f"> **{synth['tone']}**")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*本文件由 `scripts/daily_macro_consolidated.py` 自动汇总三个独立观察工具生成。*")
    L.append("*三工具各自为政、互为交叉验证；任何单一工具都不构成交易信号。投资有风险，决策需谨慎。*")
    L.append("")
    return "\n".join(L)


def build_json(
    date_str: str,
    denom: Optional[Dict],
    tech: Optional[Dict],
    theme: Optional[Dict],
    synth: Dict[str, Any],
    combined: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "report_date": date_str,
        "schema": "macro-os.daily-macro-consolidated.v2",
        "as_of": {
            "theme": synth["theme_date"],
            "tech": synth["tech_date"],
            "denominator": synth["denom_date"],
        },
        "synthesis": {
            "consistency": synth["consistency"],
            "alignment": synth.get("alignment"),
            "bias": synth.get("bias"),
            "data_quality": synth.get("data_quality"),
            "pressure_override": synth.get("pressure_override"),
            "equity_down": synth["equity_down"],
            "equity_stress": synth["equity_stress"],
            "risk_budget": synth["risk_budget"],
            "dampener_active": synth["dampener_active"],
            "denom_tag": synth["denom_tag"],
            "narrative": synth["narrative"],
            "divergences": synth["divergences"],
            "tone": synth["tone"],
            "theme_quality": synth.get("theme_quality"),
        },
        "combined_risk_budget": combined,
        "denominator_state": denom,
        "tech_dampener": tech,
        "theme_state_machine": theme,
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Daily Macro Consolidated Report (denominator + tech dampener + theme)"
    )
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="output dir holding the three instruments' artifacts",
    )
    parser.add_argument(
        "--no-run-theme",
        action="store_true",
        help="never run theme script; use newest existing json only",
    )
    parser.add_argument(
        "--no-run-tech",
        action="store_true",
        help="never run tech script; use existing json for --date only",
    )
    parser.add_argument(
        "--no-run-denom",
        action="store_true",
        help="never run denominator port; use newest existing json/md only",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="force re-run theme+tech+denom",
    )
    args = parser.parse_args(argv)
    if args.force_refresh:
        args.no_run_theme = False
        args.no_run_tech = False
        args.no_run_denom = False

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    date_str = args.date

    logger.info("Date: %s | Report dir: %s", date_str, report_dir)

    denom = ensure_denominator(report_dir, date_str, args.no_run_denom)
    denom_date = (denom or {}).get("date")

    tech = ensure_tech(report_dir, date_str, args.no_run_tech)
    tech_date = (tech or {}).get("date", date_str)

    theme = ensure_theme(report_dir, args.no_run_theme, expected_as_of=date_str)
    theme_date = (theme or {}).get("date", date_str)

    synth = synthesize(
        theme, tech, denom, theme_date, tech_date, denom_date, report_date=date_str
    )

    combined = compute_combined_budget(denom, tech, theme)

    md = build_report(date_str, denom, tech, theme, synth, combined)
    payload = build_json(date_str, denom, tech, theme, synth, combined)

    out_md = report_dir / f"daily_macro_{date_str}.md"
    out_json = report_dir / f"daily_macro_{date_str}.json"
    out_md.write_text(md, encoding="utf-8")
    out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info("written: %s", out_md)
    logger.info("written: %s", out_json)

    try:
        print(md)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(md.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
    print(f"\n[written] {out_md}")
    print(f"[written] {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
