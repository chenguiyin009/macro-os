#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""晚报旁证 8–11 节（观察，不进合成 min）— 2026-11-14 新增.

此前这 4 节只由例行 agent 在聊天里手写，没有脚本入口，归档 md 因此缺失（自 10-30 起）。
本脚本把它们落成可复现的产物：

  8  冲击吸收旁证     ← output/shock_absorption_<date>.json
  9  思路观测旁证     ← output/article_macro_watch_<date>.json
  10 美国商业票据/H.8 ← FRED COMPOUT（周，十亿美元）/ TOTBKCR（周，十亿美元）
  11 用户 Q4 判断对照 ← FRED 收益率/EFFR/SOFR/T10YIE/HY OAS + yfinance 收盘 + Bundesbank；
     判读为「规则 v2」（scripts/q4_view_rules.py，阈值由经济含义推导，不拟合手写；仅观察）

单位注意：COMPOUT 单位是十亿美元，1507.5 = 1.5075 万亿美元 = 15075 亿美元。
11-12 晚报手写成「1507.5亿美元」是单位错（少 10 倍），本脚本统一输出「十亿美元 + 万亿」。

OUTPUT: output/evening_sections_<date>.{md,json}
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import q4_view_rules as q4  # noqa: E402

logger = logging.getLogger("evening-sections")

YF_TICKERS = {
    "tlt": "TLT", "spy": "SPY", "rsp": "RSP", "hyg": "HYG", "lqd": "LQD", "dbc": "DBC",
    "copper": "HG=F", "gold": "GC=F", "wti": "CL=F", "brent": "BZ=F",
    "dxy": "DX-Y.NYB", "eurusd": "EURUSD=X", "vix": "^VIX",
}
FRED_RATES = {"us2y": "DGS2", "us5y": "DGS5", "us10y": "DGS10", "us30y": "DGS30", "us3m": "DGS3MO",
              "effr": "EFFR", "sofr": "SOFR", "t10yie": "T10YIE", "hy_oas": "BAMLH0A0HYM2"}


# ---------------- pure helpers (unit-tested) ----------------

def pct_change_n(points: List[Tuple[str, float]], as_of: str, n: int) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """points 升序 (date, value)。返回 (last, n根前到 last 的 % 变化, last_date)；只用 <= as_of。"""
    pts = [(d, v) for d, v in points if d <= as_of and v is not None]
    if not pts:
        return None, None, None
    last_d, last_v = pts[-1]
    if len(pts) <= n or not pts[-1 - n][1]:
        return last_v, None, last_d
    return last_v, (last_v / pts[-1 - n][1] - 1.0) * 100.0, last_d


def diff_n(points: List[Tuple[str, float]], as_of: str, n: int) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    pts = [(d, v) for d, v in points if d <= as_of and v is not None]
    if not pts:
        return None, None, None
    if len(pts) <= n:
        return pts[-1][1], None, pts[-1][0]
    return pts[-1][1], pts[-1][1] - pts[-1 - n][1], pts[-1][0]


def yoy_weekly(points: List[Tuple[str, float]], as_of: str, lag_weeks: int = 0) -> Optional[float]:
    """周度序列同比（52 周前）。lag_weeks>0 取更早一点的同比，用于判断放缓/加速。"""
    pts = [(d, v) for d, v in points if d <= as_of and v is not None]
    i = len(pts) - 1 - lag_weeks
    if i - 52 < 0:
        return None
    return (pts[i][1] / pts[i - 52][1] - 1.0) * 100.0


def _f(x: Optional[float]) -> bool:
    return x is not None


# 2026-11-14：v1 verdict_a..f（由 10-29/11-12 手写判读反推）已删除，改用 scripts/q4_view_rules.py 规则 v2。


# ---------------- data ----------------

def _fred(series: str, lookback_days: int) -> List[Tuple[str, float]]:
    try:
        from adapters.fred import fetch_fred_series
        pts = fetch_fred_series(series, lookback_days=lookback_days, timeout_seconds=20)
        return [(p.date, float(p.value)) for p in pts if p.value is not None]
    except Exception as exc:  # noqa: BLE001
        logger.warning("FRED %s failed: %s", series, exc)
        return []


