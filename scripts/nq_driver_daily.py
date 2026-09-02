#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NQ Driver daily observer (proxy of TradingView Pine NQ动因结构状态机 V3).

Produces observation-only artifacts:
  nq_driver_<date>.json + nq_driver_<date>.md

Does NOT emit trade signals. Designed to feed daily_macro_consolidated as a
fourth cross-check leg alongside denominator / tech dampener / theme SM.

Usage:
  python scripts/nq_driver_daily.py --date 2026-07-31
  python scripts/nq_driver_daily.py --date 2026-07-31 --force-refresh
  python scripts/nq_driver_daily.py --date 2026-07-31 --report-dir ../output
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("nq-driver-daily")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = REPO_ROOT.parent / "output"
CACHE_DIR = REPO_ROOT / "data" / "cache" / "nq_driver"
DEFAULT_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
_PROXY_ENV_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY")

# yfinance symbols (daily proxy; Pine uses futures/TIP live)
SYMBOLS = {
    "nq": ("QQQ", "纳指代理QQQ"),
    "es": ("SPY", "标普代理SPY"),
    "rty": ("IWM", "小盘代理IWM"),
    "soxx": ("SOXX", "半导体SOXX"),
    "tlt": ("TLT", "长债代理TLT"),  # bond price up = yields down
    "tip": ("TIP", "TIPS ETF"),
    "uup": ("UUP", "美元代理UUP"),
    "fxy": ("FXY", "日元代理FXY"),  # JPY up vs USD when FXY up
    "btc": ("BTC-USD", "比特币"),
    "gld": ("GLD", "黄金"),
    "hyg": ("HYG", "高收益债"),
}

# 15 states aligned with Pine manual (section 7)
STATE_META: Dict[int, Dict[str, str]] = {
    1: {
        "name": "AI扩张·引擎带队",
        "side": "bull_theme",
        "bias": "risk_on",
        "dont_do": "在引擎（半导）转弱时仍按全面牛市加仓",
        "invalid": "SOXX 相对 NQ 转负且利率压力抬头",
    },
    2: {
        "name": "贴现率顺风",
        "side": "bull_theme",
        "bias": "risk_on",
        "dont_do": "把债牛当成可以无限拉估值的许可证而忽视广度",
        "invalid": "真实利率/名义利率重新上行",
    },
    3: {
        "name": "广泛风险偏好",
        "side": "bull_theme",
        "bias": "risk_on",
        "dont_do": "在全面风偏里只敢买最窄科技而踏空质量宽基",
        "invalid": "IWM/BTC 掉队且 HYG 转弱",
    },
    4: {
        "name": "财政融涨",
        "side": "bull_theme",
        "bias": "mixed",
        "dont_do": "忽视波动把融涨当无风险牛市满仓追高",
        "invalid": "美元转强且黄金回吐、NQ 跟跌",
    },
    5: {
        "name": "窄幅领涨·AI独舞",
        "side": "bull_theme",
        "bias": "risk_on",
        "dont_do": "把 NQ 独强外推为全面牛市去买最弱小票",
        "invalid": "QQQ-SPY 差收敛或 SOXX 领跌",
    },
    6: {
        "name": "去杠杆·套息平仓",
        "side": "bear_theme",
        "bias": "risk_off",
        "dont_do": "接飞刀抄底科技/高估值；在日元急升中左侧加杠杆",
        "invalid": "日元升值动能衰竭且 NQ 止跌",
    },
    7: {
        "name": "折现率压制",
        "side": "bear_theme",
        "bias": "risk_off",
        "dont_do": "利率与美元齐升时仍按成长股无限久期逻辑加仓",
        "invalid": "利率回落、美元走软",
    },
    8: {
        "name": "结构恶化·引擎失速",
        "side": "bear_theme",
        "bias": "risk_off",
        "dont_do": "半导先坏时仍加仓 NQ 贝塔赌修复",
        "invalid": "SOXX 相对 NQ 修复并放量",
    },
    9: {
        "name": "轮动流出",
        "side": "bear_theme",
        "bias": "mixed",
        "dont_do": "把 NQ 调整当全面崩盘去空宽基/小盘",
        "invalid": "SPY/IWM 跟跌确认撤退",
    },
    10: {
        "name": "增长恐慌",
        "side": "bear_theme",
        "bias": "risk_off",
        "dont_do": "把衰退交易里的反弹当新牛起点满仓",
        "invalid": "商品止跌且信用利差改善、股债关系切换",
    },
    11: {
        "name": "顶压上涨",
        "side": "cover_bull",
        "bias": "mixed",
        "dont_do": "空头打法抢空（顶压上涨挂牌期间）",
        "invalid": "覆盖态投降：NQ 转弱且空方力量重新主导",
    },
    12: {
        "name": "利好失灵",
        "side": "cover_bear",
        "bias": "risk_off",
        "dont_do": "顺风里抄底；把下跌当普通回撤加仓",
        "invalid": "顺风重新被价格确认（放量收复）",
    },
    13: {
        "name": "力量酝酿",
        "side": "transition",
        "bias": "none",
        "dont_do": "理由已足但价未动时提前重仓押方向",
        "invalid": "价格跟上主导力量或力量散掉",
    },
    14: {
        "name": "价格先行",
        "side": "transition",
        "bias": "none",
        "dont_do": "价先动理由未到时追涨杀跌当趋势已确认",
        "invalid": "出现可解释主题并持续，或价格回到中性",
    },
    15: {
        "name": "混沌",
        "side": "transition",
        "bias": "none",
        "dont_do": "在无主题窗口硬编故事并重仓",
        "invalid": "任一主题分数重新拉开",
    },
}


