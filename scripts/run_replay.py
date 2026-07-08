#!/usr/bin/env python3
"""Macro OS — Replay Engine CLI.

Usage:
    python scripts/run_replay.py
    python scripts/run_replay.py --events vault/EVENTS.log.jsonl --output replay_results/
    python scripts/run_replay.py --validate-only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from config.settings import settings
from core.replay_engine import ReplayEngine, TemporalViolation


def main() -> None:
    parser = argparse.ArgumentParser(description="Macro OS Replay Engine")
    parser.add_argument(
        "--events",
        type=str,
        default=None,
        help="Path to EVENTS.log.jsonl (default: from settings)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="replay_results",
        help="Output directory for results",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate ledger integrity, do not run replay",
    )
    parser.add_argument(
        "--spread-bps",
        type=float,
        default=1.0,
        help="Spread cost in basis points",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=2.0,
        help="Slippage cost in basis points",
    )
    parser.add_argument(
        "--switch-penalty-bps",
        type=float,
        default=5.0,
        help="Regime switch penalty in basis points",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("replay")

    events_path = Path(args.events) if args.events else settings.events_path

    if not events_path.exists():
        logger.error("Events file not found: %s", events_path)
        sys.exit(1)

    if args.validate_only:
        from adapters.vault import VaultAdapter

        vault = VaultAdapter(events_path)
        summary = vault.validate()
        print(f"Ledger: {events_path}")
        print(f"  Events: {summary.total_events}")
        print(f"  Duplicates: {len(summary.duplicate_ids)}")
        print(f"  Schema errors: {len(summary.schema_errors)}")
        print(f"  Valid: {summary.valid}")
        sys.exit(0 if summary.valid else 1)

    config = settings.thresholds.model_dump() if settings.thresholds else {}

    engine = ReplayEngine(
        events_path=events_path,
        config=config,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        switch_penalty_bps=args.switch_penalty_bps,
    )

    try:
        metrics = engine.run()
    except TemporalViolation as e:
        logger.error("REPLAY FAILED: %s", e)
        print(f"\nCRITICAL: Temporal boundary violation detected.\n{e}")
        sys.exit(2)

    if "error" in metrics:
        logger.error("Replay failed: %s", metrics["message"])
        sys.exit(1)

    # Output results
    output_dir = Path(args.output)
    engine.save_results(metrics, output_dir)

    print("\n=== Replay Results ===")
    print(f"\nRegime Confusion Matrix:")
    print(metrics["confusion_matrix"]["table"])

    print(f"\nTransition Accuracy: {metrics['transition_accuracy'] * 100:.1f}%")
    print(f"Stability Score:     {metrics['stability_score']:.4f}")

    pnl = metrics["pnl"]
    print(f"\nPnL:")
    print(f"  Gross PnL:     {pnl['gross_pnl']:+.4f}")
    print(f"  Net PnL:       {pnl['net_pnl']:+.4f}")
    print(f"  Sharpe:        {pnl['sharpe']:.4f}")
    print(f"  Total Costs:   {pnl['total_costs_bps']:.2f} bps")
    print(f"  Trade Count:   {pnl['trade_count']}")

    print(f"\nResults saved to: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
