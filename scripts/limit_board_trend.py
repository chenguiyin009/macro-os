"""Limit-up / limit-down COUNT trend over the recorded interval.

Reads vault/shadow/overview/updown_all.json (daily breadth from westock
market_overview) and derives the 跌停 / 涨停 COUNT trend across the
crisis window. Pure read-only analysis, no writes to engine state.

Usage:
  python -m scripts.limit_board_trend --start 2026-06-01 --end 2026-07-21
"""
from __future__ import annotations
import argparse, json, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "vault" / "shadow" / "overview" / "updown_all.json"


def load() -> dict:
    raw = json.load(open(SRC, encoding="utf-8"))
    proxy = raw.pop("_proxy", {})
    rows = []
    for d, v in raw.items():
        if not isinstance(v, dict):
            continue
        rows.append({
            "date": d,
            "dn": v.get("CNT_REACH_DNLIMIT"),
            "up": v.get("CNT_REACH_UPLIMIT"),
            "ratio_down": v.get("RATIO_DOWN"),
            "ratio_up": v.get("RATIO_UP"),
            "proxied_from": v.get("_proxied_from"),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-07-21")
    args = ap.parse_args()

    rows = [r for r in load() if args.start <= r["date"] <= args.end]
    dn = [r["dn"] for r in rows if r["dn"] is not None]
    up = [r["up"] for r in rows if r["up"] is not None]

    # slope via simple linear regression on index
    n = len(dn)
    xs = list(range(n))
    if n > 1:
        mx, my = statistics.mean(xs), statistics.mean(dn)
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, dn))
        var = sum((x - mx) ** 2 for x in xs)
        slope = cov / var if var else 0.0
    else:
        slope = 0.0

    # early (first third) vs late (last third) avg
    k = max(1, n // 3)
    early_avg = statistics.mean(dn[:k])
    late_avg = statistics.mean(dn[-k:])
    up_early = statistics.mean(up[:k])
    up_late = statistics.mean(up[-k:])

    # crisis week = last 5 trading rows
    cw = dn[-5:]
    cw_dates = [r["date"] for r in rows[-5:]]

    print("=" * 64)
    print("跌停 / 涨停 数量趋势  (%s ~ %s)" % (args.start, args.end))
    print("=" * 64)
    print("交易日数: %d | 跌停均值 %.2f | 涨停均值 %.2f" % (n, statistics.mean(dn), statistics.mean(up)))
    print("跌停线性斜率(每日): %.3f  -> %s" % (slope, "上升" if slope > 0.02 else ("下降" if slope < -0.02 else "走平")))
    print("跌停 前1/3均值 %.2f -> 后1/3均值 %.2f  (倍率 %.2fx)" % (early_avg, late_avg, late_avg / early_avg if early_avg else 0))
    print("涨停 前1/3均值 %.2f -> 后1/3均值 %.2f  (倍率 %.2fx)" % (up_early, up_late, up_late / up_early if up_early else 0))
    print("危机周(最后5日 %s~%s) 跌停: %s 均值 %.2f" % (
        cw_dates[0], cw_dates[-1], cw, statistics.mean(cw)))
    print("-" * 64)
    print("%-12s %6s %6s %8s %8s" % ("date", "跌停", "涨停", "跌%", "涨%"))
    for r in rows:
        print("%-12s %6s %6s %8s %8s%s" % (
            r["date"], r["dn"], r["up"], r["ratio_down"], r["ratio_up"],
            "  (proxy)" if r["proxied_from"] else ""))

    # JSON dump for downstream report
    out = {
        "window": {"start": args.start, "end": args.end, "n_days": n},
        "dn_slope_per_day": round(slope, 4),
        "dn_trend": "rising" if slope > 0.02 else ("falling" if slope < -0.02 else "flat"),
        "dn_early_avg": round(early_avg, 2), "dn_late_avg": round(late_avg, 2),
        "dn_late_to_early_x": round(late_avg / early_avg, 2) if early_avg else None,
        "up_early_avg": round(up_early, 2), "up_late_avg": round(up_late, 2),
        "up_late_to_early_x": round(up_late / up_early, 2) if up_early else None,
        "crisis_week": {"dates": cw_dates, "dn": cw, "dn_avg": round(statistics.mean(cw), 2)},
        "rows": rows,
    }
    dst = ROOT / "output" / ("limit_board_trend_%s.json" % args.end)
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n[written] %s" % dst)


if __name__ == "__main__":
    main()
