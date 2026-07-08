#!/usr/bin/env python3
"""Macro OS — Ledger validation script.

Validates EVENTS.log.jsonl for:
- JSONL format integrity
- event_id uniqueness (no duplicates)
- Schema compliance using Pydantic models

Usage:
    python scripts/validate_ledger.py
    python scripts/validate_ledger.py --path /path/to/EVENTS.log.jsonl
    python scripts/validate_ledger.py --fix   (flag duplicates, not delete)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from config.settings import settings
from adapters.vault import VaultAdapter


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate Macro OS event ledger integrity"
    )
    parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Path to EVENTS.log.jsonl (default: from settings)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Flag duplicate events (does not delete, only annotates)",
    )
    args = parser.parse_args()

    events_path = Path(args.path) if args.path else settings.events_path

    if not events_path.exists():
        print(f"Ledger not found: {events_path}")
        sys.exit(1)

    vault = VaultAdapter(events_path)
    summary = vault.validate()

    print(f"Event Ledger: {events_path}")
    print(f"  Total events:    {summary.total_events}")
    print(f"  Unique events:   {summary.unique_events}")

    if summary.duplicate_ids:
        print(f"  Duplicates:      {len(summary.duplicate_ids)}")
        for dup_id in summary.duplicate_ids:
            print(f"    - {dup_id}")
    else:
        print(f"  Duplicates:      0 OK")

    if summary.schema_errors:
        print(f"  Schema errors:   {len(summary.schema_errors)}")
        for err in summary.schema_errors:
            print(f"    Line {err['line']}: {err['error']}")
    else:
        print(f"  Schema errors:   0 OK")

    if summary.valid:
        print(f"\n[OK] Ledger is valid")
        sys.exit(0)
    else:
        print(f"\nFAIL Ledger has issues")
        sys.exit(1)


if __name__ == "__main__":
    main()