def _ensure_proxy() -> None:
    if any(os.environ.get(k) for k in _PROXY_ENV_KEYS):
        return
    if DEFAULT_PROXY:
        os.environ.setdefault("HTTPS_PROXY", DEFAULT_PROXY)
        os.environ.setdefault("HTTP_PROXY", DEFAULT_PROXY)


def _download_close(ticker: str, period: str = "2y"):
    try:
        import yfinance as yf
        import pandas as pd
    except Exception as exc:
        logger.warning("yfinance/pandas import failed: %s", exc)
        return None
    _ensure_proxy()
    try:
        raw = yf.download(ticker, period=period, auto_adjust=True, progress=False, threads=False)
    except Exception as exc:
        logger.warning("download %s failed: %s", ticker, exc)
        return None
    if raw is None or getattr(raw, "empty", True):
        return None
    try:
        cols = raw.columns
        if getattr(cols, "nlevels", 1) > 1:
            if ("Close", ticker) in cols:
                close = raw[("Close", ticker)]
            elif (ticker, "Close") in cols:
                close = raw[(ticker, "Close")]
            elif "Close" in cols.get_level_values(0):
                cd = raw.xs("Close", axis=1, level=0)
                close = cd[ticker] if ticker in cd.columns else cd.iloc[:, 0]
            else:
                return None
        else:
            close = raw["Close"] if "Close" in cols else None
        if close is None:
            return None
        s = pd.to_numeric(close, errors="coerce").dropna()
        s.name = ticker
        return s if len(s) >= 40 else None
    except Exception as exc:
        logger.warning("parse %s failed: %s", ticker, exc)
        return None


def _read_cache(ticker: str, max_age_seconds: int = 86400):
    import pandas as pd

    p = CACHE_DIR / f"{ticker.replace('=', '_').replace('^', '')}.csv"
    if not p.exists():
        return None
    try:
        if dt.datetime.now().timestamp() - p.stat().st_mtime > max_age_seconds:
            return None
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        if "Close" not in df.columns:
            return None
        s = pd.to_numeric(df["Close"], errors="coerce").dropna()
        return s if len(s) >= 40 else None
    except Exception:
        return None


def _write_cache(ticker: str, s) -> None:
    try:
        import pandas as pd

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        safe = ticker.replace("=", "_").replace("^", "")
        pd.DataFrame({"Close": s}).to_csv(CACHE_DIR / f"{safe}.csv")
    except Exception as exc:
        logger.warning("cache write %s failed: %s", ticker, exc)


