#!/usr/bin/env python
"""Aggregate recorded MCP raw payloads (2026-07-21) into a SentimentRawInput-shaped dict.

Recording source (Workbuddy MCP calls, 2026-07-21 close unless noted):
  - westock data_kline: sh000001/sh000300/sz399006/sh000852, sh512480/sz159915, usSOXX
  - westock data_market_overview type=updown (returned 2026-07-20; 07-21 post-close not refreshed)

Honesty rules (handoff):
  - margin_top100_* : NOT available this round (needs margin-Top100 list + per-name limit status)
                       -> left None -> engine emits PARTIAL.
  - drawdown_kr_semi : Korean semiconductor not covered by westock -> None (PARTIAL).
  - updown overview  : used 2026-07-20 as proxy (API lag) -> noted in payload.
  - transmission_*   : derived as intraday recovery ratio (close-low)/(high-low), 0..1.
  - *_etf_bid / gjd  : derived from ETF amount 20d z-score, normalized 0..1.
  - drawdown_*       : peak-to-current since 2026-06-01 window.

Output: tests/fixtures/sentiment/mcp_raw_2026-07-21.json (LIVE_PARTIAL)
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, pstdev

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "sentiment" / "mcp_raw_2026-07-21.json"


def _recovery(o, h, l, c) -> float:
    if h == l:
        return 0.5
    return max(0.0, min(1.0, (c - l) / (h - l)))


def _znorm(x: float, hist: list[float]) -> float:
    m = mean(hist)
    s = pstdev(hist) or 1.0
    z = (x - m) / s
    return max(0.0, min(1.0, 0.5 + z / 3.0))


def _drawdown(peak: float, cur: float) -> float:
    return round((cur - peak) / peak, 4)


def main() -> int:
    # ---- recorded OHLC (2026-07-21 close) ----
    hs300 = dict(o=4630.85, h=4739.23, l=4566.28, c=4739.23, peak=5059.66,
                 amount_yi=1016.7,
                 amount_hist=[964.9, 1110.0, 1068.1, 1053.7, 985.8, 1101.7, 1027.8,
                              942.6, 907.9, 762.6, 783.2, 947.1, 1083.9, 907.9, 924.1,
                              858.3, 798.8, 920.8, 933.4, 1016.7])
    chinext = dict(o=3469.22, h=3687.15, l=3373.04, c=3685.97, peak=4371.99)
    csi1000 = dict(o=6975.89, h=7264.34, l=6714.19, c=7259.86, peak=8876.63)

    # ---- ETF amount history (亿元), 近20日，含 07-21 ----
    cyb_etf_amount = [70.3, 59.5, 65.4, 65.2, 47.8, 54.8, 77.0, 56.0, 49.6, 52.8,
                      64.4, 76.5, 76.1, 72.1, 87.7, 68.8, 77.7, 154.3, 194.6, 152.6]
    hs300_etf_amount = hs300["amount_hist"]  # proxy for broad ETF bid

    # ---- updown overview (2026-07-20 proxy) ----
    updown = dict(red=1740, green=3710, total=5528, dn_limit=10, up_limit=47)

    # ---- drawdowns (peak since 2026-06-01) ----
    a_semi_cur = 1.144
    a_semi_peak = 1.50  # sh512480 2026-06-30
    soxx_cur = 524.14   # 2026-07-20 (US not yet closed 07-21)
    soxx_peak = 640.76  # 2026-06-30

    transmission_hs300 = _recovery(**{k: hs300[k] for k in ("o", "h", "l", "c")})
    transmission_chinext = _recovery(**{k: chinext[k] for k in ("o", "h", "l", "c")})
    transmission_csi1000 = _recovery(**{k: csi1000[k] for k in ("o", "h", "l", "c")})

    star_bid = _znorm(cyb_etf_amount[-1], cyb_etf_amount)
    gjd = _znorm(hs300_etf_amount[-1], hs300_etf_amount)

    # 上证 amount 用作 volume_thrust 代理
    sse_amount = [1514.2, 1619.0, 1621.2, 1666.2, 1530.3, 1698.5, 1577.2, 1465.6,
                  1432.1, 1196.4, 1192.4, 1364.2, 1563.1, 1334.9, 1271.8, 1226.3,
                  1124.2, 1246.4, 1294.7, 1396.5]
    volume_thrust = round((sse_amount[-1] / mean(sse_amount)) - 1.0, 4)

    advance_decline = round((updown["red"] - updown["green"]) / updown["total"], 4)
    limit_stress = round(min(1.0, (updown["dn_limit"] / updown["total"]) / 0.05), 4)

    payload = {
        "as_of": "2026-07-21",
        "session": "CLOSE",
        "source": "LIVE_PARTIAL",
        # anchor
        "sse_last": 3864.37,
        # breadth / stress (07-20 proxy)
        "advance_decline_tech": advance_decline,
        "limit_stress": limit_stress,
        "limit_down_count": updown["dn_limit"],
        # intraday path: high-open then intraday selloff recovered by close -> GAP_UP_FADE
        "intraday_path": "GAP_UP_FADE",
        "volume_thrust": volume_thrust,
        # leverage / margin: NOT available this round -> None (honest PARTIAL)
        "margin_top100_limit_down_n": None,
        "margin_top100_open_board_n": None,
        "margin_top100_new_limit_down_n": None,
        "margin_list_as_of": None,
        "forced_selling_proxy": None,
        # intervention / ETF bid (derived)
        "gjd_proxy_score": round(gjd, 4),
        "star_chinext_etf_bid": round(star_bid, 4),
        "intervention_hint": "DRAG_NOT_LIFT",
        # liquidity transmission (derived intraday recovery)
        "transmission_hs300": round(transmission_hs300, 4),
        "transmission_chinext": round(transmission_chinext, 4),
        "transmission_csi1000": round(transmission_csi1000, 4),
        "transmission_lag_days": 2.0,
        # global semis drawdown (a_semi / soxx real; kr_semi None)
        "drawdown_soxx": _drawdown(soxx_peak, soxx_cur),
        "drawdown_kr_semi": None,
        "drawdown_a_semi": _drawdown(a_semi_peak, a_semi_cur),
        "t0_date": "2026-06-01",
        # structure / resonance: not derived this round
        "sector_index_resonance": None,
        "cross_section_corr_tech": None,
        "no_mainline_hint": True,
        # provenance notes (non-field, stripped before SentimentRawInput mapping)
        "_notes": {
            "margin_top100": "MISSING: needs margin-Top100 list + per-name limit/open status (multi-step MCP) -> engine PARTIAL",
            "drawdown_kr_semi": "MISSING: Korean semiconductor not covered by westock -> None",
            "updown": "used 2026-07-20 overview (07-21 post-close not refreshed)",
            "transmission": "intraday recovery (close-low)/(high-low)",
            "etf_bid": "20d amount z-score normalized 0..1 (cyb ETF / hs300 index amount proxy)",
            "a_semi_peak": "sh512480 2026-06-30=1.50",
            "soxx_peak": "usSOXX 2026-06-30=640.76 (07-20 close used)",
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[written] {OUT}")
    print(json.dumps({k: v for k, v in payload.items() if not k.startswith("_")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
