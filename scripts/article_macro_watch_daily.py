#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文章思路观测旁证 · 日频（Article Macro Watch · daily headless）

--------------------------------------------------------------------------------
WHY THIS EXISTS
--------------------------------------------------------------------------------
作者叙事线旁证：把「一年累计加息定价 / WTI 绝对价 / 10Y / 盈亏平衡通胀相对均值 /
MOVE」收成一份只读观测 artifact，挂到晨报旁证小节。

定位（对齐冲击吸收旁证）：
  - 只观察 + 报警，不进主状态机
  - **永不**进入 `compute_combined_budget` / 不放松或覆盖分母 `tight→0.35`
  - **不做第六腿**

Fed 一年累计加息定价 bp：
  开源自算 CME 30-Day Fed Funds 结算（优先 `cme-fedwatch`：CME 结算 + FRED
  EFFR/target + 内置 FOMC 日程）。算法版本 `cme_settlement_cum_v1`：
    取 as_of 起 365 日内最远一场 FOMC，对其概率分布取目标区间中点期望，
    减去当前目标区间中点，×100 → bp。
  FRED 维护/拉不到时：NY Fed EFFR API（含 target 上下限）+ 同库 CME 结算自算；
  再不行则用 ZQ=F / ^IRX 估 mid，并用 TV ZQU 价推导 tv_1y_hike_bp 作旁证主值。
  与 CME FedWatch / QuikStrike 官网可能有偏差（日结算 vs 盘中、日程硬编码、
  月末敏感）；接受偏差，失败则整段 Fed degraded，**禁止编造 bp**。

WTI / 10Y / T10YIE：复用 yfinance + FRED 公开 CSV；T10YIE 再回退 `data/_bei_daily.csv`。
MOVE：优先 TradingView CFD scanner（TVC:MOVE）；失败则弃权（与冲击吸收同口径）。

TV 对照（默认列，不进 min）：
  对 ~1y Fed Funds 期货 `CBOT:ZQU2027` 做 TradingView 报价交叉校验。
  优先 `--tv-price` / 环境变量 `ARTICLE_TV_PRICE`；否则轻量 HTTP 拉
  `scanner.tradingview.com/futures/scan` close；失败则 status=
  unavailable / rate_limited，字段置 null，**不阻断**主 CME 旁证。
  公式：implied_rate = 100 - tv_price；
  tv_1y_hike_bp = (implied - current_mid) * 100；
  delta_bp = tv_1y_hike_bp - CME自算 bp。

OUTPUT: output/article_macro_watch_<as_of>.{md,json}
门槛文案（作者叙事线，未标定）：Fed 70/100/110 bp；油 90。
--------------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_MAX_THREADS", "1")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = REPO_ROOT / "output"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("article-macro-watch")

DEFAULT_PROXY = "http://127.0.0.1:7890"
_PROXY_ENV_KEYS = ("HTTPS_PROXY", "HTTP_PROXY")
_PROXY_OFF_VALUES = ("", "off", "none", "0", "false", "no")

ALGO_VERSION = "cme_settlement_cum_v1"
HORIZON_DAYS = 365

# 作者叙事线门槛（观察文案，不进合成 min）
THRESHOLDS = {
    "fed_1y_hike_bp": {"warn": 70, "elevated": 100, "extreme": 110},
    "wti_narrative_line": 90.0,
    "t10yie_mean_lookback_calendar_days": 1095,  # ~3y
}

# TradingView 1y Fed Funds future cross-check (旁证对照列；不进 combined min)
TV_FF_SYMBOL_DEFAULT = "CBOT:ZQU2027"
TV_SCANNER_URL = "https://scanner.tradingview.com/futures/scan"
TV_CFD_SCANNER_URL = "https://scanner.tradingview.com/cfd/scan"
TV_SCANNER_TIMEOUT_SEC = 12
TV_MOVE_SYMBOL = "TVC:MOVE"
NYFED_EFFR_URL = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"
BEI_CACHE_CSV = REPO_ROOT / "data" / "_bei_daily.csv"


def _ensure_proxy() -> None:
    if any(os.environ.get(k) for k in _PROXY_ENV_KEYS):
        return
    sp = os.environ.get("SECTOR_PROXY")
    if sp is not None and str(sp).strip().lower() in _PROXY_OFF_VALUES:
        return
    try:
        proxy = sp if (sp and str(sp).strip()) else DEFAULT_PROXY
        if not (sp and str(sp).strip()):
            # Default local proxy only if it is actually listening; otherwise
            # direct egress (cloud runner without 127.0.0.1:7890).
            import socket
            from urllib.parse import urlparse

            u = urlparse(proxy)
            try:
                socket.create_connection((u.hostname, u.port or 80), timeout=1).close()
            except OSError:
                return
        os.environ.setdefault("HTTPS_PROXY", proxy)
        os.environ.setdefault("HTTP_PROXY", proxy)
    except Exception:
        pass


def _range_midpoint(label: str) -> Optional[float]:
    """Parse '3.75%-4.00%' → 3.875. Returns None on bad label."""
    try:
        s = str(label).strip().replace("%", "")
        a, b = s.split("-", 1)
        return (float(a) + float(b)) / 2.0
    except Exception:
        return None


def _expected_rate_from_probs(probabilities: Dict[str, float]) -> Optional[float]:
    if not probabilities:
        return None
    total_w = 0.0
    acc = 0.0
    for label, pct in probabilities.items():
        mid = _range_midpoint(label)
        if mid is None:
            continue
        w = float(pct) / 100.0
        acc += mid * w
        total_w += w
    if total_w <= 0:
        return None
    return acc / total_w