def load_closes(force_refresh: bool = False) -> Dict[str, Any]:
    closes: Dict[str, Any] = {}
    for key, (tkr, label) in SYMBOLS.items():
        s = None if force_refresh else _read_cache(tkr)
        if s is None:
            s = _download_close(tkr)
            if s is not None:
                _write_cache(tkr, s)
        if s is None:
            logger.warning("无数据: %s (%s)", label, tkr)
        else:
            closes[key] = s
            logger.info("已获取 %s (%s): %d 根", label, tkr, len(s))
    return closes


def zscore_ret(close, k1: int = 5, k2: int = 60):
    import pandas as pd
    import numpy as np

    r = close / close.shift(k1) - 1.0
    m = r.rolling(k2).mean()
    s = r.rolling(k2).std()
    out = (r - m) / s.replace(0, np.nan)
    return out.fillna(0.0)


def last_z(closes: Dict[str, Any], key: str, k1: int = 5) -> Optional[float]:
    s = closes.get(key)
    if s is None:
        return None
    z = zscore_ret(s, k1=k1)
    if z is None or len(z) == 0:
        return None
    v = float(z.iloc[-1])
    return v if math.isfinite(v) else None


def corr_nq_bond(closes: Dict[str, Any], window: int = 60) -> Optional[float]:
    import pandas as pd
    import numpy as np

    nq, tlt = closes.get("nq"), closes.get("tlt")
    if nq is None or tlt is None:
        return None
    df = pd.concat([nq.pct_change(), tlt.pct_change()], axis=1, join="inner").dropna()
    if len(df) < window + 5:
        return None
    c = df.iloc[:, 0].rolling(window).corr(df.iloc[:, 1]).iloc[-1]
    return float(c) if math.isfinite(float(c)) else None


def score_themes(z: Dict[str, Optional[float]]) -> Dict[str, float]:
    """Return bull/bear theme soft scores (0..+)."""
    def g(k, default=0.0):
        v = z.get(k)
        return default if v is None else float(v)

    nq, es, rty, soxx = g("nq"), g("es"), g("rty"), g("soxx")
    tlt, tip, uup, fxy = g("tlt"), g("tip"), g("uup"), g("fxy")
    btc, gld, hyg = g("btc"), g("gld"), g("hyg")
    # tip up ~ real rate down for TIPS price; treat -tip as real-rate pressure proxy
    real_tight = -tip
    rates_up = -tlt  # TLT down => yields up
    jpy_up = fxy

    scores = {
        "AI扩张·引擎带队": max(0.0, soxx - max(nq, 0) * 0.3) + max(0.0, nq) * 0.5 + max(0.0, -rates_up) * 0.3,
        "贴现率顺风": max(0.0, tlt) + max(0.0, tip) + max(0.0, nq) * 0.4,
        "广泛风险偏好": max(0.0, nq) + max(0.0, es) + max(0.0, rty) + max(0.0, btc) * 0.5,
        "财政融涨": max(0.0, -uup) + max(0.0, gld) + max(0.0, rates_up) * 0.4 + max(0.0, nq) * 0.4,
        "窄幅领涨·AI独舞": max(0.0, nq - es) + max(0.0, nq - rty) * 0.5 - max(0.0, -nq),
        "去杠杆·套息平仓": max(0.0, jpy_up) + max(0.0, -nq) + max(0.0, -btc) * 0.5,
        "折现率压制": max(0.0, rates_up) + max(0.0, uup) + max(0.0, -nq) * 0.6,
        "结构恶化·引擎失速": max(0.0, nq - soxx) + max(0.0, -soxx) + max(0.0, -nq) * 0.3,
        "轮动流出": max(0.0, -nq) + max(0.0, es) * 0.5 + max(0.0, rty) * 0.3 - max(0.0, -es),
        "增长恐慌": max(0.0, uup) + max(0.0, tlt) + max(0.0, -nq) + max(0.0, -hyg) * 0.5,
    }
    # floor at 0
    return {k: float(max(0.0, v)) for k, v in scores.items()}