def _yf(ticker: str) -> List[Tuple[str, float]]:
    try:
        import yfinance as yf
        df = yf.download(ticker, period="6mo", interval="1d", progress=False, auto_adjust=False)
        if df is None or df.empty:
            return []
        s = df["Close"]
        if hasattr(s, "columns"):
            s = s.iloc[:, 0]
        s = s.dropna()
        return [(d.strftime("%Y-%m-%d"), float(v)) for d, v in s.items()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance %s failed: %s", ticker, exc)
        return []


# ---------------- Bund 10Y（2026-11-14）----------------
# 优先级：--bund10y / GYBOT_BUND10Y 手工覆盖 > Bundesbank 日频「最新 10 年期 Bund 收益率」(BBSSY WT1010，
# 交易所价，口径最接近 TV TVC:DE10Y) > Bundesbank Svensson 10 年期零息曲线 > FRED IRLTLT01DEM156N（月均，只作兜底，无 5D）。
BBK_URL = "https://api.statistiken.bundesbank.de/rest/data/{flow}/{key}?lastNObservations={n}"
BBK_SERIES = [
    ("BBSSY", "D.REN.EUR.A630.000000WT1010.A", "Bundesbank BBSSY WT1010（最新10年期Bund收益率，日）"),
    ("BBSIS", "D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A", "Bundesbank Svensson 10Y（日，曲线拟合口径）"),
]


def parse_bbk_csv(text: str) -> List[Tuple[str, float]]:
    """Bundesbank SDMX-CSV（; 分隔）→ 升序 (date, value)；'.' 为休市缺值，跳过。"""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return []
    head = lines[0].split(";")
    try:
        it, iv = head.index("TIME_PERIOD"), head.index("OBS_VALUE")
    except ValueError:
        return []
    out = []
    for ln in lines[1:]:
        c = ln.split(";")
        if len(c) <= max(it, iv):
            continue
        try:
            out.append((c[it], float(c[iv])))
        except ValueError:
            continue
    return sorted(out)


def _http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 25.0) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def bund_from_points(pts: List[Tuple[str, float]], date_str: str, source: str) -> Optional[Dict[str, Any]]:
    pts = [(d, v) for d, v in pts if d <= date_str]
    if not pts:
        return None
    last_d, last_v = pts[-1]
    prev = pts[-6][1] if len(pts) >= 6 else None
    prev20 = pts[-21][1] if len(pts) >= 21 else None
    return {"last": last_v, "date": last_d,
            "chg5_bp": (round((last_v - prev) * 100, 1) if prev is not None else None),
            "chg20_bp": (round((last_v - prev20) * 100, 1) if prev20 is not None else None), "source": source}


def fetch_bund10y(date_str: str, get=None) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    get = get or _http_get
    notes: List[str] = []
    for flow, key, label in BBK_SERIES:
        try:
            txt = get(BBK_URL.format(flow=flow, key=key, n=120),
                      {"Accept": "application/vnd.sdmx.data+csv;version=1.0.0", "User-Agent": "Mozilla/5.0"})
            b = bund_from_points(parse_bbk_csv(txt), date_str, label)
            if b:
                if b["date"] != date_str:
                    notes.append(f"{label} 最新 {b['date']}（{date_str} 未发布）")
                return b, notes
            notes.append(f"{label} 无数据")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{label} 失败: {exc}")
    pts = _fred("IRLTLT01DEM156N", 400)
    if pts:
        d, v = [(d, v) for d, v in pts if d <= date_str][-1]
        notes.append("Bund 10Y 用 FRED 月均兜底（无 5D）")
        return {"last": v, "date": d, "chg5_bp": None, "chg20_bp": None, "source": "FRED IRLTLT01DEM156N（月均兜底）"}, notes
    return None, notes


