#!/usr/bin/env python
"""Forward-return table for the sentiment-shadow interval backtest.

读 `vault/shadow/mcp_klines_raw.json`（4 个前瞻标的的日线收盘）与
`output/sentiment_shadow_<start>_<end>.jsonl`（快照日期 + 阶段），
计算每只标的在 N=3/5/10 个交易日后的前瞻收益。

诚实原则（与 handoff 一致）：
- 前瞻窗口目标日 > 数据可得最新日（2026-07-21）时，收益为 None（未实现），
  绝不用 0 或外推假充。
- 本脚本只读不改 engine / Kernel。

用法：
  python -m scripts.forward_returns --start 2026-06-01 --end 2026-07-21
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "output"
KLINES = ROOT / "vault" / "shadow" / "mcp_klines_raw.json"

# 前瞻标的映射：展示名 -> kline symbol
FORWARD_MAP = {
    "HS300": "sh000300",
    "CYB": "sz399006",
    "CSI1000": "sh000852",
    "SEMI": "sh512480",
}
HORIZONS = [3, 5, 10]


def _load_close_series() -> Dict[str, Dict[str, float]]:
    """symbol -> {date_str: close} sorted ascending by date."""
    raw = json.loads(KLINES.read_text(encoding="utf-8"))
    out: Dict[str, Dict[str, float]] = {}
    for item in raw["data"]["data"]:
        sym = item["symbol"]
        series: Dict[str, float] = {}
        for node in item.get("data", {}).get("nodes", []):
            series[node["date"]] = float(node["last"])
        out[sym] = dict(sorted(series.items()))
    return out


def _trading_dates(series: Dict[str, float]) -> List[str]:
    return list(series.keys())  # ascending


def _fwd_return(series: Dict[str, float], d: str, n: int) -> Tuple[Optional[float], Optional[str]]:
    """Close-to-close N trading days forward. Returns (ret, target_date)."""
    dates = _trading_dates(series)
    if d not in dates:
        return None, None
    i = dates.index(d)
    j = i + n
    if j >= len(dates):
        return None, None  # 未实现（超出数据可得日）
    base = series[d]
    tgt = dates[j]
    tgt_close = series[tgt]
    if base <= 0 or tgt_close <= 0:
        return None, tgt
    return tgt_close / base - 1.0, tgt


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v*100:+.2f}%"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Sentiment-shadow forward returns")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args(argv)

    closes = _load_close_series()
    jsonl = OUT_DIR / f"sentiment_shadow_{args.start}_{args.end}.jsonl"
    if not jsonl.exists():
        print(f"[error] missing {jsonl}; run a_share_sentiment_backtest first")
        return 2
    rows = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows.sort(key=lambda r: r["as_of"])

    # also load updown proxy note for 07-21? not needed here.

    # compute matrix
    matrix: List[Dict[str, Any]] = []
    for r in rows:
        d = r["as_of"]
        stage = r["cycle"]["stage"]
        rec: Dict[str, Any] = {"date": d, "stage": stage, "quality": r["quality"]}
        for label, sym in FORWARD_MAP.items():
            ser = closes.get(sym, {})
            for n in HORIZONS:
                ret, tgt = _fwd_return(ser, d, n)
                rec[f"{label}_n{n}"] = ret
                rec[f"{label}_tgt{n}"] = tgt
        matrix.append(rec)

    # by-stage aggregation (realized only)
    from collections import defaultdict

    agg = defaultdict(lambda: {"cnt": 0, "realized": 0})
    stage_stats: Dict[str, Dict[str, Dict[int, List[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for rec in matrix:
        st = rec["stage"]
        for label in FORWARD_MAP:
            for n in HORIZONS:
                v = rec[f"{label}_n{n}"]
                if v is not None:
                    stage_stats[st][label][n].append(v)

    # ---- markdown ----
    latest = max(closes[list(FORWARD_MAP.values())[0]].keys())
    L: List[str] = []
    L.append(f"# 前瞻收益表（快照 {args.start} ~ {args.end}）\n")
    L.append("> 观测层验证：阶段（S2/S3）是否领先于宽基/科技复盘收益。不改 Kernel、不自动交易。\n")
    L.append(f"**数据可得最新日：{latest}**。前瞻窗口目标日 > 该日视为未实现，收益记为 `—`（绝不用 0 假充）。\n")

    L.append("## 1. 按阶段聚合的前瞻收益（均值 / 样本数，仅已实现的窗口）")
    L.append("| 阶段 | HS300 N3 | N5 | N10 | CYB N3 | N5 | N10 | CSI1000 N3 | N5 | N10 | SEMI N3 | N5 | N10 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for st in sorted(stage_stats.keys()):
        cells = [st]
        for label in FORWARD_MAP:
            for n in HORIZONS:
                vals = stage_stats[st][label][n]
                if vals:
                    cells.append(f"{sum(vals)/len(vals)*100:+.2f}% (n={len(vals)})")
                else:
                    cells.append("—")
        L.append("| " + " | ".join(cells) + " |")
    L.append("")
    L.append("> 说明：由于数据截止 2026-07-21，N=10 窗口仅 07-11 及之前的快照有实现收益；")
    L.append("> N=5 仅 07-14 及之前；N=3 仅 07-16 及之前。近期（危机底 07-17/07-21）的前瞻收益尚未实现，需后续数据回填。\n")

    # daily matrix per instrument
    L.append("## 2. 每日前瞻收益矩阵")
    for label, sym in FORWARD_MAP.items():
        L.append(f"\n### {label}（{sym}）")
        L.append("| 日期 | 阶段 | N3 | N5 | N10 |")
        L.append("|---|---|---|---|---|")
        for rec in matrix:
            if rec["stage"] == "":
                continue
            L.append(
                f"| {rec['date']} | {rec['stage']} | "
                f"{_fmt(rec[f'{label}_n3'])} | {_fmt(rec[f'{label}_n5'])} | {_fmt(rec[f'{label}_n10'])} |"
            )
        L.append("")

    L.append("## 3. 结论性质")
    L.append("- 该表为**验证层描述统计**：检验情绪阶段（S2 摸底 / S3 磨底）是否领先于宽基与科技复盘。")
    L.append("- 因数据截止 07-21，近期危机底的 N=3/5/10 前瞻收益尚未实现，留待后续交易日回填后再评估。")
    L.append("- 全样本阶段仅解析到 S2/S3（margin_top100 与 sector_index_resonance 缺失抑制 S1/S4），见 handoff §6。")

    md = OUT_DIR / f"sentiment_forward_returns_{args.end}.md"
    md.write_text("\n".join(L), encoding="utf-8")

    # json (full, machine-readable)
    json_out = OUT_DIR / f"sentiment_forward_returns_{args.end}.json"
    json_out.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[written] {md}")
    print(f"[written] {json_out}")
    print(f"[info] latest available date = {latest}; snapshots = {len(matrix)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
