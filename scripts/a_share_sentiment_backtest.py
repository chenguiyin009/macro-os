#!/usr/bin/env python
"""A-share sentiment shadow backtest — Workbuddy side (MCP data + Phase0 engine).

遵循 handoff `workbuddy_sentiment_backtest_handoff.md`：
- 引擎腿已在（`SentimentShadowEngine`）；本脚本只做「MCP 录制数据 → 回放 → 描述统计」。
- 不修改 Kernel、不自动交易。
- 缺字段由 engine 判为 PARTIAL，绝不在本脚本用 0 假充。

最小路径示例：
  python -m scripts.a_share_sentiment_backtest --as-of 2026-07-21 --provider mcp --diff-mock
  python -m scripts.a_share_sentiment_backtest --start 2026-07-17 --end 2026-07-21 --provider mcp
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "output"


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _trading_days(start: date, end: date) -> List[date]:
    out: List[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri
            out.append(d)
        d += timedelta(days=1)
    return out


def _diff_snaps(live: Dict[str, Any], mock: Dict[str, Any]) -> Dict[str, Any]:
    """Compare LIVE vs MOCK snapshot dicts (golden-sample diff)."""
    live_flags = set(live.get("corroboration_flags", []))
    mock_flags = set(mock.get("corroboration_flags", []))
    return {
        "stage_live": live.get("cycle", {}).get("stage"),
        "stage_mock": mock.get("cycle", {}).get("stage"),
        "rebound_live": live.get("cycle", {}).get("rebound_type"),
        "rebound_mock": mock.get("cycle", {}).get("rebound_type"),
        "quality_live": live.get("quality"),
        "quality_mock": mock.get("quality"),
        "release_rate_live": live.get("deleveraging_release_rate"),
        "release_rate_mock": mock.get("deleveraging_release_rate"),
        "transmission_live": live.get("liquidity_transmission_stage"),
        "transmission_mock": mock.get("liquidity_transmission_stage"),
        "intervention_live": live.get("intervention_mode"),
        "intervention_mock": mock.get("intervention_mode"),
        "confidence_live": live.get("confidence"),
        "confidence_mock": mock.get("confidence"),
        "flags_only_live": sorted(live_flags - mock_flags),
        "flags_only_mock": sorted(mock_flags - live_flags),
        "flags_common": sorted(live_flags & mock_flags),
    }


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write((text + "\n").encode(enc, errors="replace"))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CN tech sentiment shadow backtest (observation only)")
    parser.add_argument("--config", default=str(ROOT / "config" / "sentiment_shadow.yaml"))
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD single-day LIVE replay")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD range start")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD range end")
    parser.add_argument("--session", default="CLOSE")
    parser.add_argument("--provider", default="mcp", choices=["mcp", "mock", "manual"])
    parser.add_argument("--manual-json", default=None, help="path for --provider manual")
    parser.add_argument("--raw-dir", default=None, help="override McpSentimentProvider raw dir")
    parser.add_argument("--diff-mock", action="store_true", help="single-day: also compute MOCK and diff")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args(argv)

    from adapters.sentiment.mcp_provider import McpSentimentProvider
    from adapters.sentiment.providers import ManualJsonProvider, MockScenarioProvider
    from core.sentiment.engine import SentimentShadowEngine

    cfg = _load_yaml(Path(args.config))
    engine = SentimentShadowEngine(cfg)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def provider_for():
        if args.provider == "mcp":
            return McpSentimentProvider(raw_dir=args.raw_dir)
        if args.provider == "manual":
            assert args.manual_json, "--manual-json required"
            return ManualJsonProvider(args.manual_json)
        return MockScenarioProvider()

    # ---- single-day mode ----
    if args.as_of:
        as_of = args.as_of
        if args.provider == "mcp":
            try:
                raw = McpSentimentProvider(raw_dir=args.raw_dir).fetch(as_of, args.session)
            except FileNotFoundError as e:
                _safe_print(f"[error] no recorded MCP raw for {as_of}: {e}")
                return 2
        elif args.provider == "manual":
            raw = ManualJsonProvider(args.manual_json).fetch(as_of, args.session)
        else:
            raw = MockScenarioProvider().fetch(as_of, args.session)

        snap = engine.compute(raw)
        snap_d = snap.to_dict()

        _safe_print(f"== LIVE replay {as_of} ({args.session}) ==")
        _safe_print(json.dumps(snap_d, ensure_ascii=False, indent=2))

        if args.diff_mock:
            mock_raw = MockScenarioProvider().fetch(as_of, args.session)
            mock_snap = engine.compute(mock_raw)
            diff = _diff_snaps(snap_d, mock_snap.to_dict())
            _safe_print("\n== LIVE vs MOCK diff ==")
            _safe_print(json.dumps(diff, ensure_ascii=False, indent=2))
            out = out_dir / f"sentiment_live_mock_diff_{as_of}.json"
            out.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
            _safe_print(f"\n[written] {out}")

        out = out_dir / f"sentiment_shadow_{as_of}.json"
        out.write_text(json.dumps(snap_d, ensure_ascii=False, indent=2), encoding="utf-8")
        _safe_print(f"[written] {out}")
        return 0

    # ---- range replay mode ----
    assert args.start and args.end, "range mode needs --start and --end"
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    days = _trading_days(start, end)

    provider = provider_for()
    rows: List[Dict[str, Any]] = []
    missing: List[str] = []
    for d in days:
        ds = d.isoformat()
        try:
            raw = provider.fetch(ds, args.session)
        except FileNotFoundError:
            missing.append(ds)
            continue
        snap = engine.compute(raw)
        rows.append(snap.to_dict())

    jsonl = out_dir / f"sentiment_shadow_{args.start}_{args.end}.jsonl"
    with jsonl.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ---- markdown report ----
    report = _build_report(rows, missing, args.start, args.end)
    md = out_dir / f"sentiment_backtest_report_{args.end}.md"
    md.write_text(report, encoding="utf-8")
    _safe_print(f"[written] {jsonl}")
    _safe_print(f"[written] {md}")
    if missing:
        _safe_print(f"[warn] missing MCP raw for {len(missing)} days: {missing[:10]}{'...' if len(missing) > 10 else ''}")
    return 0


def _build_report(rows: List[Dict[str, Any]], missing: List[str], start: str, end: str) -> str:
    L: List[str] = []
    L.append(f"# A股情绪 Shadow 回测报告（{start} ~ {end}）\n")
    L.append("> 观测层描述统计，不改 Kernel、不自动交易。\n")

    if not rows:
        L.append("无可用 LIVE 录制数据（全部缺失）。请先用 MCP 录制 `vault/shadow/mcp_raw/{date}.json`。\n")
        if missing:
            L.append(f"缺失日期（{len(missing)}）：{', '.join(missing[:20])}\n")
        return "\n".join(L)

    # stage distribution
    from collections import Counter

    stage_c = Counter(r["cycle"]["stage"] for r in rows)
    L.append("## 1. 周期阶段分布")
    L.append("| 阶段 | 天数 |")
    L.append("|---|---|")
    for s, n in stage_c.most_common():
        L.append(f"| {s} | {n} |")
    L.append("")

    # daily table
    L.append("## 2. 每日快照")
    L.append("| 日期 | 阶段 | 质量 | 释放率 | 传导 | 干预 | 反弹 | 置信 | flags数 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        c = r["cycle"]
        L.append(
            f"| {r['as_of']} | {c['stage']} | {r['quality']} | "
            f"{_fmt(r['deleveraging_release_rate'])} | {r['liquidity_transmission_stage']} | "
            f"{r['intervention_mode']} | {c['rebound_type']} | {_fmt(r['confidence'])} | "
            f"{len(r['corroboration_flags'])} |"
        )
    L.append("")

    # flag frequency
    flag_c: Counter = Counter()
    for r in rows:
        flag_c.update(r["corroboration_flags"])
    L.append("## 3. 关键 flag 频次")
    L.append("| flag | 次数 |")
    L.append("|---|---|")
    for fl, n in flag_c.most_common():
        L.append(f"| {fl} | {n} |")
    L.append("")

    if missing:
        L.append(f"## 4. 缺失录制（PARTIAL 未覆盖）")
        L.append(f"缺失 {len(missing)} 日：{', '.join(missing[:20])}（其余见日志）\n")

    L.append("## 5. 结论性质")
    L.append("- 本报告为**描述统计**：仅描述观测层状态与 flag 频次，不构成可交易性结论。")
    L.append("- 前瞻收益表（HS300/CYB/CSI1000/半导体 N=3/5/10）需接入指数历史序列后补充。")
    return "\n".join(L)


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


if __name__ == "__main__":
    raise SystemExit(main())
