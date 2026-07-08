#!/usr/bin/env python3
"""Validate Macro OS configuration files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from core.config_validation import load_yaml, validate_macro_configuration


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Macro OS thresholds and watchlist YAML files"
    )
    parser.add_argument(
        "--thresholds",
        type=str,
        default=None,
        help="Path to thresholds.yaml (default: config/thresholds.yaml)",
    )
    parser.add_argument(
        "--watchlist",
        type=str,
        default=None,
        help="Path to watchlist.yaml (default: config/watchlist.yaml)",
    )
    args = parser.parse_args()

    config_dir = _project_root / "config"
    thresholds_path = Path(args.thresholds) if args.thresholds else config_dir / "thresholds.yaml"
    watchlist_path = Path(args.watchlist) if args.watchlist else config_dir / "watchlist.yaml"

    try:
        thresholds = load_yaml(thresholds_path)
        watchlist = load_yaml(watchlist_path)
        validate_macro_configuration(thresholds, watchlist)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    weights = thresholds.get("scoring", {}).get("weights", {})
    weight_total = sum(float(weights.get(name, 0.0)) for name in ("regime_base", "trend_strength", "volatility_adjust", "liquidity_adjust"))
    asset_count = len(watchlist.get("assets", {}))

    print(f"[OK] thresholds: {thresholds_path}")
    print(f"[OK] watchlist:  {watchlist_path}")
    print(f"  scoring.weights total = {weight_total:.6f}")
    print(f"  assets validated      = {asset_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