def _load(report_dir: Path, stem: str, date_str: str) -> Optional[Dict[str, Any]]:
    p = report_dir / f"{stem}_{date_str}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def build(date_str: str, report_dir: Path, bund: Optional[Tuple[float, Optional[float]]] = None,
          fetch=True) -> Dict[str, Any]:
    out: Dict[str, Any] = {"date": date_str, "schema": "evening_sections_v2", "gaps": []}
    out["shock"] = _load(report_dir, "shock_absorption", date_str)
    out["article"] = _load(report_dir, "article_macro_watch", date_str)
    if out["shock"] is None:
        out["gaps"].append(f"shock_absorption_{date_str}.json 缺失")
    if out["article"] is None:
        out["gaps"].append(f"article_macro_watch_{date_str}.json 缺失")
    px: Dict[str, Dict[str, Any]] = {}
    if fetch:
        for k, t in YF_TICKERS.items():
            pts = _yf(t)
            last, c5, d = pct_change_n(pts, date_str, 5)
            _, c20, _ = pct_change_n(pts, date_str, q4.W_TREND)
            px[k] = {"ticker": t, "last": last, "chg5_pct": c5, "chg20_pct": c20, "date": d}
            if k == "spy":
                win = [v for dd, v in pts if dd <= date_str][-(q4.W_TREND + 1):]
                px[k]["drop_days_20d"] = q4.drop_days(win, q4.TH["c3_drop_day_pct"]) if len(win) == q4.W_TREND + 1 else None
                px[k]["maxdd_20d_pct"] = q4.max_drawdown_pct(win) if len(win) == q4.W_TREND + 1 else None
            if last is None or d != date_str:
                out["gaps"].append(f"{t} 收盘缺 {date_str}（最新 {d}）")
        for k, s in FRED_RATES.items():
            pts = _fred(s, 120)
            last, d5, d = diff_n(pts, date_str, 5)
            _, d20, _ = diff_n(pts, date_str, q4.W_TREND)
            px[k] = {"series": s, "last": last, "chg5_bp": (d5 * 100 if d5 is not None else None),
                     "chg20_bp": (d20 * 100 if d20 is not None else None), "date": d}
            if d != date_str:
                out["gaps"].append(f"FRED {s} 最新 {d}（{date_str} 未发布）")
        cp = _fred("COMPOUT", 500)
        h8 = _fred("TOTBKCR", 500)
        cpl, cpw, cpd = diff_n(cp, date_str, 1)
        h8l, h8w, h8d = diff_n(h8, date_str, 1)
        out["cp"] = {"series": "COMPOUT", "unit": "十亿美元", "last": cpl, "wchg": cpw, "date": cpd}
        out["h8"] = {"series": "TOTBKCR", "unit": "十亿美元", "last": h8l, "wchg": h8w, "date": h8d,
                     "yoy_pct": yoy_weekly(h8, date_str), "yoy_4w_ago_pct": yoy_weekly(h8, date_str, 4),
                     "yoy_13w_ago_pct": yoy_weekly(h8, date_str, q4.W_CREDIT)}
    out["px"] = px
    if bund:
        out["bund10y"] = {"last": bund[0], "chg5_bp": (round((bund[0] - bund[1]) * 100, 1) if bund[1] is not None else None),
                          "chg20_bp": None, "date": date_str, "source": "手工覆盖 --bund10y / GYBOT_BUND10Y"}
    elif fetch:
        b, notes = fetch_bund10y(date_str)
        out["bund10y"] = b
        out["gaps"] += notes
        if b is None:
            out["gaps"].append("Bund 10Y 所有来源失败")
    else:
        out["bund10y"] = None
    out["q4"] = q4_judge(out, date_str)
    out["verdicts"] = {k: v["verdict"] for k, v in out["q4"].items()}
    return out


def q4_judge(out: Dict[str, Any], date_str: str) -> Dict[str, Dict[str, Any]]:
    """规则 v2：每条观点 → 判定 + 所用数据（数值、as_of）。缺数据即「数据缺」。"""
    px = out.get("px") or {}
    g = lambda k, f: (px.get(k) or {}).get(f)  # noqa: E731
    art = out.get("article") or {}
    fed = art.get("fed_1y_hike_bp")
    bund = out.get("bund10y") or {}
    h8 = out.get("h8") or {}
    r: Dict[str, Dict[str, Any]] = {}
    a1 = q4.judge_a1(fed, g("us2y", "last"), g("effr", "last"))
    a2 = q4.judge_a2(g("us2y", "last"), g("us5y", "last"))
    a3 = q4.judge_a3(g("tlt", "chg20_pct"), g("us30y", "chg20_bp"))
    for k, x in (("a1", a1), ("a2", a2), ("a3", a3)):
        r[k] = x
    r["a1"]["as_of"] = f"Fed1y {art.get('as_of') or art.get('date')}；FRED {g('us2y', 'date')}/{g('effr', 'date')}"
    r["a2"]["as_of"] = f"FRED {g('us2y', 'date')}"
    r["a3"]["as_of"] = f"TLT {g('tlt', 'date')}；DGS30 {g('us30y', 'date')}"
    r["b"] = q4.judge_b(g("eurusd", "chg20_pct"), bund.get("chg20_bp"), g("wti", "chg20_pct"), date_str)
    r["b"]["as_of"] = f"EURUSD {g('eurusd', 'date')}；Bund {bund.get('date')}；WTI {g('wti', 'date')}"
    r["c"] = q4.judge_c(g("us3m", "last"), g("t10yie", "last"), g("dxy", "chg20_pct"), g("spy", "drop_days_20d"),
                        g("spy", "maxdd_20d_pct"), g("spy", "chg20_pct"), g("rsp", "chg20_pct"))
    r["c"]["as_of"] = f"FRED {g('us3m', 'date')}/{g('t10yie', 'date')}；DXY {g('dxy', 'date')}；SPY {g('spy', 'date')}"
    r["d"] = q4.judge_d(g("dbc", "chg20_pct"))
    r["d"]["as_of"] = f"DBC {g('dbc', 'date')}"
    r["e"] = q4.judge_e(h8.get("yoy_pct"), h8.get("yoy_13w_ago_pct"))
    r["e"]["as_of"] = f"H.8 {h8.get('date')}；HY OAS {g('hy_oas', 'date')}；CP {(out.get('cp') or {}).get('date')}"
    r["f"] = q4.judge_f(g("us10y", "chg5_bp"), g("dxy", "chg5_pct"), g("gold", "chg5_pct"), g("spy", "chg5_pct"))
    r["f"]["as_of"] = f"FRED {g('us10y', 'date')}；DXY/金/SPY {g('spy', 'date')}"
    return r