def pick_state(
    scores: Dict[str, float],
    z: Dict[str, Optional[float]],
    bond_corr: Optional[float],
) -> Tuple[int, Dict[str, Any]]:
    def g(k, default=0.0):
        v = z.get(k)
        return default if v is None else float(v)

    nq = g("nq")
    bull_names = ["AI扩张·引擎带队", "贴现率顺风", "广泛风险偏好", "财政融涨", "窄幅领涨·AI独舞"]
    bear_names = ["去杠杆·套息平仓", "折现率压制", "结构恶化·引擎失速", "轮动流出", "增长恐慌"]
    bull = sorted(((n, scores[n]) for n in bull_names), key=lambda x: -x[1])
    bear = sorted(((n, scores[n]) for n in bear_names), key=lambda x: -x[1])
    top_bull, sb = bull[0]
    top_bear, se = bear[0]
    net = sb - se

    # Cover states
    # 顶压上涨: NQ up while bear force material and rates/jpy pressure
    if nq > 0.4 and se >= 0.9 and se >= sb * 0.85:
        sid = 11
    # 利好失灵: NQ down while bull force material
    elif nq < -0.4 and sb >= 0.9 and sb >= se * 0.85:
        sid = 12
    elif max(sb, se) < 0.55:
        # transitions
        if abs(nq) >= 0.7 and max(sb, se) < 0.8:
            sid = 14  # price first
        elif max(sb, se) >= 0.4 and abs(nq) < 0.35:
            sid = 13  # force brewing
        else:
            sid = 15  # chaos
    elif sb >= se:
        name_to_id = {STATE_META[i]["name"]: i for i in range(1, 6)}
        sid = name_to_id[top_bull]
    else:
        name_to_id = {STATE_META[i]["name"]: i for i in range(6, 11)}
        sid = name_to_id[top_bear]

    # participation
    parts = [g("nq"), g("es"), g("rty"), g("soxx")]
    pos = sum(1 for p in parts if p > 0.25)
    neg = sum(1 for p in parts if p < -0.25)
    if pos >= 3:
        participation = "broad_risk_on"
    elif neg >= 3:
        participation = "broad_risk_off"
    elif g("nq") > 0.4 and g("es") < 0.1 and g("rty") < 0.1:
        participation = "nq_solo"
    else:
        participation = "mixed"

    # attitude
    if nq > 0.3 and sb > se:
        attitude = "涨得有依据"
    elif nq > 0.3 and se > sb:
        attitude = "逆着压力在涨"
    elif nq < -0.3 and se > sb:
        attitude = "跌得有依据"
    elif nq < -0.3 and sb > se:
        attitude = "顺风里下跌"
    elif abs(nq) < 0.25 and max(sb, se) >= 0.6:
        attitude = "理由足价未动"
    elif abs(nq) >= 0.5 and max(sb, se) < 0.5:
        attitude = "价先动理由未到"
    else:
        attitude = "中性纠缠"

    # bond regime: corr(NQ, TLT). TLT up with NQ up => growth-ish; classic risk-on is often neg corr with yields
    # Using corr(NQ returns, TLT returns): positive => stocks and bonds same direction (growth/liquidity)
    if bond_corr is None:
        bond_regime = "unknown"
    elif bond_corr >= 0.15:
        bond_regime = "growth_led"  # 增长当家 proxy
    elif bond_corr <= -0.15:
        bond_regime = "rates_led"  # 利率当家 proxy
    else:
        bond_regime = "neutral"

    dominant = top_bull if sb >= se else top_bear
    opposing = top_bear if sb >= se else top_bull
    opp_over = (se > sb * 1.15 and sid <= 5) or (sb > se * 1.15 and 6 <= sid <= 10)

    meta = STATE_META[sid]
    playbook = _playbook(sid, meta["bias"], attitude)
    veto = _veto(sid, meta, z, opp_over)

    return sid, {
        "state_id": sid,
        "state_name": meta["name"],
        "side": meta["side"],
        "risk_bias": meta["bias"],
        "net_force": round(net, 3),
        "dominant_theme": dominant,
        "dominant_score": round(max(sb, se), 3),
        "opposing_theme": opposing,
        "opposing_score": round(min(sb, se), 3),
        "opposing_over_dominant": bool(opp_over),
        "attitude": attitude,
        "participation": participation,
        "bond_regime": bond_regime,
        "bond_corr_nq_tlt_60d": None if bond_corr is None else round(bond_corr, 3),
        "dont_do": meta["dont_do"],
        "invalid_if": meta["invalid"],
        "playbook": playbook,
        "driver_mod": veto["driver_mod"],
        "veto": veto["veto"],
        "veto_reason": veto["reason"],
        "theme_scores": {k: round(v, 3) for k, v in scores.items()},
    }


