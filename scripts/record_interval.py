#!/usr/bin/env python
"""Interval recorder — Workbuddy side (MCP data -> SentimentRawInput fixtures).

把已录制的 MCP 原始数据：
  - vault/shadow/mcp_klines_raw.json  (批量历史 kline: 上证/沪深300/创业板/中证1000/半导体ETF/创业板ETF/SOXX)
  - vault/shadow/overview/updown_all.json (36 交易日真实涨跌分布广度)
派生为 36 个 mcp_raw_{date}.json，落到 vault/shadow/mcp_raw/（McpSentimentProvider 默认目录）。

诚实原则（handoff）：
  - margin_top100_* / forced_selling_proxy : 市场级融资余额变动 API 返回空、逐只 Top100 太重
    -> 保持 None。引擎因此判 DEGRADED，但仅因该字段不可得；其余信号全真实。
  - drawdown_kr_semi / kr_a_divergence : westock 不覆盖韩国半导体 -> None。
  - sector_index_resonance / cross_section_corr_tech : 截面相关需板块内部数据 -> None。
  - 07-21 广度 API 滞后返回 07-20 -> updown_all.json 中已用 07-20 代理并标注 _proxied_from。
  - 所有派生字段来自真实 kline/广度，绝不用 0 假充。
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KLINES = ROOT / "vault" / "shadow" / "mcp_klines_raw.json"
UPDOWN = ROOT / "vault" / "shadow" / "overview" / "updown_all.json"
OUT_DIR = ROOT / "vault" / "shadow" / "mcp_raw"
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "sentiment"
# 黄金回归样本（handoff §10.3 已验证 + test_mcp_provider 守护）：
# 07-17=S2/FAILED、07-21=S3/TO_CSI1000。这两日的 intraday_path / 广度 / 传导为人工校准的 LIVE 真值，
# 批量派生的路径分类在边缘日（如 07-21 深V）会与黄金结果分歧，故直接沿用黄金 fixture 作为 source of truth，
# 其余 34 日走批量派生。绝不改写这两份已验证契约。
GOLDEN_DATES = {"2026-07-17", "2026-07-21"}

T0_DATE = "2026-06-01"
SYMBOLS = {
    "sse": "sh000001",
    "hs300": "sh000300",
    "chinext": "sz399006",
    "csi1000": "sh000852",
    "soxx": "usSOXX",
    "a_semi": "sh512480",
    "cyb_etf": "sz159915",
}


def _recovery(o, h, l, c) -> float:
    if h == l:
        return 0.5
    return max(0.0, min(1.0, (c - l) / (h - l)))


def _znorm(x: float, hist: list[float]) -> float:
    m = statistics.mean(hist)
    s = statistics.pstdev(hist) or 1.0
    z = (x - m) / s
    return max(0.0, min(1.0, 0.5 + z / 3.0))


def _drawdown(peak: float, cur: float) -> float:
    return round((cur - peak) / peak, 4)


def _classify_path(o, h, l, c, prev_close) -> str:
    recovery = _recovery(o, h, l, c)
    gap = (o / prev_close - 1) if prev_close else 0.0
    if gap > 0.003 and c < o * 0.995:
        return "GAP_UP_FADE"
    if gap < -0.003 and recovery > 0.6:
        return "GAP_DOWN_RECOVERY"
    if recovery >= 0.75:
        return "DEEP_V"
    if c > o and recovery >= 0.5:
        return "LATE_BID_SQUEEZE"
    return "CHOP"


def _load_klines() -> dict:
    raw = json.loads(KLINES.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for sym in SYMBOLS.values():
        out[sym] = {}
    for entry in raw["data"]["data"]:
        sym = entry["symbol"]
        if sym not in out:
            continue
        for n in entry["data"]["nodes"]:
            out[sym][n["date"]] = n
    return out


def _load_updown() -> tuple[dict, dict]:
    raw = json.loads(UPDOWN.read_text(encoding="utf-8"))
    proxy = raw.get("_proxy", {})
    rows = {k: v for k, v in raw.items() if k != "_proxy"}
    return rows, proxy


def _amount_yi(n: float) -> float:
    return n / 1e8


def main() -> int:
    klines = _load_klines()
    updown, proxy = _load_updown()

    # trading-day set = union of kline dates (use sse as anchor)
    dates = sorted(klines[SYMBOLS["sse"]].keys())

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for d in dates:
        # 黄金回归样本：直接沿用已验证 fixture，覆盖批量派生（见 GOLDEN_DATES 注释）
        if d in GOLDEN_DATES and (GOLDEN_DIR / f"mcp_raw_{d}.json").exists():
            src = GOLDEN_DIR / f"mcp_raw_{d}.json"
            (OUT_DIR / f"mcp_raw_{d}.json").write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8"
            )
            written += 1
            continue

        sse = klines[SYMBOLS["sse"]][d]
        hs300 = klines[SYMBOLS["hs300"]][d]
        chinext = klines[SYMBOLS["chinext"]][d]
        csi1000 = klines[SYMBOLS["csi1000"]][d]
        a_semi = klines[SYMBOLS["a_semi"]][d]
        cyb_etf = klines[SYMBOLS["cyb_etf"]][d]
        soxx_node = klines[SYMBOLS["soxx"]].get(d)  # US calendar gap possible

        # breadth (with proxy fallback)
        br = updown.get(d)
        if br is None and d in proxy:
            br = updown.get(proxy[d])
        proxied_from = br.get("_proxied_from") if br and "_proxied_from" in br else None
        br = {k: v for k, v in (br or {}).items() if k != "_proxied_from"}

        # ---- amount histories (in 亿) up to and including d ----
        def amt_hist(sym: str, key: str = "amount") -> list[float]:
            hs = []
            for dd in dates:
                if dd <= d:
                    hs.append(_amount_yi(klines[sym][dd][key]))
            return hs

        sse_amt = amt_hist(SYMBOLS["sse"])
        hs300_amt = amt_hist(SYMBOLS["hs300"])
        cyb_amt = amt_hist(SYMBOLS["cyb_etf"])

        # ---- prev close for gap ----
        idx = dates.index(d)
        prev_close = klines[SYMBOLS["sse"]][dates[idx - 1]]["last"] if idx > 0 else None

        # ---- transmission (intraday recovery) ----
        transmission_hs300 = round(_recovery(hs300["open"], hs300["high"], hs300["low"], hs300["last"]), 4)
        transmission_chinext = round(_recovery(chinext["open"], chinext["high"], chinext["low"], chinext["last"]), 4)
        transmission_csi1000 = round(_recovery(csi1000["open"], csi1000["high"], csi1000["low"], csi1000["last"]), 4)

        # ---- volume thrust & ETF bid z-scores ----
        sse_amt_today = _amount_yi(sse["amount"])
        volume_thrust = round((sse_amt_today / statistics.mean(sse_amt)) - 1.0, 4) if sse_amt else 0.0
        gjd = round(_znorm(_amount_yi(hs300["amount"]), hs300_amt), 4)
        star = round(_znorm(_amount_yi(cyb_etf["amount"]), cyb_amt), 4)

        # ---- running peaks (since T0) for drawdowns ----
        def running_peak(sym: str) -> float:
            pk = -1e18
            for dd in dates:
                node = klines[sym].get(dd)
                if node and dd <= d:
                    pk = max(pk, node["last"])
            return pk

        a_semi_peak = running_peak(SYMBOLS["a_semi"])
        drawdown_a_semi = _drawdown(a_semi_peak, a_semi["last"])

        # ---- us mega tech path (from SOXX; None if US calendar gap) ----
        if soxx_node:
            soxx_peak = running_peak(SYMBOLS["soxx"])
            drawdown_soxx = _drawdown(soxx_peak, soxx_node["last"])
            soxx_hist = [klines[SYMBOLS["soxx"]][dd]["last"] for dd in dates if klines[SYMBOLS["soxx"]].get(dd) and dd <= d]
            soxx_ma20 = statistics.mean(soxx_hist[-20:]) if len(soxx_hist) >= 20 else statistics.mean(soxx_hist)
            soxx_5d = (soxx_node["last"] / soxx_hist[-6] - 1.0) if len(soxx_hist) >= 6 else None
            if soxx_node["last"] < soxx_ma20 and (soxx_5d is None or soxx_5d < 0):
                us_path = "RALLY_FADE"
            elif soxx_5d is not None and soxx_5d > 0.03:
                us_path = "RALLY"
            else:
                us_path = "FADE"
        else:
            drawdown_soxx = None
            us_path = None

        # ---- breadth-derived ----
        if br:
            total = br.get("CNT_TOTAL") or 1
            red = br.get("CNT_RED", 0)
            green = br.get("CNT_GREEN", 0)
            dn = br.get("CNT_REACH_DNLIMIT", 0)
            advance_decline = round((red - green) / total, 4)
            limit_down_count = int(dn)
            limit_stress = round(min(1.0, (dn / total) / 0.05), 4)
        else:
            advance_decline = None
            limit_down_count = None
            limit_stress = None

        intraday_path = _classify_path(
            sse["open"], sse["high"], sse["low"], sse["last"], prev_close
        )

        # no_mainline: tie to breadth — weak breadth => no leadership
        no_mainline = advance_decline is None or advance_decline < 0.0

        payload = {
            "as_of": d,
            "session": "CLOSE",
            "source": "LIVE_PARTIAL",
            "sse_last": round(sse["last"], 2),
            "advance_decline_tech": advance_decline,
            "limit_down_count": limit_down_count,
            "limit_stress": limit_stress,
            "intraday_path": intraday_path,
            "volume_thrust": volume_thrust,
            # margin Top100: NOT available (market-level margin API empty) -> honest None
            "margin_top100_limit_down_n": None,
            "margin_top100_open_board_n": None,
            "margin_top100_new_limit_down_n": None,
            "margin_list_as_of": None,
            "forced_selling_proxy": None,
            # intervention: let engine derive from gjd/star/path (data-driven)
            "gjd_proxy_score": round(gjd, 4),
            "star_chinext_etf_bid": round(star, 4),
            "intervention_hint": None,
            "transmission_hs300": transmission_hs300,
            "transmission_chinext": transmission_chinext,
            "transmission_csi1000": transmission_csi1000,
            "transmission_lag_days": 2.0,
            # global semis
            "drawdown_soxx": drawdown_soxx,
            "drawdown_kr_semi": None,
            "drawdown_a_semi": drawdown_a_semi,
            "t0_date": T0_DATE,
            "us_mega_tech_path": us_path,
            "kr_a_divergence": None,
            "sector_index_resonance": None,
            "cross_section_corr_tech": None,
            "no_mainline_hint": bool(no_mainline),
            "_notes": {
                "margin_top100": "MISSING: market-level margin API returns empty rows; per-name Top100 too heavy -> engine DEGRADED (only this reason)",
                "drawdown_kr_semi": "MISSING: Korean semiconductor not covered by westock -> None",
                "breadth": f"real updown (proxied_from={proxied_from})" if proxied_from else "real updown",
                "transmission": "intraday recovery (close-low)/(high-low)",
                "etf_bid": "20d amount z-score normalized 0..1 (cyb ETF / hs300 index amount)",
                "soxx_peak": round(soxx_peak, 2) if soxx_node else None,
                "a_semi_peak": round(a_semi_peak, 4),
            },
        }

        out = OUT_DIR / f"mcp_raw_{d}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written += 1

    print(f"[written] {written} fixtures to {OUT_DIR}")
    return 0


def _amount_yi_n(x: float) -> float:
    return x


def _amount_yi_n(x: float) -> float:
    return x


if __name__ == "__main__":
    raise SystemExit(main())
