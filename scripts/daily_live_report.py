#!/usr/bin/env python3
"""Macro OS v5.0 -- Daily Live Review Report Generator (lightweight).

Reads the day's ledger / events / replay data, optionally invokes the
existing ReplayEngine and Alpha-Report pipeline, and produces:

  - docs/research/<date>-daily-live-review.md   (human-readable)
  - docs/research/<date>-daily-live-review.json  (machine-readable)

Design principles (budget mode):
  * Reuse scripts/run_replay.py  -> core.replay_engine.ReplayEngine
  * Reuse scripts/generate_alpha_report.py -> load_ledger + generate_report
  * Reference scripts/backtest_loop.py -> MacroBacktestEngine trace shape
  * NO changes to runtime/orchestrator.py, core/decision_kernel.py, etc.
  * NO mock / database / Feishu / review-chain dependencies
  * Graceful degradation: if a subsystem import or run fails, the report
    is still produced with whatever data is available.

Usage:
    python scripts/daily_live_report.py --date 2026-07-17
    python scripts/daily_live_report.py --date 2026-07-17 --dry-run
    python scripts/daily_live_report.py --date 2026-07-17 --events vault/EVENTS.log.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# -- project root on sys.path (same pattern as sibling scripts) ---------------
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("macro-os.daily-live-report")

# -- output directory (fixed, per acceptance criteria) ------------------------
OUTPUT_DIR = ROOT / "docs" / "research"

# ---------------------------------------------------------------------------
# 1.  Data loaders (vault events + decision journal)
# ---------------------------------------------------------------------------

def _parse_date(date_str: str) -> str:
    """Validate YYYY-MM-DD and return as-is."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid date format: {date_str!r} (expected YYYY-MM-DD)"
        )
    return date_str


def _matches_date(ts: str, date_str: str) -> bool:
    """Check whether an ISO timestamp falls on *date_str* (YYYY-MM-DD)."""
    if not ts:
        return False
    # Normalise: strip Z suffix, handle both 'T' and space separators
    normalised = ts.replace("Z", "+00:00").replace(" ", "T")
    try:
        dt = datetime.fromisoformat(normalised)
    except (ValueError, TypeError):
        # Fallback: string-prefix match
        return ts.startswith(date_str)
    return dt.strftime("%Y-%m-%d") == date_str