def _playbook(sid: int, bias: str, attitude: str) -> str:
    if sid == 11:
        return "覆盖多：空头打法暂停，等投降信号"
    if sid == 12:
        return "覆盖空：多头打法暂停，不抄底"
    if sid == 6:
        return "去杠杆：降杠杆观望，不接飞刀"
    if sid in (13, 14, 15):
        return "过渡：轻仓或观望，等主题确认"
    if bias == "risk_on":
        return "顺风框架：仅在失效条件未触发时持有/回撤买（非信号）"
    if bias == "risk_off":
        return "逆风框架：减风险、避免左侧加仓（非信号）"
    return "混合：控制仓位，禁做优先"


def _veto(sid: int, meta: Dict[str, str], z: Dict[str, Optional[float]], opp_over: bool) -> Dict[str, Any]:
    def g(k, default=0.0):
        v = z.get(k)
        return default if v is None else float(v)

    # delever core
    if g("fxy") > 1.0 and g("nq") < -0.5 and g("btc") < 0:
        return {
            "veto": True,
            "driver_mod": "否决级·去杠杆核心",
            "reason": "日元急升+NQ弱+BTC弱",
        }
    if sid == 12:
        return {"veto": True, "driver_mod": "否决级·利好失灵", "reason": "顺风下跌覆盖态"}
    if sid == 11:
        return {"veto": False, "driver_mod": "空头暂停·顶压上涨", "reason": "逆空方力量上涨"}
    if opp_over:
        return {
            "veto": False,
            "driver_mod": "减半·对抗力压过主导",
            "reason": "状态可能靠切换惯性",
        }
    if meta["bias"] == "risk_off":
        return {"veto": False, "driver_mod": "逆风减半", "reason": meta["name"]}
    if meta["bias"] == "risk_on":
        return {"veto": False, "driver_mod": "顺风足额（观察）", "reason": meta["name"]}
    return {"veto": False, "driver_mod": "中性", "reason": meta["name"]}


def sensors(z: Dict[str, Optional[float]], bond_corr: Optional[float]) -> List[Dict[str, Any]]:
    def row(name, key, note=""):
        v = z.get(key)
        return {
            "name": name,
            "z": None if v is None else round(float(v), 3),
            "note": note,
        }

    rows = [
        row("NQ强度", "nq"),
        row("真实利率代理(-TIP)", "tip", "z 为 TIP 价格强度；解读时注意方向"),
        row("债券价格TLT", "tlt", "上涨≈利率下行"),
        row("美元UUP", "uup"),
        row("半导体SOXX", "soxx"),
        row("日元FXY", "fxy", "急升≈套息平仓压力"),
        row("小盘IWM", "rty"),
        row("BTC", "btc"),
    ]
    rows.append(
        {
            "name": "股债60日相关(NQ,TLT)",
            "z": None if bond_corr is None else round(bond_corr, 3),
            "note": "正≈增长/流动性同向；负≈利率当家倾向",
        }
    )
    return rows


