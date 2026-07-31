#!/usr/bin/env python
"""Aggregate recorded MCP raw payloads (2026-07-17, selloff day) -> SentimentRawInput.

Same honesty rules as aggregate_mcp_raw_0721.py:
  - margin_top100_* : None (not recorded this round) -> engine PARTIAL/DEGRADED
  - drawdown_kr_semi : None (Korean semi not covered by westock)
  - updown overview for 2026-07-17 not returned by API -> limit_down_count /
    advance_decline left None (honest PARTIAL), NOT fabricated.
  - transmission_* : intraday recovery (close-low)/(high-low). 2026-07-17 was a
    down day -> recovery low (broad stabilization weak that day).
  - *_etf_bid : 20d amount z-score normalized (high volume = sellout or support).
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, pstdev

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "sentiment" / "mcp_raw_2026-07-17.json"


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
    hs300 = dict(o=4661.62, h=4663.53, l=4492.09, c=4529.1, amount_yi=920.8,
                 amount_hist=[1110.0, 1068.1, 1053.7, 985.8, 1101.7, 1027.8, 942.6,
                              907.9, 762.6, 783.2, 947.1, 1083.9, 907.9, 924.1,
                              858.3, 798.8, 1246.4])
    chinext = dict(o=3642.06, h=3642.06, l=3387.26, c=3428.63)
    csi1000 = dict(o=7599.39, h=7605.61, l=7163.31, c=7168.0)

    cyb_etf_amount = [56.0, 49.6, 52.8, 64.4, 76.5, 76.1, 72.1, 87.7, 68.8,
                      77.7, 154.3]  # recent window ending 2026-07-17
    hs300_etf_amount = hs300["amount_hist"]

    soxx_cur = 521.81   # 2026-07-17
    soxx_peak = 640.76  # 2026-06-30
    a_semi_cur = 1.07   # sh512480 2026-07-17
    a_semi_peak = 1.50  # 2026-06-30

    transmission_hs300 = _recovery(**{k: hs300[k] for k in ("o", "h", "l", "c")})
    transmission_chinext = _recovery(**{k: chinext[k] for k in ("o", "h", "l", "c")})
    transmission_csi1000 = _recovery(**{k: csi1000[k] for k in ("o", "h", "l", "c")})

    star_bid = _znorm(cyb_etf_amount[-1], cyb_etf_amount)
    gjd = _znorm(hs300_etf_amount[-1], hs300_etf_amount)

    sse_amount = [1619.0, 1621.2, 1666.2, 1530.3, 1698.5, 1577.2, 1465.6,
                  1432.1, 1196.4, 1192.4, 1364.2, 1563.1, 1334.9, 1271.8,
                  1124.2, 1246.4]
    volume_thrust = round((sse_amount[-1] / mean(sse_amount)) - 1.0, 4)

    payload = {
        "as_of": "2026-07-17",
        "session": "CLOSE",
        "source": "LIVE_PARTIAL",
        "sse_last": 3764.15,
        "advance_decline_tech": None,   # 07-17 updown overview not returned -> honest None
        "limit_stress": None,
        "limit_down_count": None,
        "intraday_path": "CHOP",         # down day, no clear V / gap-fade
        "volume_thrust": volume_thrust,
        "margin_top100_limit_down_n": None,
        "margin_top100_open_board_n": None,
        "margin_top100_new_limit_down_n": None,
        "margin_list_as_of": None,
        "forced_selling_proxy": None,
        "gjd_proxy_score": round(gjd, 4),
        "star_chinext_etf_bid": round(star_bid, 4),
        "intervention_hint": "DRAG_NOT_LIFT",
        "transmission_hs300": round(transmission_hs300, 4),
        "transmission_chinext": round(transmission_chinext, 4),
        "transmission_csi1000": round(transmission_csi1000, 4),
        "transmission_lag_days": 0.0,
        "drawdown_soxx": _drawdown(soxx_peak, soxx_cur),
        "drawdown_kr_semi": None,
        "drawdown_a_semi": _drawdown(a_semi_peak, a_semi_cur),
        "t0_date": "2026-06-01",
        "sector_index_resonance": None,
        "cross_section_corr_tech": None,
        "no_mainline_hint": False,
        "_notes": {
            "margin_top100": "MISSING -> engine PARTIAL/DEGRADED",
            "updown_07-17": "market_overview returned 2026-07-20 only; 07-17 breadth left None (not fabricated)",
            "drawdown_kr_semi": "MISSING: Korean semiconductor not covered by westock",
            "transmission": "intraday recovery low (down day): hs300 0.22 / chinext 0.16 / csi1000 0.01 -> engine FAILED stage",
            "etf_bid": "20d amount z normalized; high volume on 07-17 may be sellout, not support",
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