def _n(x, fmt="{:.2f}", na="缺失"):
    return na if x is None else fmt.format(x)


def _s(x, fmt="{:+.1f}%", na="缺失"):
    return na if x is None else fmt.format(x)


def render(p: Dict[str, Any]) -> str:
    L: List[str] = [f"## ⑦ 晚报旁证 8–11（观察，不进合成 min）· {p['date']}", ""]
    sh = p.get("shock")
    if sh:
        fp = sh.get("fingerprints") or {}
        legs = sh.get("legs") or {}
        hit = [f"{n} z{(legs.get(k) or {}).get('z')}" for k, n in (("rate", "利率"), ("dxy", "美元"), ("credit", "信用"), ("vol", "波动")) if (legs.get(k) or {}).get("hit")]
        L.append(f"**8. 冲击吸收旁证**：{sh.get('fragility_state')}（L{sh.get('fragility_level')}，主软肋 {sh.get('weak_link')}）；今日 {sh.get('day_text')}"
                 + (f"（{'、'.join(hit)}）" if hit else "")
                 + f"；BTC金丝雀{'报警' if fp.get('btc_canary') else '平静'}，保证金抛售{'出现' if fp.get('margin_call') else '未出现'}，股债汇三杀{'出现' if fp.get('sell_america') else '未出现'}。")
    else:
        L.append("**8. 冲击吸收旁证**：数据缺失。")
    art = p.get("article")
    if art:
        tv = art.get("tv_crosscheck") or {}
        w = art.get("wti") or {}
        L.append(f"**9. 思路观测旁证**：Fed 1y 加息定价 {art.get('fed_1y_hike_bp')}bp（{art.get('fed_alert_level')}）；"
                 f"TV {tv.get('tv_symbol')}={tv.get('tv_price')} → {tv.get('tv_1y_hike_bp')}bp（Δ{_s(tv.get('delta_bp'), '{:+.1f}bp')}）；"
                 f"WTI {w.get('wti_last')}（线 {w.get('narrative_line')}，{'已过' if w.get('above_narrative_line') else '未过'}）。仅旁证，不进 min。")
    else:
        L.append("**9. 思路观测旁证**：数据缺失。")
    cp, h8 = p.get("cp") or {}, p.get("h8") or {}
    if cp.get("last") is not None:
        L.append(f"**10. 美国商业票据**：CP 余额 {cp['last']:.1f} 十亿美元（约 {cp['last']/1000:.3f} 万亿；FRED COMPOUT，{cp['date']}），"
                 f"周变化 {_s(cp.get('wchg'), '{:+.1f}')} 十亿美元；银行信贷 H.8 总量 {_n(h8.get('last'), '{:.1f}')} 十亿美元（TOTBKCR，{h8.get('date')}），"
                 f"周 {_s(h8.get('wchg'), '{:+.1f}')}，同比 {_s(h8.get('yoy_pct'), '{:.2f}%')}（4 周前 {_s(h8.get('yoy_4w_ago_pct'), '{:.2f}%')}）。")
    else:
        L.append("**10. 美国商业票据**：FRED 数据缺失。")
    px, v = p.get("px") or {}, p.get("verdicts") or {}
    g = lambda k: px.get(k) or {}  # noqa: E731
    bund = p.get("bund10y") or {}
    q = p.get("q4") or {}
    qv = lambda k: (q.get(k) or {}).get("verdict", q4.MISSING)  # noqa: E731
    art = p.get("article") or {}
    h8 = p.get("h8") or {}
    cp = p.get("cp") or {}
    L += ["", f"**11. 用户 Q4 判断对照**（规则 v2 = {q4.RULES_VERSION}：阈值由观点的经济含义推导，不拟合历史手写；"
              f"趋势窗口 {q4.W_TREND} 交易日、信用 {q4.W_CREDIT} 周、冲击 {q4.W_SHOCK} 交易日；仅观察，不进 min、不改任何腿）", ""]
    a1 = q.get("a1") or {}
    L.append(f"- a1 加息路径（26 再加一次/27 两次）：**{qv('a1')}**。Fed 1y 加息定价 {_n(art.get('fed_1y_hike_bp'), '{:.1f}')}bp，"
             f"2Y {_n(g('us2y').get('last'))}% − EFFR {_n(g('effr').get('last'))}% = {_s(a1.get('us2y_effr_bp'), '{:+.0f}bp')}。"
             f"阈值：1y ≥50bp 且 2Y−EFFR ≥+25bp 支持；1y ≤25bp 或 2Y−EFFR ≤0 证伪。as_of：{a1.get('as_of')}")
    a2 = q.get("a2") or {}
    L.append(f"- a2 2–5Y 约 5%：**{qv('a2')}**。2Y {_n(g('us2y').get('last'))}% / 5Y {_n(g('us5y').get('last'))}%，均值 {_n(a2.get('mean_2y5y'), '{:.2f}')}%"
             f"（20D 2Y {_s(g('us2y').get('chg20_bp'), '{:+.0f}bp')}、5Y {_s(g('us5y').get('chg20_bp'), '{:+.0f}bp')}）。阈值：≥4.75% 支持；<4.50% 证伪。as_of：{a2.get('as_of')}")
    a3 = q.get("a3") or {}
    L.append(f"- a3 长债暂避：**{qv('a3')}**。TLT {_n(g('tlt').get('last'))}（20D {_s(g('tlt').get('chg20_pct'))}），30Y {_n(g('us30y').get('last'))}%（20D {_s(g('us30y').get('chg20_bp'), '{:+.0f}bp')}），"
             f"10Y {_n(g('us10y').get('last'))}%（20D {_s(g('us10y').get('chg20_bp'), '{:+.0f}bp')}），10Y−2Y {_s((g('us10y').get('last') - g('us2y').get('last')) * 100 if g('us10y').get('last') is not None and g('us2y').get('last') is not None else None, '{:+.0f}bp')}。"
             f"阈值：TLT 20D ≤-2% 或 30Y 20D ≥+15bp 支持；TLT 20D ≥+3% 证伪。as_of：{a3.get('as_of')}")
    b_ = q.get("b") or {}
    L.append(f"- b EURUSD/欧债/能源可交易：**{qv('b')}**。EURUSD {_n(g('eurusd').get('last'), '{:.4f}')}（20D {_s(g('eurusd').get('chg20_pct'))}），"
             f"Bund10Y {_n(bund.get('last'))}%（20D {_s(bund.get('chg20_bp'), '{:+.0f}bp')}；{bund.get('source') or '缺失'}），WTI {_n(g('wti').get('last'))}（20D {_s(g('wti').get('chg20_pct'))}），"
             f"Brent {_n(g('brent').get('last'))}（20D {_s(g('brent').get('chg20_pct'))}，旁证）；达标腿 {'、'.join(b_.get('legs_hit') or []) or '无'}。"
             f"阈值：|20D| EURUSD ≥2% / Bund ≥20bp / WTI ≥10%，≥2 腿支持；{q4.B_DEADLINE} 前不判证伪。as_of：{b_.get('as_of')}")
    c_ = q.get("c") or {}
    cs = c_.get("subs") or {}
    L.append(f"- c 现金贵/美元强/流动性回撤/指数优于个股：**{qv('c')}**（c1 {cs.get('c1')}，c2 {cs.get('c2')}，c3 {cs.get('c3')}，c4 {cs.get('c4')}）。"
             f"3M {_n(g('us3m').get('last'))}% − 10Y 盈亏平衡 {_n(g('t10yie').get('last'))}% = 实际现金 {_s(c_.get('real_cash_pp'), '{:+.2f}pp')}；"
             f"DXY {_n(g('dxy').get('last'))}（20D {_s(g('dxy').get('chg20_pct'))}）；SPY 20 日内 ≤-1.5% 日 {g('spy').get('drop_days_20d') if g('spy').get('drop_days_20d') is not None else '缺失'} 次、最大回撤 {_s(g('spy').get('maxdd_20d_pct'))}；"
             f"SPY 20D {_s(g('spy').get('chg20_pct'))} vs RSP {_s(g('rsp').get('chg20_pct'))}；旁证 EFFR {_n(g('effr').get('last'))}% / SOFR {_n(g('sofr').get('last'))}%、VIX {_n(g('vix').get('last'))}。"
             f"阈值见规则表。as_of：{c_.get('as_of')}")
    d_ = q.get("d") or {}
    L.append(f"- d 大宗不拖：**{qv('d')}**。DBC {_n(g('dbc').get('last'))}（20D {_s(g('dbc').get('chg20_pct'))}）；宽度 WTI 20D {_s(g('wti').get('chg20_pct'))}、铜 20D {_s(g('copper').get('chg20_pct'))}、金 20D {_s(g('gold').get('chg20_pct'))}。"
             f"阈值：DBC 20D ≥+4% 支持；≤-4% 证伪。as_of：{d_.get('as_of')}")
    e_ = q.get("e") or {}
    L.append(f"- e 信用斜率放缓：**{qv('e')}**。H.8 同比 {_s(h8.get('yoy_pct'), '{:.2f}%')}，13 周前 {_s(h8.get('yoy_13w_ago_pct'), '{:.2f}%')}，斜率 {_s(e_.get('slope_pp'), '{:+.2f}pp')}；"
             f"旁证 HY OAS {_n(g('hy_oas').get('last'))}%（20D {_s(g('hy_oas').get('chg20_bp'), '{:+.0f}bp')}），CP 周 {_s(cp.get('wchg'), '{:+.1f}')} 十亿美元，HYG 20D {_s(g('hyg').get('chg20_pct'))} vs LQD {_s(g('lqd').get('chg20_pct'))}。"
             f"阈值：13 周同比变化 ≤-0.25pp 支持；≥+0.25pp 证伪。as_of：{e_.get('as_of')}")
    f_ = q.get("f") or {}
    L.append(f"- f 三重流动性利空（冲击诊断）：**{qv('f')}**（{f_.get('n', '缺')}/3）。10Y 5D {_s(g('us10y').get('chg5_bp'), '{:+.0f}bp')}，DXY 5D {_s(g('dxy').get('chg5_pct'))}，"
             f"金 5D {_s(g('gold').get('chg5_pct'))} vs SPY 5D {_s(g('spy').get('chg5_pct'))}。阈值：10Y ≤-5bp、DXY ≤+0.5%、金 ≤-1% 且弱于 SPY。as_of：{f_.get('as_of')}")
    if p.get("gaps"):
        L += ["", "> 缺口：" + "；".join(p["gaps"])]
    L += ["", "> Q4 规则 v2（`scripts/q4_view_rules.py`）阈值理由写在 THRESHOLD_NOTES；v1（反推手写）已废弃。判读仅观察，不进合成 min、不改任何腿，非交易信号。", ""]
    return "\n".join(L)


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", required=True, help="YYYY-MM-DD（美股已收盘日）")
    ap.add_argument("--report-dir", default=str(REPO_ROOT / "output"))
    ap.add_argument("--bund10y", default=None, help="手工覆盖 DE10Y 收益率 当前[,5日前]（优先于 Bundesbank 自动源）")
    a = ap.parse_args(argv)
    dt.date.fromisoformat(a.date)
    bund = None
    import os
    a.bund10y = a.bund10y or os.environ.get("GYBOT_BUND10Y", "").strip() or None
    if a.bund10y:
        parts = [float(x) for x in a.bund10y.split(",")]
        bund = (parts[0], parts[1] if len(parts) > 1 else None)
    rd = Path(a.report_dir)
    p = build(a.date, rd, bund=bund)
    (rd / f"evening_sections_{a.date}.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    (rd / f"evening_sections_{a.date}.md").write_text(render(p), encoding="utf-8")
    print(render(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