def build_payload(date_str: str, closes: Dict[str, Any]) -> Dict[str, Any]:
    z = {k: last_z(closes, k) for k in SYMBOLS}
    # engine relative: soxx - nq
    if z.get("soxx") is not None and z.get("nq") is not None:
        z["engine_rel"] = float(z["soxx"]) - float(z["nq"])
    else:
        z["engine_rel"] = None
    bond_corr = corr_nq_bond(closes)
    scores = score_themes(z)
    sid, body = pick_state(scores, z, bond_corr)
    missing = [k for k, v in z.items() if v is None and k in SYMBOLS]
    quality = "ok" if len(missing) <= 1 else ("degraded" if len(missing) <= 3 else "bad")

    return {
        "date": date_str,
        "schema": "macro-os.nq-driver-daily.v1",
        "source": "daily_proxy_yfinance",
        "note": "Pine NQ动因 V3 的日频代理，非日内面板；观察 only",
        "quality": {
            "data_quality": quality,
            "missing_legs": missing,
            "as_of": date_str,
        },
        "z_legs": {k: None if v is None else round(float(v), 3) for k, v in z.items()},
        "sensors": sensors(z, bond_corr),
        "driver": body,
        "risk_bias": body["risk_bias"],
        "pressure_override": body["state_id"] in (6, 11, 12) or body.get("veto", False),
        "dont_do": body["dont_do"],
    }


def render_md(p: Dict[str, Any]) -> str:
    d = p["driver"]
    L = [
        f"# NQ 动因日频观察 · {p['date']}",
        "",
        "> **观察 only · 非交易信号**。日频代理 Pine「NQ 动因结构状态机 V3」，不替代盘中面板。",
        "",
        f"- **状态**: **{d['state_name']}** (id={d['state_id']}, {d['side']})",
        f"- **risk_bias**: {d['risk_bias']} ｜ **veto**: {'是' if d.get('veto') else '否'}",
        f"- **动因修正**: {d.get('driver_mod')} — {d.get('veto_reason')}",
        f"- **净力量**: {d.get('net_force')} ｜ 态度: {d.get('attitude')} ｜ 参与度: {d.get('participation')}",
        f"- **主导**: {d.get('dominant_theme')} ({d.get('dominant_score')}) ｜ "
        f"**对抗**: {d.get('opposing_theme')} ({d.get('opposing_score')})"
        + (" ⚠已压过主导" if d.get("opposing_over_dominant") else ""),
        f"- **股债体制**: {d.get('bond_regime')} (corr={d.get('bond_corr_nq_tlt_60d')})",
        f"- **打法框架**: {d.get('playbook')}",
        f"- **今天不做**: {d.get('dont_do')}",
        f"- **失效**: {d.get('invalid_if')}",
        "",
        "## 传感表（日频 z）",
        "",
    ]
    for row in p.get("sensors") or []:
        L.append(f"- {row['name']}: {row.get('z')} {('· ' + row['note']) if row.get('note') else ''}")
    L.extend(["", "## 主题分数", ""])
    for k, v in sorted((d.get("theme_scores") or {}).items(), key=lambda kv: -kv[1]):
        L.append(f"- {k}: {v}")
    L.extend(
        [
            "",
            f"*quality={p.get('quality', {}).get('data_quality')} · schema={p.get('schema')}*",
            "",
        ]
    )
    return "\n".join(L)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="NQ driver daily observer (Pine V3 proxy)")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    ap.add_argument("--force-refresh", action="store_true")
    args = ap.parse_args(argv)

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    date_str = args.date

    closes = load_closes(force_refresh=args.force_refresh)
    if len(closes) < 4:
        logger.error("insufficient market data (%d legs)", len(closes))
        return 2

    # align series to date if possible (use last available <= date)
    import pandas as pd

    trimmed = {}
    target = pd.Timestamp(date_str)
    for k, s in closes.items():
        s2 = s.copy()
        s2.index = pd.to_datetime(s2.index).tz_localize(None)
        s2 = s2[s2.index <= target]
        if len(s2) >= 40:
            trimmed[k] = s2
        else:
            logger.warning("leg %s too short after date trim", k)
    if len(trimmed) < 4:
        trimmed = closes  # fallback

    payload = build_payload(date_str, trimmed)
    md = render_md(payload)
    out_j = report_dir / f"nq_driver_{date_str}.json"
    out_m = report_dir / f"nq_driver_{date_str}.md"
    out_j.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_m.write_text(md, encoding="utf-8")
    logger.info("written %s", out_j)
    logger.info("written %s", out_m)
    try:
        print(md)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(md.encode("utf-8", errors="replace"))
    print(f"\n[written] {out_j}")
    print(f"[written] {out_m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
