#!/usr/bin/env python
"""Phase 0 CLI: CN tech sentiment shadow observation card.

Examples:
  python -m scripts.run_sentiment_shadow --scenario 2026-07-21
  python -m scripts.run_sentiment_shadow --scenario 2026-07-17 --previous-scenario 2026-07-17
  python -m scripts.run_sentiment_shadow --as-of 2026-07-21 --session MORNING --print-json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        # minimal fallback parser not attempted; empty config
        return {}


def _persist(path: Path, snap_dict: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snap_dict, ensure_ascii=False) + "\n")


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write((text + "\n").encode(enc, errors="replace"))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CN tech sentiment shadow (observation only)")
    parser.add_argument("--config", default=str(ROOT / "config" / "sentiment_shadow.yaml"))
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD")
    parser.add_argument("--session", default="MORNING")
    parser.add_argument(
        "--scenario",
        default=None,
        help="Built-in mock scenario date, e.g. 2026-07-17 / 2026-07-21",
    )
    parser.add_argument(
        "--previous-scenario",
        default=None,
        help="Previous scenario for delta row on Feishu card",
    )
    parser.add_argument("--manual-json", default=None, help="Path to manual input JSON")
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument(
        "--send-feishu",
        action="store_true",
        help="Actually call FeishuAdapter if webhook configured",
    )
    args = parser.parse_args(argv)

    from adapters.sentiment.providers import ManualJsonProvider, MockScenarioProvider
    from core.sentiment.engine import SentimentShadowEngine
    from core.sentiment.feishu_card import render_observation_card

    cfg = _load_yaml(Path(args.config))
    engine = SentimentShadowEngine(cfg)

    as_of = args.as_of or args.scenario or "2026-07-21"
    session = args.session

    if args.manual_json:
        provider = ManualJsonProvider(args.manual_json)
        raw = provider.fetch(as_of, session)
    else:
        provider = MockScenarioProvider()
        raw = provider.fetch(as_of if not args.scenario else args.scenario, session)

    snap = engine.compute(raw)

    prev_snap = None
    if args.previous_scenario:
        prev_raw = MockScenarioProvider().fetch(args.previous_scenario, session)
        prev_snap = engine.compute(prev_raw)
    elif args.scenario == "2026-07-21":
        # default delta vs 07-17 golden sample
        prev_snap = engine.compute(MockScenarioProvider().fetch("2026-07-17", "AFTERNOON"))

    title, body = render_observation_card(snap, previous=prev_snap)
    _safe_print(title)
    _safe_print("")
    _safe_print(body)

    if args.print_json:
        _safe_print("")
        _safe_print(json.dumps(snap.to_dict(), ensure_ascii=False, indent=2))

    if cfg.get("persist_jsonl", True) and not args.no_persist:
        out = Path(cfg.get("jsonl_path") or (ROOT / "vault/shadow/sentiment_shadow.jsonl"))
        if not out.is_absolute():
            out = ROOT / out
        _persist(out, snap.to_dict())

    if args.send_feishu:
        try:
            from adapters.feishu import FeishuAdapter
            import os

            url = os.environ.get("MACRO_OS_FEISHU_OBSERVATION_WEBHOOK_URL") or os.environ.get(
                "MACRO_OS_FEISHU_WEBHOOK_URL"
            )
            FeishuAdapter(webhook_url=url).send_message(title, body)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] feishu send failed: {exc}", file=sys.stderr)
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