def load_events_by_date(events_path: Path, date_str: str) -> List[Dict[str, Any]]:
    """Load JSONL events and filter to the target date.

    Uses raw json.loads to avoid hard dependency on pydantic Event schema
    at the loader level (graceful if schema changes).
    """
    if not events_path.exists():
        logger.warning("Events file not found: %s", events_path)
        return []

    events: List[Dict[str, Any]] = []
    with open(events_path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Skip invalid JSON at line %d", line_no)
                continue
            ts = evt.get("ts", "")
            if _matches_date(ts, date_str):
                events.append(evt)
    return events


def load_journal_by_date(journal_path: Path, date_str: str) -> List[Dict[str, Any]]:
    """Load decision journal JSONL and filter to the target date."""
    if not journal_path.exists():
        logger.warning("Journal file not found: %s", journal_path)
        return []

    entries: List[Dict[str, Any]] = []
    with open(journal_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = entry.get("ts") or entry.get("time") or ""
            if _matches_date(ts, date_str):
                entries.append(entry)
    return entries


def load_all_events(events_path: Path) -> List[Dict[str, Any]]:
    """Load all events from JSONL (for replay context)."""
    if not events_path.exists():
        return []
    events: List[Dict[str, Any]] = []
    with open(events_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


# ---------------------------------------------------------------------------
# 2.  Replay integration (reuse core.replay_engine)
# ---------------------------------------------------------------------------

def try_run_replay(
    events_path: Path,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Attempt to run the ReplayEngine on the full event stream.

    Returns metrics dict on success, None on failure (graceful skip).
    """
    try:
        from config.settings import settings
        from core.replay_engine import ReplayEngine, TemporalViolation

        cfg = config
        if cfg is None:
            cfg = settings.thresholds.model_dump() if settings.thresholds else {}

        engine = ReplayEngine(
            events_path=events_path,
            config=cfg,
            spread_bps=1.0,
            slippage_bps=2.0,
            switch_penalty_bps=5.0,
        )
        metrics = engine.run()
        if "error" in metrics:
            logger.info("Replay engine returned error: %s", metrics.get("message"))
            return None
        return metrics
    except TemporalViolation as e:
        logger.warning("Replay temporal violation: %s", e)
        return None
    except Exception as e:
        logger.warning("Replay engine unavailable: %s", e)
        logger.debug("Traceback: %s", traceback.format_exc())
        return None


# ---------------------------------------------------------------------------
# 3.  Alpha report integration (reuse scripts.generate_alpha_report)
# ---------------------------------------------------------------------------

def try_run_alpha_report(ledger_path: Path) -> Optional[Dict[str, Any]]:
    """Attempt to run the alpha attribution report from a ledger file.

    Returns a serialisable summary dict on success, None on failure.
    """
    if not ledger_path.exists():
        logger.info("Ledger file not found: %s", ledger_path)
        return None

    try:
        # Import from the sibling script (same package-level path)
        from scripts.generate_alpha_report import generate_report

        report = generate_report(
            ledger_filepath=str(ledger_path),
            market_data=None,
            symbol="DAILY_REVIEW",
            bars=200,
            seed=42,
            fixed_horizon=20,
        )

        return {
            "sample_size": report.sample_size,
            "avg_return": round(report.avg_return, 6) if report.avg_return else 0.0,
            "win_rate": round(report.win_rate, 4) if report.win_rate else 0.0,
            "top_factors": report.top_factors[:5] if report.top_factors else [],
            "top_interactions": [
                {
                    "factor_a": ie.factor_a,
                    "factor_b": ie.factor_b,
                    "interaction": round(ie.interaction, 6),
                    "interpretation": ie.interpretation,
                }
                for ie in (report.top_interactions or [])
            ][:3],
            "recommendations": report.recommendations[:5] if report.recommendations else [],
        }
    except Exception as e:
        logger.warning("Alpha report unavailable: %s", e)
        logger.debug("Traceback: %s", traceback.format_exc())
        return None


# ---------------------------------------------------------------------------
# 4.  Daily summary builder
# ---------------------------------------------------------------------------

def build_daily_summary(
    date_str: str,
    day_events: List[Dict[str, Any]],
    day_journal: List[Dict[str, Any]],
    all_events_count: int,
    replay_metrics: Optional[Dict[str, Any]],
    alpha_report: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Assemble the structured JSON payload for the daily report."""

    # -- event type breakdown -----------------------------------------------
    event_types: Dict[str, int] = {}
    for evt in day_events:
        et = evt.get("event_type", "UNKNOWN")
        event_types[et] = event_types.get(et, 0) + 1

    # -- decision actions ----------------------------------------------------
    decisions: List[Dict[str, Any]] = []
    for evt in day_events:
        if evt.get("event_type") == "DECISION":
            payload = evt.get("payload", {})
            decisions.append({
                "ts": evt.get("ts", ""),
                "action": payload.get("action", "N/A"),
                "regime": payload.get("regime", "N/A"),
                "risk_score": payload.get("risk_score"),
                "confidence": payload.get("confidence"),
                "reason": payload.get("reason", ""),
                "source": evt.get("source", ""),
            })

    # -- journal entries -----------------------------------------------------
    journal_summary: List[Dict[str, Any]] = []
    for entry in day_journal:
        journal_summary.append({
            "ts": entry.get("ts") or entry.get("time", ""),
            "action": entry.get("action", "N/A"),
            "budget": entry.get("budget"),
            "composite_regime": entry.get("composite_regime", "N/A"),
            "danger_regime": entry.get("danger_regime", "N/A"),
            "danger_score": entry.get("danger_score"),
            "qqq": entry.get("qqq"),
            "vix": entry.get("vix"),
            "dxy": entry.get("dxy"),
        })

    # -- replay extract ------------------------------------------------------
    replay_extract: Optional[Dict[str, Any]] = None
    if replay_metrics:
        pnl = replay_metrics.get("pnl", {})
        cm = replay_metrics.get("confusion_matrix", {})
        replay_extract = {
            "transition_accuracy": replay_metrics.get("transition_accuracy"),
            "stability_score": replay_metrics.get("stability_score"),
            "gross_pnl": pnl.get("gross_pnl"),
            "net_pnl": pnl.get("net_pnl"),
            "sharpe": pnl.get("sharpe"),
            "total_costs_bps": pnl.get("total_costs_bps"),
            "trade_count": pnl.get("trade_count"),
            "confusion_accuracy": cm.get("accuracy"),
        }

    # -- feature snapshot (last event of the day with features) -------------
    feature_snapshot: Dict[str, Any] = {}
    for evt in reversed(day_events):
        payload = evt.get("payload", {})
        feats = payload.get("features")
        if feats and isinstance(feats, dict):
            # Strip internal keys
            feature_snapshot = {
                k: v for k, v in feats.items()
                if not k.startswith("_")
            }
            break

    return {
        "report_date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "schema": "macro-os.daily-live-review.v1",
        "data_coverage": {
            "events_on_date": len(day_events),
            "journal_entries_on_date": len(day_journal),
            "total_events_in_vault": all_events_count,
            "event_type_breakdown": event_types,
        },
        "decisions": decisions,
        "journal_summary": journal_summary,
        "feature_snapshot": feature_snapshot,
        "replay_metrics": replay_extract,
        "alpha_report": alpha_report,
    }


# ---------------------------------------------------------------------------
# 5.  Markdown renderer
# ---------------------------------------------------------------------------

def render_markdown(summary: Dict[str, Any]) -> str:
    """Render the daily summary as a Markdown document."""
    date_str = summary["report_date"]
    gen_ts = summary["generated_at"]
    coverage = summary["data_coverage"]

    lines: List[str] = []
    lines.append(f"# 每日实盘回测分析日报 -- {date_str}")
    lines.append("")
    lines.append(f"- **报告日期**: {date_str}")
    lines.append(f"- **生成时间**: {gen_ts}")
    lines.append(f"- **Schema**: `{summary['schema']}`")
    lines.append("")

    # -- Data coverage -------------------------------------------------------
    lines.append("## 数据覆盖")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 当日事件数 | {coverage['events_on_date']} |")
    lines.append(f"| 当日日志条目 | {coverage['journal_entries_on_date']} |")
    lines.append(f"| Vault 总事件数 | {coverage['total_events_in_vault']} |")
    breakdown = coverage.get("event_type_breakdown", {})
    if breakdown:
        bd_str = ", ".join(f"{k}: {v}" for k, v in sorted(breakdown.items()))
        lines.append(f"| 事件类型分布 | {bd_str} |")
    lines.append("")

    # -- Decisions -----------------------------------------------------------
    decisions = summary.get("decisions", [])
    lines.append("## 当日决策")
    lines.append("")
    if not decisions:
        lines.append("> 当日无 DECISION 事件。")
    else:
        lines.append("| 时间 | Action | Regime | Risk Score | Confidence | Source |")
        lines.append("|------|---------|--------|------------|------------|--------|")
        for d in decisions:
            lines.append(
                f"| {d['ts']} | {d['action']} | {d['regime']} | "
                f"{d['risk_score'] if d['risk_score'] is not None else '-'} | "
                f"{d['confidence'] if d['confidence'] is not None else '-'} | "
                f"{d['source']} |"
            )
    lines.append("")

    # -- Journal summary -----------------------------------------------------
    journal = summary.get("journal_summary", [])
    lines.append("## 决策日志摘要")
    lines.append("")
    if not journal:
        lines.append("> 当日无 Decision Journal 条目。")
    else:
        lines.append("| 时间 | Action | Budget | Composite Regime | Danger Regime | Danger Score | QQQ | VIX | DXY |")
        lines.append("|------|---------|--------|------------------|---------------|--------------|-----|-----|-----|")
        for j in journal:
            lines.append(
                f"| {j['ts']} | {j['action']} | "
                f"{j['budget'] if j['budget'] is not None else '-'} | "
                f"{j['composite_regime']} | {j['danger_regime']} | "
                f"{j['danger_score'] if j['danger_score'] is not None else '-'} | "
                f"{j['qqq'] if j['qqq'] is not None else '-'} | "
                f"{j['vix'] if j['vix'] is not None else '-'} | "
                f"{j['dxy'] if j['dxy'] is not None else '-'} |"
            )
    lines.append("")

    # -- Feature snapshot ----------------------------------------------------
    feats = summary.get("feature_snapshot", {})
    lines.append("## 宏观特征快照")
    lines.append("")
    if not feats:
        lines.append("> 当日无特征数据。")
    else:
        lines.append("| 特征 | 值 |")
        lines.append("|------|-----|")
        for k, v in sorted(feats.items()):
            val = f"{v:.4f}" if isinstance(v, float) else str(v)
            lines.append(f"| {k} | {val} |")
    lines.append("")

    # -- Replay metrics ------------------------------------------------------
    replay = summary.get("replay_metrics")
    lines.append("## 回测引擎指标 (ReplayEngine)")
    lines.append("")
    if not replay:
        lines.append("> ReplayEngine 未运行或无可用数据。")
    else:
        lines.append("| 指标 | 值 |")
        lines.append("|------|-----|")
        ta = replay.get("transition_accuracy")
        lines.append(f"| Transition Accuracy | {ta*100:.1f}% |" if ta is not None else "| Transition Accuracy | - |")
        ss = replay.get("stability_score")
        lines.append(f"| Stability Score | {ss:.4f} |" if ss is not None else "| Stability Score | - |")
        gp = replay.get("gross_pnl")
        lines.append(f"| Gross PnL | {gp:+.4f} |" if gp is not None else "| Gross PnL | - |")
        np_ = replay.get("net_pnl")
        lines.append(f"| Net PnL | {np_:+.4f} |" if np_ is not None else "| Net PnL | - |")
        sh = replay.get("sharpe")
        lines.append(f"| Sharpe | {sh:.4f} |" if sh is not None else "| Sharpe | - |")
        tc = replay.get("total_costs_bps")
        lines.append(f"| Total Costs | {tc:.2f} bps |" if tc is not None else "| Total Costs | - |")
        tcount = replay.get("trade_count")
        lines.append(f"| Trade Count | {tcount} |" if tcount is not None else "| Trade Count | - |")
        ca = replay.get("confusion_accuracy")
        lines.append(f"| Confusion Accuracy | {ca*100:.1f}% |" if ca is not None else "| Confusion Accuracy | - |")
        lines.append("")
        lines.append("> 注: ReplayEngine 在全量事件流上运行, 指标为累计值而非当日单独值。")
    lines.append("")

    # -- Alpha report --------------------------------------------------------
    alpha = summary.get("alpha_report")
    lines.append("## Alpha 归因报告")
    lines.append("")
    if not alpha:
        lines.append("> Alpha 归因报告不可用 (无 ledger 文件或管道异常)。")
    else:
        lines.append(f"- **样本数**: {alpha.get('sample_size', 0)}")
        ar = alpha.get("avg_return", 0)
        lines.append(f"- **平均收益**: {ar:+.4%}" if ar else f"- **平均收益**: {ar}")
        wr = alpha.get("win_rate", 0)
        lines.append(f"- **胜率**: {wr:.2%}" if wr else f"- **胜率**: {wr}")
        lines.append("")

        top_factors = alpha.get("top_factors", [])
        if top_factors:
            lines.append("### 因子 Alpha 排名")
            lines.append("")
            lines.append("| 因子 | Alpha |")
            lines.append("|------|-------|")
            for fname, alpha_val in top_factors:
                av = f"{alpha_val:+.4f}" if isinstance(alpha_val, (int, float)) else str(alpha_val)
                lines.append(f"| {fname} | {av} |")
            lines.append("")

        top_interactions = alpha.get("top_interactions", [])
        if top_interactions:
            lines.append("### 交互效应")
            lines.append("")
            for ie in top_interactions:
                lines.append(f"- **{ie['factor_a']} x {ie['factor_b']}**: {ie['interaction']:+.4f} -- {ie['interpretation']}")
            lines.append("")

        recommendations = alpha.get("recommendations", [])
        if recommendations:
            lines.append("### 权重优化建议")
            lines.append("")
            for i, rec in enumerate(recommendations, 1):
                lines.append(f"{i}. {rec}")
            lines.append("")

    # -- Footer --------------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append("本文件由 `scripts/daily_live_report.py` 自动生成。")
    lines.append("回测结果为模型驱动, 不构成任何交易信号。")
    lines.append("")
    lines.append("> WARNING: 以上内容由 AI 基于公开信息整理生成, 仅供参考, 不构成任何投资建议或个股推荐。投资有风险, 决策需谨慎。")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 6.  CLI entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Macro OS -- Daily Live Review Report Generator",
    )
    parser.add_argument(
        "--date",
        required=True,
        type=_parse_date,
        help="Target trading date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--events",
        type=str,
        default=None,
        help="Path to EVENTS.log.jsonl (default: from settings / vault)",
    )
    parser.add_argument(
        "--journal",
        type=str,
        default=None,
        help="Path to DECISION_JOURNAL.jsonl (default: vault/DECISION_JOURNAL.jsonl)",
    )
    parser.add_argument(
        "--ledger",
        type=str,
        default=None,
        help="Path to ledger JSON for alpha report (default: data/ledger.json -> data/ledger.sample.json)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(OUTPUT_DIR),
        help="Output directory (default: docs/research)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run all logic but do not write files; print summary to stdout",
    )
    parser.add_argument(
        "--skip-replay",
        action="store_true",
        help="Skip ReplayEngine (faster, less context)",
    )
    parser.add_argument(
        "--skip-alpha",
        action="store_true",
        help="Skip alpha attribution report",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    date_str = args.date

    # -- Resolve paths -------------------------------------------------------
    # Events path: CLI > settings > default vault
    if args.events:
        events_path = Path(args.events)
    else:
        try:
            from config.settings import settings
            events_path = settings.events_path
        except Exception:
            events_path = ROOT / "vault" / "EVENTS.log.jsonl"

    # Journal path
    if args.journal:
        journal_path = Path(args.journal)
    else:
        journal_path = ROOT / "vault" / "DECISION_JOURNAL.jsonl"

    # Ledger path: CLI > data/ledger.json > data/ledger.sample.json
    if args.ledger:
        ledger_path = Path(args.ledger)
    else:
        ledger_path = ROOT / "data" / "ledger.json"
        if not ledger_path.exists():
            sample = ROOT / "data" / "ledger.sample.json"
            if sample.exists():
                ledger_path = sample

    output_dir = Path(args.output_dir)
    md_path = output_dir / f"{date_str}-daily-live-review.md"
    json_path = output_dir / f"{date_str}-daily-live-review.json"

    logger.info("Date:       %s", date_str)
    logger.info("Events:     %s", events_path)
    logger.info("Journal:    %s", journal_path)
    logger.info("Ledger:     %s", ledger_path)
    logger.info("Output MD:  %s", md_path)
    logger.info("Output JSON:%s", json_path)

    # -- Load day-specific data ----------------------------------------------
    day_events = load_events_by_date(events_path, date_str)
    day_journal = load_journal_by_date(journal_path, date_str)
    all_events = load_all_events(events_path)
    all_count = len(all_events)

    logger.info(
        "Day events: %d | Journal entries: %d | Total events: %d",
        len(day_events), len(day_journal), all_count,
    )

    # -- Replay (optional) ---------------------------------------------------
    replay_metrics: Optional[Dict[str, Any]] = None
    if not args.skip_replay and events_path.exists():
        logger.info("Running ReplayEngine...")
        replay_metrics = try_run_replay(events_path)
        if replay_metrics:
            logger.info("Replay completed: Sharpe=%.2f", replay_metrics.get("pnl", {}).get("sharpe", 0))
        else:
            logger.info("Replay skipped or failed.")
    else:
        logger.info("Replay skipped (--skip-replay or events file missing).")

    # -- Alpha report (optional) --------------------------------------------
    alpha_report: Optional[Dict[str, Any]] = None
    if not args.skip_alpha and ledger_path.exists():
        logger.info("Running alpha report from ledger: %s", ledger_path)
        alpha_report = try_run_alpha_report(ledger_path)
        if alpha_report:
            logger.info("Alpha report completed: samples=%d", alpha_report.get("sample_size", 0))
        else:
            logger.info("Alpha report skipped or failed.")
    else:
        logger.info("Alpha report skipped (--skip-alpha or ledger file missing).")

    # -- Build summary -------------------------------------------------------
    summary = build_daily_summary(
        date_str=date_str,
        day_events=day_events,
        day_journal=day_journal,
        all_events_count=all_count,
        replay_metrics=replay_metrics,
        alpha_report=alpha_report,
    )

    # -- Render markdown -----------------------------------------------------
    markdown = render_markdown(summary)

    # -- Output --------------------------------------------------------------
    if args.dry_run:
        print("\n" + "=" * 70)
        print(f"  DRY RUN -- Daily Live Review for {date_str}")
        print("=" * 70)
        print(f"\n  Events on date:      {len(day_events)}")
        print(f"  Journal entries:     {len(day_journal)}")
        print(f"  Total vault events:  {all_count}")
        print(f"  Replay available:    {'yes' if replay_metrics else 'no'}")
        print(f"  Alpha report avail:  {'yes' if alpha_report else 'no'}")
        print(f"\n  Output MD would be:  {md_path}")
        print(f"  Output JSON would be:{json_path}")
        print("\n" + "-" * 70)
        print("  Markdown preview (first 40 lines):")
        print("-" * 70)
        for line in markdown.split("\n")[:40]:
            print(f"  {line}")
        if len(markdown.split("\n")) > 40:
            print("  ... (truncated)")
        print("\n" + "=" * 70)
        return 0

    # Write files
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    logger.info("Markdown written: %s", md_path)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    logger.info("JSON written: %s", json_path)

    print(f"\n=== Daily Live Review for {date_str} ===")
    print(f"  Markdown: {md_path}")
    print(f"  JSON:     {json_path}")
    print(f"  Events:   {len(day_events)} on date / {all_count} total")
    print(f"  Replay:   {'available' if replay_metrics else 'n/a'}")
    print(f"  Alpha:    {'available' if alpha_report else 'n/a'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