def _fetch_effr_nyfed(timeout_seconds: float = 12.0) -> Optional[Dict[str, Any]]:
    """NY Fed public EFFR JSON — works when FRED graph CSV is on maintenance.

    Returns dict with effr / target_lo / target_hi / as_of, or None.
    """
    import urllib.error
    import urllib.request

    _ensure_proxy()
    req = urllib.request.Request(
        NYFED_EFFR_URL,
        headers={
            "User-Agent": "macro-os-article-watch/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("NY Fed EFFR fetch failed: %s", exc)
        return None
    rows = payload.get("refRates") if isinstance(payload, dict) else None
    if not rows:
        return None
    row = rows[0]
    try:
        effr = float(row["percentRate"])
    except Exception:
        return None
    lo = row.get("targetRateFrom")
    hi = row.get("targetRateTo")
    try:
        lo_f = float(lo) if lo is not None else None
        hi_f = float(hi) if hi is not None else None
    except Exception:
        lo_f, hi_f = None, None
    return {
        "effr": effr,
        "target_lo": lo_f,
        "target_hi": hi_f,
        "as_of": str(row.get("effectiveDate") or "")[:10] or None,
        "source": "NYFed:EFFR",
    }


def _fetch_effr_zq_proxy() -> Optional[Dict[str, Any]]:
    """Last-resort EFFR/mid proxy from front-month ZQ=F (or ^IRX)."""
    for ticker, kind in (("ZQ=F", "zq"), ("^IRX", "irx")):
        s = _download_close(ticker, period="10d")
        if s is None or len(s) == 0:
            continue
        last = float(s.iloc[-1])
        as_of = str(s.index[-1])[:10]
        if kind == "zq":
            # ZQ price → implied average EFFR for delivery month
            effr = 100.0 - last
        else:
            effr = last
        return {
            "effr": round(effr, 4),
            "target_lo": None,
            "target_hi": None,
            "as_of": as_of,
            "source": f"yfinance:{ticker}",
        }
    return None


def _resolve_effr_context() -> Dict[str, Any]:
    """Resolve EFFR + target band without relying solely on FRED graph CSV."""
    # 1) NY Fed (authoritative when FRED is down; also carries target band)
    ny = _fetch_effr_nyfed()
    if ny and ny.get("effr") is not None:
        lo, hi = ny.get("target_lo"), ny.get("target_hi")
        if lo is None or hi is None:
            try:
                from cme_fedwatch.calc import current_target_range

                lo, hi = current_target_range(float(ny["effr"]))
                ny["target_source"] = "estimated_from_effr"
            except Exception:
                lo = hi = None
        else:
            ny["target_source"] = "nyfed"
        ny["target_lo"], ny["target_hi"] = lo, hi
        return ny

    # 2) Try FRED EFFR via cme-fedwatch (when maintenance ends)
    try:
        from cme_fedwatch.api import fetch_effr, fetch_target_range

        effr = float(fetch_effr())
        try:
            lo, hi = fetch_target_range()
            src = "fred"
        except Exception:
            from cme_fedwatch.calc import current_target_range

            lo, hi = current_target_range(effr)
            src = "estimated_from_effr"
        return {
            "effr": effr,
            "target_lo": float(lo),
            "target_hi": float(hi),
            "as_of": None,
            "source": "FRED:EFFR",
            "target_source": src,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("FRED EFFR/target unavailable: %s", exc)

    # 3) ZQ=F / ^IRX proxy
    prox = _fetch_effr_zq_proxy()
    if prox and prox.get("effr") is not None:
        try:
            from cme_fedwatch.calc import current_target_range

            lo, hi = current_target_range(float(prox["effr"]))
            prox["target_lo"], prox["target_hi"] = lo, hi
            prox["target_source"] = "estimated_from_effr"
            return prox
        except Exception as exc:  # noqa: BLE001
            logger.warning("ZQ/IRX target estimate failed: %s", exc)
    return {
        "effr": None,
        "target_lo": None,
        "target_hi": None,
        "as_of": None,
        "source": None,
        "target_source": None,
    }


def _fed_from_cme_settlements(
    *,
    as_of: dt.date,
    effr: float,
    target_lo: float,
    target_hi: float,
    target_source: str,
    effr_source: str,
    trade_date: Optional[dt.date] = None,
) -> Dict[str, Any]:
    """CME settlements + known EFFR/target → same cum-hike bp shape as get_probabilities."""
    base: Dict[str, Any] = {
        "fed_1y_hike_bp": None,
        "meeting_anchor": None,
        "meetings_in_horizon": [],
        "algo_version": ALGO_VERSION,
        "source": f"cme-settlement+{effr_source}",
        "quality": "degraded",
        "degraded": True,
        "degraded_reason": None,
        "effr": float(effr),
        "current_target": f"{target_lo:.2f}%-{target_hi:.2f}%",
        "trade_date": None,
        "expected_rate_1y": None,
        "current_mid": round((float(target_lo) + float(target_hi)) / 2.0, 4),
        "target_source": target_source,
        "disclaimer": (
            "开源自算 CME 日结算 FedWatch；EFFR/target 来自 FRED 回退源；"
            "与官网 QuikStrike/盘中可能偏差，接受偏差；失败不编造 bp。"
        ),
    }
    try:
        from cme_fedwatch.api import get_settlements
        from cme_fedwatch.calc import calculate
        from cme_fedwatch.fomc import get_upcoming_meetings, schedule_horizon, schedule_status
    except Exception as exc:  # noqa: BLE001
        base["degraded_reason"] = f"cme-fedwatch import failed: {exc}"
        return base

    try:
        settlements = get_settlements(trade_date)
    except Exception as exc:  # noqa: BLE001
        base["degraded_reason"] = f"CME settlements failed: {exc}"
        return base

    cur_mid = float(base["current_mid"])
    td = settlements.trade_date
    base["trade_date"] = td.isoformat()
    try:
        base["schedule_status"] = schedule_status()
    except Exception:
        pass

    meetings_list = get_upcoming_meetings(td)
    raw = calculate(
        settlements.rows, meetings_list, (float(target_lo), float(target_hi)), schedule_horizon()
    )
    horizon_end = as_of + dt.timedelta(days=HORIZON_DAYS)
    in_h: List[Dict[str, Any]] = []
    for m in raw or []:
        try:
            md = dt.date.fromisoformat(str(m.get("date"))[:10])
        except Exception:
            continue
        if md > horizon_end or md < as_of:
            continue
        exp = _expected_rate_from_probs(m.get("probabilities") or {})
        if exp is None:
            continue
        in_h.append(
            {
                "date": md.isoformat(),
                "contract": m.get("contract"),
                "expected_rate": round(float(exp), 4),
                "cum_hike_bp": round((float(exp) - cur_mid) * 100.0, 1),
            }
        )
    if not in_h:
        base["degraded_reason"] = (
            f"no FOMC meetings in [{as_of.isoformat()}, {horizon_end.isoformat()}]"
        )
        return base
    anchor = in_h[-1]
    base.update(
        {
            "fed_1y_hike_bp": float(anchor["cum_hike_bp"]),
            "meeting_anchor": {
                "date": anchor["date"],
                "contract": anchor.get("contract"),
                "horizon_days": HORIZON_DAYS,
            },
            "meetings_in_horizon": in_h,
            "quality": "ok",
            "degraded": False,
            "degraded_reason": None,
            "expected_rate_1y": anchor["expected_rate"],
        }
    )
    return base


def compute_fed_1y_hike_bp(
    *,
    as_of: dt.date,
    trade_date: Optional[dt.date] = None,
) -> Dict[str, Any]:
    """Open-source CME settlement FedWatch → 1y cumulative hike bp.

    On any hard failure returns degraded payload with fed_1y_hike_bp=None
    (never fabricates a number).
    """
    base: Dict[str, Any] = {
        "fed_1y_hike_bp": None,
        "meeting_anchor": None,
        "meetings_in_horizon": [],
        "algo_version": ALGO_VERSION,
        "source": "cme-fedwatch+CME_settlement+FRED_EFFR",
        "quality": "degraded",
        "degraded": True,
        "degraded_reason": None,
        "effr": None,
        "current_target": None,
        "trade_date": None,
        "expected_rate_1y": None,
        "current_mid": None,
        "disclaimer": (
            "开源自算 CME 日结算 FedWatch；与官网 QuikStrike/盘中可能偏差，"
            "接受偏差；失败不编造 bp。"
        ),
    }
    # Prefer stock cme-fedwatch (FRED EFFR). On FRED outage, rebuild from
    # CME settlements + NY Fed / ZQ EFFR context (same algo, no fabricated bp).
    data = None
    primary_err: Optional[str] = None
    try:
        from cme_fedwatch import get_probabilities
    except Exception as exc:  # noqa: BLE001
        primary_err = f"cme-fedwatch import failed: {exc}"
        logger.warning(primary_err)

    if primary_err is None:
        try:
            kwargs: Dict[str, Any] = {}
            if trade_date is not None:
                kwargs["trade_date"] = trade_date
            data = get_probabilities(**kwargs)  # all upcoming meetings
        except Exception as exc:  # noqa: BLE001
            primary_err = f"get_probabilities failed: {exc}"
            logger.warning(primary_err)
            data = None

    if not isinstance(data, dict) or not data.get("meetings"):
        if data is not None and not data.get("meetings"):
            primary_err = primary_err or "empty meetings from cme-fedwatch"
        ctx = _resolve_effr_context()
        if (
            ctx.get("effr") is not None
            and ctx.get("target_lo") is not None
            and ctx.get("target_hi") is not None
        ):
            fb = _fed_from_cme_settlements(
                as_of=as_of,
                effr=float(ctx["effr"]),
                target_lo=float(ctx["target_lo"]),
                target_hi=float(ctx["target_hi"]),
                target_source=str(ctx.get("target_source") or "fallback"),
                effr_source=str(ctx.get("source") or "EFFR_fallback"),
                trade_date=trade_date,
            )
            if fb.get("quality") == "ok":
                if primary_err:
                    fb["fallback_note"] = primary_err
                return fb
            primary_err = (
                f"{primary_err}; settlement_fallback: {fb.get('degraded_reason')}"
                if primary_err
                else f"settlement_fallback: {fb.get('degraded_reason')}"
            )
            # Keep mid/effr even if meetings missing — helps TV bp conversion.
            base.update(
                {
                    "effr": fb.get("effr"),
                    "current_target": fb.get("current_target"),
                    "current_mid": fb.get("current_mid"),
                    "trade_date": fb.get("trade_date"),
                    "target_source": fb.get("target_source"),
                    "source": fb.get("source") or base["source"],
                }
            )
        base["degraded_reason"] = primary_err or "empty meetings from cme-fedwatch"
        return base

    cur_label = data.get("current_target")
    cur_mid = _range_midpoint(cur_label) if cur_label else None
    if cur_mid is None:
        base["degraded_reason"] = f"unparseable current_target: {cur_label!r}"
        base["current_target"] = cur_label
        return base

    td_raw = data.get("trade_date")
    try:
        td = dt.date.fromisoformat(str(td_raw)[:10]) if td_raw else None
    except Exception:
        td = None

    horizon_end = as_of + dt.timedelta(days=HORIZON_DAYS)
    in_h: List[Dict[str, Any]] = []
    for m in data["meetings"]:
        try:
            md = dt.date.fromisoformat(str(m.get("date"))[:10])
        except Exception:
            continue
        if md > horizon_end:
            continue
        if md < as_of:
            continue
        exp = _expected_rate_from_probs(m.get("probabilities") or {})
        if exp is None:
            continue
        in_h.append(
            {
                "date": md.isoformat(),
                "contract": m.get("contract"),
                "expected_rate": round(float(exp), 4),
                "cum_hike_bp": round((float(exp) - cur_mid) * 100.0, 1),
            }
        )

    if not in_h:
        base["degraded_reason"] = (
            f"no FOMC meetings in [{as_of.isoformat()}, {horizon_end.isoformat()}]"
        )
        base["current_target"] = cur_label
        base["current_mid"] = cur_mid
        base["effr"] = data.get("effr")
        base["trade_date"] = td.isoformat() if td else None
        return base

    anchor = in_h[-1]
    bp = float(anchor["cum_hike_bp"])
    base.update(
        {
            "fed_1y_hike_bp": bp,
            "meeting_anchor": {
                "date": anchor["date"],
                "contract": anchor.get("contract"),
                "horizon_days": HORIZON_DAYS,
            },
            "meetings_in_horizon": in_h,
            "quality": "ok",
            "degraded": False,
            "degraded_reason": None,
            "effr": data.get("effr"),
            "current_target": cur_label,
            "current_mid": round(cur_mid, 4),
            "expected_rate_1y": anchor["expected_rate"],
            "trade_date": td.isoformat() if td else None,
            "target_source": data.get("target_source"),
            "schedule_status": data.get("schedule_status"),
        }
    )
    return base


def _download_close(ticker: str, period: str = "1y") -> Optional[Any]:
    import pandas as pd
    import yfinance as yf

    _ensure_proxy()
    try:
        raw = yf.download(
            ticker, period=period, auto_adjust=True, progress=False, threads=False
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance download failed %s: %s", ticker, exc)
        return None
    if raw is None or getattr(raw, "empty", True):
        logger.warning("yfinance empty: %s", ticker)
        return None
    cols = raw.columns
    close = None
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
    s.index = pd.to_datetime(s.index).normalize()
    return s if len(s) >= 5 else None


def _clamp_series_last(series: Any, as_of: dt.date) -> Tuple[Optional[float], Optional[str]]:
    if series is None or len(series) == 0:
        return None, None
    idx = series.index
    try:
        mask = idx.date <= as_of if hasattr(idx, "date") else idx <= pd_Timestamp(as_of)
    except Exception:
        # fallback: compare iso strings
        keep = [i for i in idx if str(i)[:10] <= as_of.isoformat()]
        if not keep:
            return None, None
        last_i = keep[-1]
        return float(series.loc[last_i]), str(last_i)[:10]
    sub = series.loc[mask]
    if sub is None or len(sub) == 0:
        return None, None
    last_i = sub.index[-1]
    return float(sub.iloc[-1]), str(last_i)[:10]


def pd_Timestamp(d: dt.date):
    import pandas as pd
    return pd.Timestamp(d)


def fetch_wti(as_of: dt.date) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "wti_last": None,
        "as_of": None,
        "ticker": "CL=F",
        "source": "yfinance",
        "quality": "degraded",
        "narrative_line": THRESHOLDS["wti_narrative_line"],
        "note": "作者叙事线绝对价门槛 90（观察文案，不进合成 min）",
    }
    s = _download_close("CL=F", period="6mo")
    val, d = _clamp_series_last(s, as_of)
    if val is None:
        # Fallback 1: shared bar_sources chain (yfinance w/ backoff -> TV -> Stooq).
        try:
            sys.path.insert(0, str(REPO_ROOT / "scripts"))
            import bar_sources as bs

            frame, notes, src = bs.fetch_with_fallback("CL=F", None, full_period="6mo", min_bars=1)
            if frame is not None and len(frame):
                val, d = _clamp_series_last(frame["Close"].dropna(), as_of)
                if val is not None:
                    out["source"] = f"bar_sources:{src}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("WTI bar_sources fallback failed: %s", exc)
    if val is None:
        # Fallback 2: FRED DCOILWTICO (spot, ~1-2 day publication lag).
        try:
            sys.path.insert(0, str(REPO_ROOT))
            from adapters.fred import fetch_fred_series

            pts = [p for p in fetch_fred_series("DCOILWTICO", lookback_days=60, timeout_seconds=20)
                   if p.date <= as_of.isoformat()]
            if pts:
                val, d = float(pts[-1].value), pts[-1].date
                out["source"] = "FRED:DCOILWTICO"
                out["ticker"] = "DCOILWTICO"
        except Exception as exc:  # noqa: BLE001
            logger.warning("WTI FRED fallback failed: %s", exc)
    if val is None:
        out["degraded_reason"] = "WTI CL=F/TV/Stooq/FRED all unavailable"
        return out
    out.update(
        {
            "wti_last": round(val, 2),
            "as_of": d,
            "quality": "ok",
            "above_narrative_line": bool(val >= THRESHOLDS["wti_narrative_line"]),
        }
    )
    return out


def fetch_dgs10(as_of: dt.date) -> Dict[str, Any]:
    """Prefer FRED DGS10; fall back to yfinance ^TNX."""
    out: Dict[str, Any] = {
        "dgs10": None,
        "tnx": None,
        "as_of": None,
        "source": None,
        "quality": "degraded",
        "unit": "percent",
    }
    # FRED
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from adapters.fred import fetch_fred_series

        pts = fetch_fred_series("DGS10", lookback_days=400, timeout_seconds=20)
        pts = [p for p in pts if p.date <= as_of.isoformat()]
        if pts:
            last = pts[-1]
            out.update(
                {
                    "dgs10": round(float(last.value), 3),
                    "as_of": last.date,
                    "source": "FRED:DGS10",
                    "quality": "ok",
                }
            )
            return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("FRED DGS10 failed: %s", exc)

    s = _download_close("^TNX", period="6mo")
    val, d = _clamp_series_last(s, as_of)
    if val is None:
        out["degraded_reason"] = "DGS10/TNX unavailable"
        return out
    out.update(
        {
            "dgs10": round(val, 3),
            "tnx": round(val, 3),
            "as_of": d,
            "source": "yfinance:^TNX",
            "quality": "ok",
            "note": "FRED DGS10 missing; using ^TNX proxy",
        }
    )
    return out



def _load_bei_cache(as_of: dt.date, lookback: int) -> Optional[Dict[str, Any]]:
    """Read data/_bei_daily.csv (T10YIE history) when live FRED is down."""
    import csv

    path_csv = BEI_CACHE_CSV
    if not path_csv.is_file():
        return None
    rows: List[Tuple[str, float]] = []
    try:
        with path_csv.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                ds = (row.get("observation_date") or row.get("date") or "").strip()
                raw = (row.get("T10YIE") or row.get("value") or "").strip()
                if not ds or raw in {"", "."}:
                    continue
                if ds > as_of.isoformat():
                    continue
                try:
                    rows.append((ds, float(raw)))
                except ValueError:
                    continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("BEI cache read failed: %s", exc)
        return None
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])
    start = (as_of - dt.timedelta(days=lookback)).isoformat()
    window = [v for d, v in rows if d >= start] or [v for _, v in rows]
    last_d, last_v = rows[-1]
    mean = sum(window) / len(window)
    dev = last_v - mean
    return {
        "t10yie": round(last_v, 3),
        "as_of": last_d,
        "mean": round(mean, 3),
        "n_obs": len(window),
        "deviation": round(dev, 3),
        "deviation_bp": round(dev * 100.0, 1),
        "source": "cache:_bei_daily.csv",
        "quality": "ok",
        "unit": "percent",
        "note": "FRED T10YIE unavailable; using local cache (may lag 1 session)",
    }


def fetch_t10yie(as_of: dt.date) -> Dict[str, Any]:
    lookback = int(THRESHOLDS["t10yie_mean_lookback_calendar_days"])
    out: Dict[str, Any] = {
        "t10yie": None,
        "as_of": None,
        "mean": None,
        "mean_lookback_calendar_days": lookback,
        "deviation": None,
        "deviation_bp": None,
        "source": "FRED:T10YIE",
        "quality": "degraded",
        "unit": "percent",
    }
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from adapters.fred import fetch_fred_series

        pts = fetch_fred_series(
            "T10YIE", lookback_days=lookback + 60, timeout_seconds=25
        )
        pts = [p for p in pts if p.date <= as_of.isoformat()]
        if pts:
            start = (as_of - dt.timedelta(days=lookback)).isoformat()
            window = [p for p in pts if p.date >= start] or pts
            vals = [float(p.value) for p in window]
            mean = sum(vals) / len(vals)
            last = pts[-1]
            last_v = float(last.value)
            dev = last_v - mean
            out.update(
                {
                    "t10yie": round(last_v, 3),
                    "as_of": last.date,
                    "mean": round(mean, 3),
                    "n_obs": len(vals),
                    "deviation": round(dev, 3),
                    "deviation_bp": round(dev * 100.0, 1),
                    "quality": "ok",
                }
            )
            return out
        logger.warning("T10YIE FRED empty (maintenance?); trying local cache")
    except Exception as exc:  # noqa: BLE001
        out["degraded_reason"] = f"T10YIE fetch failed: {exc}"
        logger.warning(out["degraded_reason"])

    # Fallback: local FRED cache written by prefetch / prior good runs
    cached = _load_bei_cache(as_of, lookback)
    if cached is not None:
        out.update(cached)
        return out
    if not out.get("degraded_reason"):
        out["degraded_reason"] = "T10YIE empty (FRED + local cache)"
    return out


def move_abstain(reason: Optional[str] = None) -> Dict[str, Any]:
    return {
        "move": None,
        "quality": "abstain",
        "abstain_reason": reason
        or (
            "MOVE (TVC:MOVE) 为 TV-only，无可靠开源日频代理；"
            "按冲击吸收同口径弃权，不参与任何计算、不编造。"
        ),
    }


def fetch_move_tv(symbol: str = TV_MOVE_SYMBOL) -> Dict[str, Any]:
    """Observational MOVE level via TradingView CFD scanner (not in combined min)."""
    close, status, reason = fetch_tv_scanner_close(symbol, market="cfd")
    if status == "ok" and close is not None:
        return {
            "move": round(float(close), 2),
            "as_of": None,
            "quality": "ok",
            "source": f"tradingview_cfd_scanner:{symbol}",
            "abstain_reason": None,
            "note": "旁证展示用；不进合成 min / 不进冲击吸收计算。",
        }
    return move_abstain(
        reason=(
            f"MOVE TV scanner 不可用（status={status}"
            + (f"；{reason}" if reason else "")
            + "）；按冲击吸收同口径弃权，不编造。"
        )
    )


def _tv_empty(
    *,
    symbol: str,
    status: str,
    reason: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "tv_symbol": symbol,
        "tv_price": None,
        "tv_implied_rate": None,
        "tv_1y_hike_bp": None,
        "delta_bp": None,
        "cme_1y_hike_bp": None,
        "current_mid_used": None,
        "source": source,
        "status": status,
        "degraded_reason": reason,
        "note": (
            "TV 对照：implied=100-price；tv_1y_hike_bp=(implied-current_mid)*100；"
            "delta_bp=tv−CME自算。不进合成 min。"
        ),
    }


def _tv_compute_from_price(
    *,
    symbol: str,
    price: float,
    current_mid: Optional[float],
    cme_bp: Optional[float],
    source: str,
) -> Dict[str, Any]:
    """Build TV cross-check fields from a futures mid/close price."""
    out = _tv_empty(symbol=symbol, status="ok", source=source)
    out["tv_price"] = round(float(price), 4)
    implied = 100.0 - float(price)
    out["tv_implied_rate"] = round(implied, 4)
    mid = float(current_mid) if current_mid is not None else None
    out["current_mid_used"] = round(mid, 4) if mid is not None else None
    out["cme_1y_hike_bp"] = (
        round(float(cme_bp), 1) if isinstance(cme_bp, (int, float)) else None
    )
    if mid is None:
        out["status"] = "unavailable"
        out["degraded_reason"] = "current_mid missing; cannot convert TV price to hike bp"
        return out
    tv_bp = (implied - mid) * 100.0
    out["tv_1y_hike_bp"] = round(tv_bp, 1)
    if out["cme_1y_hike_bp"] is not None:
        out["delta_bp"] = round(out["tv_1y_hike_bp"] - out["cme_1y_hike_bp"], 1)
    return out


def fetch_tv_scanner_close(
    symbol: str, *, market: str = "futures"
) -> Tuple[Optional[float], str, Optional[str]]:
    """Lightweight TradingView scanner HTTP → (close, status, reason).

    market: futures | cfd | america | bond …
    status: ok | unavailable | rate_limited
    Never raises; never fabricates a price.
    """
    import urllib.error
    import urllib.request

    _ensure_proxy()
    url = (
        TV_CFD_SCANNER_URL
        if market == "cfd"
        else (
            TV_SCANNER_URL
            if market == "futures"
            else f"https://scanner.tradingview.com/{market}/scan"
        )
    )
    payload = json.dumps(
        {
            "symbols": {"tickers": [symbol], "query": {"types": []}},
            "columns": ["close", "name", "description"],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "macro-os-article-watch/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TV_SCANNER_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            code = getattr(resp, "status", 200) or 200
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return None, "rate_limited", f"TV scanner HTTP 429 for {symbol}"
        return None, "unavailable", f"TV scanner HTTP {exc.code} for {symbol}: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return None, "unavailable", f"TV scanner fetch failed: {exc}"

    if code == 429:
        return None, "rate_limited", f"TV scanner HTTP 429 for {symbol}"
    try:
        data = json.loads(body)
    except Exception as exc:  # noqa: BLE001
        return None, "unavailable", f"TV scanner JSON parse failed: {exc}"

    rows = data.get("data") if isinstance(data, dict) else None
    if not rows:
        return None, "unavailable", f"TV scanner empty for {symbol}"
    row = rows[0]
    try:
        close = row["d"][0]
        if close is None:
            return None, "unavailable", f"TV scanner close=null for {symbol}"
        return float(close), "ok", None
    except Exception as exc:  # noqa: BLE001
        return None, "unavailable", f"TV scanner row parse failed: {exc}"


def resolve_tv_crosscheck(
    *,
    symbol: str,
    current_mid: Optional[float],
    cme_bp: Optional[float],
    tv_price_override: Optional[float] = None,
    fetch_enabled: bool = True,
) -> Dict[str, Any]:
    """Resolve TV Fed Funds cross-check: CLI/env override → scanner HTTP → abstain."""
    # 1) explicit override (CLI / env) — preferred for Grok Bot MCP handoff
    if tv_price_override is not None:
        try:
            px = float(tv_price_override)
        except (TypeError, ValueError):
            return _tv_empty(
                symbol=symbol,
                status="unavailable",
                reason=f"invalid tv_price override: {tv_price_override!r}",
                source="cli:--tv-price",
            )
        return _tv_compute_from_price(
            symbol=symbol,
            price=px,
            current_mid=current_mid,
            cme_bp=cme_bp,
            source="cli:--tv-price",
        )

    # 2) lightweight public scanner (default; no MCP required)
    if fetch_enabled:
        close, status, reason = fetch_tv_scanner_close(symbol)
        if status == "ok" and close is not None:
            return _tv_compute_from_price(
                symbol=symbol,
                price=close,
                current_mid=current_mid,
                cme_bp=cme_bp,
                source="tradingview_scanner_http",
            )
        return _tv_empty(
            symbol=symbol,
            status=status,
            reason=reason,
            source="tradingview_scanner_http",
        )

    return _tv_empty(
        symbol=symbol,
        status="unavailable",
        reason="TV fetch disabled (--no-tv-fetch) and no --tv-price",
        source=None,
    )


def fed_alert_level(bp: Optional[float]) -> str:
    if bp is None:
        return "unavailable"
    t = THRESHOLDS["fed_1y_hike_bp"]
    if bp >= t["extreme"]:
        return "extreme"
    if bp >= t["elevated"]:
        return "elevated"
    if bp >= t["warn"]:
        return "warn"
    return "calm"


def build_payload(
    as_of: dt.date,
    *,
    tv_price: Optional[float] = None,
    tv_symbol: str = TV_FF_SYMBOL_DEFAULT,
    tv_fetch: bool = True,
) -> Dict[str, Any]:
    fed = compute_fed_1y_hike_bp(as_of=as_of)
    wti = fetch_wti(as_of)
    dgs = fetch_dgs10(as_of)
    bei = fetch_t10yie(as_of)
    move = fetch_move_tv()
    tv = resolve_tv_crosscheck(
        symbol=tv_symbol or TV_FF_SYMBOL_DEFAULT,
        current_mid=fed.get("current_mid"),
        cme_bp=fed.get("fed_1y_hike_bp"),
        tv_price_override=tv_price,
        fetch_enabled=tv_fetch,
    )

    # If CME path still degraded but we have mid + TV price, surface TV-derived
    # hike bp as the observational Fed reading (never fabricates mid/price).
    if (
        fed.get("quality") != "ok"
        and fed.get("fed_1y_hike_bp") is None
        and isinstance(tv.get("tv_1y_hike_bp"), (int, float))
        and fed.get("current_mid") is not None
    ):
        fed = dict(fed)
        fed.update(
            {
                "fed_1y_hike_bp": float(tv["tv_1y_hike_bp"]),
                "expected_rate_1y": tv.get("tv_implied_rate"),
                "quality": "ok",
                "degraded": False,
                "degraded_reason": None,
                "algo_version": "tv_zqu_implied_v1",
                "source": f"tv_crosscheck:{tv.get('source') or tv_symbol}",
                "fallback_note": fed.get("degraded_reason") or fed.get("fallback_note"),
                "meeting_anchor": {
                    "date": None,
                    "contract": tv.get("tv_symbol") or tv_symbol,
                    "horizon_days": HORIZON_DAYS,
                    "note": "TV ZQU implied; CME settlement path unavailable",
                },
            }
        )

    legs_ok = [
        fed.get("quality") == "ok",
        wti.get("quality") == "ok",
        dgs.get("quality") == "ok",
        bei.get("quality") == "ok",
    ]
    # MOVE always abstains — does not degrade whole artifact alone
    if all(legs_ok):
        quality = "ok"
        degraded = False
        degraded_reason = None
    elif any(legs_ok):
        quality = "partial"
        degraded = True
        reasons = []
        if fed.get("quality") != "ok":
            reasons.append(f"fed:{fed.get('degraded_reason') or fed.get('quality')}")
        if wti.get("quality") != "ok":
            reasons.append(f"wti:{wti.get('degraded_reason') or 'bad'}")
        if dgs.get("quality") != "ok":
            reasons.append(f"dgs10:{dgs.get('degraded_reason') or 'bad'}")
        if bei.get("quality") != "ok":
            reasons.append(f"t10yie:{bei.get('degraded_reason') or 'bad'}")
        degraded_reason = "; ".join(reasons)
    else:
        quality = "degraded"
        degraded = True
        degraded_reason = "all priced legs failed"

    thr = {
        "fed_1y_hike_bp_warn": THRESHOLDS["fed_1y_hike_bp"]["warn"],
        "fed_1y_hike_bp_elevated": THRESHOLDS["fed_1y_hike_bp"]["elevated"],
        "fed_1y_hike_bp_extreme": THRESHOLDS["fed_1y_hike_bp"]["extreme"],
        "wti_narrative_line": THRESHOLDS["wti_narrative_line"],
        "copy": (
            f"Fed 一年累计加息定价门槛（观察）："
            f"{THRESHOLDS['fed_1y_hike_bp']['warn']}/"
            f"{THRESHOLDS['fed_1y_hike_bp']['elevated']}/"
            f"{THRESHOLDS['fed_1y_hike_bp']['extreme']} bp；"
            f"WTI 作者叙事线绝对价 {THRESHOLDS['wti_narrative_line']}。"
            "本旁证不进合成 min / 不改 denom_policy。"
        ),
    }

    alerts: List[str] = []
    bp = fed.get("fed_1y_hike_bp")
    lvl = fed_alert_level(bp if isinstance(bp, (int, float)) else None)
    if lvl == "extreme":
        alerts.append(f"Fed 1y 加息定价 {bp}bp ≥ 极端线 {thr['fed_1y_hike_bp_extreme']}")
    elif lvl == "elevated":
        alerts.append(f"Fed 1y 加息定价 {bp}bp ≥ 抬升线 {thr['fed_1y_hike_bp_elevated']}")
    elif lvl == "warn":
        alerts.append(f"Fed 1y 加息定价 {bp}bp ≥ 观察线 {thr['fed_1y_hike_bp_warn']}")
    if wti.get("above_narrative_line"):
        alerts.append(
            f"WTI {wti.get('wti_last')} ≥ 叙事线 {thr['wti_narrative_line']}"
        )
    if fed.get("degraded"):
        alerts.append(f"Fed 段 degraded：{fed.get('degraded_reason')}")

    return {
        "date": as_of.isoformat(),
        "as_of": as_of.isoformat(),
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "article_macro_watch_daily.py",
        "schema": "macro-os.article-macro-watch.v1",
        "role": "article_narrative_crosscheck",
        "bound_to_combined_min": False,
        "note": (
            "思路观测旁证：只观察+报警；不进 compute_combined_budget；"
            "不放松/不覆盖分母 tight→0.35；不做第六腿。"
        ),
        "quality": quality,
        "degraded": degraded,
        "degraded_reason": degraded_reason,
        "fed_1y_hike_bp": fed.get("fed_1y_hike_bp"),
        "fed": fed,
        "wti_last": wti.get("wti_last"),
        "wti": wti,
        "dgs10": dgs.get("dgs10"),
        "tnx": dgs.get("tnx"),
        "rates_10y": dgs,
        "t10yie": bei.get("t10yie"),
        "t10yie_detail": bei,
        "t10yie_vs_mean": {
            "last": bei.get("t10yie"),
            "mean": bei.get("mean"),
            "deviation": bei.get("deviation"),
            "deviation_bp": bei.get("deviation_bp"),
            "lookback_calendar_days": bei.get("mean_lookback_calendar_days"),
        },
        "move": move.get("move"),
        "move_detail": move,
        "abstain_reason": move.get("abstain_reason"),
        # TV 对照列（默认字段；status=unavailable/rate_limited 时数值为 null）
        "tv_symbol": tv.get("tv_symbol"),
        "tv_price": tv.get("tv_price"),
        "tv_implied_rate": tv.get("tv_implied_rate"),
        "tv_1y_hike_bp": tv.get("tv_1y_hike_bp"),
        "tv_delta_bp": tv.get("delta_bp"),
        "tv_crosscheck": tv,
        "thresholds": thr,
        "fed_alert_level": lvl,
        "alerts": alerts,
    }


def build_report(p: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append(f"# 文章思路观测旁证 · {p['as_of']}")
    L.append("")
    L.append(
        "> **观察旁证 · 非交易指令**。不进合成预算 min，不改 denom_policy，不做第六腿。"
    )
    L.append("")
    L.append(f"- **质量**: {p.get('quality')} ｜ degraded={p.get('degraded')}")
    if p.get("degraded_reason"):
        L.append(f"- **降级原因**: {p['degraded_reason']}")
    L.append("")
    L.append("## Fed 一年累计加息定价")
    L.append("")
    fed = p.get("fed") or {}
    if fed.get("fed_1y_hike_bp") is not None:
        L.append(
            f"- **fed_1y_hike_bp**: **{fed['fed_1y_hike_bp']} bp** "
            f"（alert={p.get('fed_alert_level')}）"
        )
        anc = fed.get("meeting_anchor") or {}
        L.append(
            f"- **meeting 锚点**: {anc.get('date')} / {anc.get('contract')} "
            f"（horizon={anc.get('horizon_days')}d）"
        )
        L.append(
            f"- **当前目标**: {fed.get('current_target')} ｜ EFFR={fed.get('effr')} ｜ "
            f"结算日={fed.get('trade_date')}"
        )
        L.append(
            f"- **E[rate@1y]**: {fed.get('expected_rate_1y')} ｜ mid={fed.get('current_mid')}"
        )
    else:
        L.append(f"- **fed_1y_hike_bp**: null（degraded）— {fed.get('degraded_reason')}")
    L.append(
        f"- **算法**: `{fed.get('algo_version')}` ｜ source=`{fed.get('source')}`"
    )
    L.append(f"- **偏差声明**: {fed.get('disclaimer')}")
    L.append("")
    L.append("## TV 对照（CBOT Fed Funds 期货）")
    L.append("")
    tv = p.get("tv_crosscheck") or {}
    st = tv.get("status") or "unavailable"
    L.append(f"- **tv_symbol**: `{tv.get('tv_symbol') or p.get('tv_symbol')}`")
    if st == "ok" and tv.get("tv_price") is not None:
        delta = tv.get("delta_bp")
        delta_s = f"{delta:+.1f}" if isinstance(delta, (int, float)) else "n/a"
        L.append(
            f"- **tv_price**: {tv.get('tv_price')} ｜ "
            f"implied={tv.get('tv_implied_rate')}% ｜ "
            f"**tv_1y_hike_bp={tv.get('tv_1y_hike_bp')} bp** ｜ "
            f"Δ vs CME自算 = **{delta_s} bp**"
        )
        L.append(
            f"- **对照口径**: current_mid={tv.get('current_mid_used')}；"
            f"CME自算={tv.get('cme_1y_hike_bp')} bp；"
            f"source=`{tv.get('source')}`；status={st}"
        )
    else:
        L.append(
            f"- **tv_price / tv_1y_hike_bp**: null（status={st}"
            + (f"；{tv.get('degraded_reason')}" if tv.get("degraded_reason") else "")
            + "）"
        )
        L.append(
            f"- **说明**: TV 对照失败不阻断 CME 旁证；"
            f"可由 `--tv-price` / `ARTICLE_TV_PRICE` 注入，或依赖 scanner HTTP。"
        )
    L.append(
        f"- **公式**: implied=100−price；"
        f"tv_1y_hike_bp=(implied−current_mid)×100；delta=tv−CME。"
        "本列不进合成 min。"
    )
    L.append("")
    L.append("## WTI / 10Y / BEI / MOVE")
    L.append("")
    wti = p.get("wti") or {}
    L.append(
        f"- **WTI**: {p.get('wti_last')}（as_of={wti.get('as_of')}，"
        f"叙事线 {THRESHOLDS['wti_narrative_line']}，"
        f"{'触及/越过' if wti.get('above_narrative_line') else '未过线'}）"
    )
    L.append(
        f"- **DGS10/10Y**: {p.get('dgs10')}%（source={ (p.get('rates_10y') or {}).get('source') }，"
        f"as_of={(p.get('rates_10y') or {}).get('as_of')}）"
    )
    vs = p.get("t10yie_vs_mean") or {}
    L.append(
        f"- **T10YIE**: {p.get('t10yie')}% ｜ 近年均值 {vs.get('mean')} "
        f"（lookback {vs.get('lookback_calendar_days')}d）｜ "
        f"偏离 {vs.get('deviation')} pp（{vs.get('deviation_bp')} bp）"
    )
    md = p.get("move_detail") or {}
    if p.get("move") is not None:
        L.append(
            f"- **MOVE**: {p.get('move')}（source={md.get('source')}；"
            f"旁证展示，不进合成 min）"
        )
    else:
        L.append(f"- **MOVE**: null — {md.get('abstain_reason')}")
    L.append("")
    L.append("## 门槛文案")
    L.append("")
    L.append(f"- {(p.get('thresholds') or {}).get('copy')}")
    L.append("")
    if p.get("alerts"):
        L.append("## 报警")
        L.append("")
        for a in p["alerts"]:
            L.append(f"- ⚠ {a}")
        L.append("")
    L.append(
        "> 完整逻辑见 `scripts/article_macro_watch_daily.py`。"
        "P2：永不进 combined min / 不改 tight→0.35 / 不做第六腿。"
    )
    return "\n".join(L) + "\n"


def write_outputs(report_dir: Path, p: Dict[str, Any]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"article_macro_watch_{p['as_of']}"
    (report_dir / f"{stem}.json").write_text(
        json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (report_dir / f"{stem}.md").write_text(build_report(p), encoding="utf-8")
    logger.info("written %s.{md,json}", stem)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Article macro watch daily (narrative cross-check, not in combined min)"
    )
    ap.add_argument(
        "--date",
        default=None,
        help="as_of YYYY-MM-DD (US session clamp); default=today UTC date",
    )
    ap.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    ap.add_argument(
        "--tv-symbol",
        default=os.environ.get("ARTICLE_TV_SYMBOL", TV_FF_SYMBOL_DEFAULT),
        help=f"TV Fed Funds future symbol (default {TV_FF_SYMBOL_DEFAULT})",
    )
    ap.add_argument(
        "--tv-price",
        default=None,
        help="Optional TV close/mid override (also ARTICLE_TV_PRICE). "
        "When set, skips scanner HTTP. Typical handoff from TradingView MCP.",
    )
    ap.add_argument(
        "--no-tv-fetch",
        action="store_true",
        help="Skip TradingView scanner HTTP (still accepts --tv-price / ARTICLE_TV_PRICE)",
    )
    args = ap.parse_args(argv)

    if args.date:
        try:
            as_of = dt.date.fromisoformat(str(args.date)[:10])
        except ValueError:
            logger.error("invalid --date %r", args.date)
            return 2
    else:
        as_of = dt.datetime.now(dt.timezone.utc).date()

    tv_price = None
    raw_tv = args.tv_price if args.tv_price is not None else os.environ.get("ARTICLE_TV_PRICE")
    if raw_tv is not None and str(raw_tv).strip() != "":
        try:
            tv_price = float(str(raw_tv).strip())
        except ValueError:
            logger.error("invalid --tv-price / ARTICLE_TV_PRICE %r", raw_tv)
            return 2

    try:
        payload = build_payload(
            as_of,
            tv_price=tv_price,
            tv_symbol=str(args.tv_symbol or TV_FF_SYMBOL_DEFAULT),
            tv_fetch=not bool(args.no_tv_fetch),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("build_payload failed: %s", exc)
        return 1

    write_outputs(Path(args.report_dir), payload)
    try:
        print(build_report(payload))
    except Exception:
        pass
    print(
        f"\n[written] article_macro_watch_{payload['as_of']}.json / .md",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
