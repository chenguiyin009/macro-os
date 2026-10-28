# -*- coding: utf-8 -*-
"""Load prediction artifacts + ETF bars (SECTOR_PROXY=off). Does not touch scoring."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MACRO_OS = Path(os.environ.get("CALIB_MACRO_OS", "/workspace/macro-os"))
TECH_ROT = Path(os.environ.get("CALIB_TECH_ROT", "/workspace/tech-rotation"))
SECTOR_ROT = Path(os.environ.get("CALIB_SECTOR_ROT", "/workspace/sector-rotation"))
TV_DESK = Path(os.environ.get("CALIB_TV_DESK", "/workspace/tradingview-desk"))

# Ensure bar_sources importable
for p in (str(ROOT / "scripts"), str(MACRO_OS / "scripts"), str(TECH_ROT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

CACHE_CANDIDATES = [
    TECH_ROT / "data" / "tech_price_cache",
    MACRO_OS / "data" / "sector_price_cache",
    SECTOR_ROT / "data" / "sector_price_cache",
    MACRO_OS / "data" / "tech_price_cache",
]

# ticker -> possible cache filenames (case variants)
TICKER_FILES = {
    "SPY": ["SPY.csv", "spy.csv"],
    "QQQ": ["QQQ.csv", "qqq.csv"],
    "SMH": ["SMH.csv", "smh.csv"],
    "IGV": ["IGV.csv", "igv.csv"],
    "SKYY": ["SKYY.csv", "skyy.csv"],
    "CIBR": ["CIBR.csv", "cibr.csv"],
    "XLE": ["XLE.csv", "xle.csv"],
    "XLP": ["XLP.csv", "xlp.csv"],
    "XLU": ["XLU.csv", "xlu.csv"],
    "XLK": ["XLK.csv", "xlk.csv"],
    "XLF": ["XLF.csv", "xlf.csv"],
    "XLV": ["XLV.csv", "xlv.csv"],
    "XLY": ["XLY.csv", "xly.csv"],
    "XLI": ["XLI.csv", "xli.csv"],
    "XLB": ["XLB.csv", "xlb.csv"],
    "XLRE": ["XLRE.csv", "xlre.csv"],
    "XLC": ["XLC.csv", "xlc.csv"],
    "IWM": ["IWM.csv", "iwm.csv"],
    "RSP": ["RSP.csv", "rsp.csv"],
    "SOXX": ["SOXX.csv", "soxx.csv"],
    "BOTZ": ["BOTZ.csv", "botz.csv"],
    "159915.SZ": ["159915.SZ.csv", "159915.sz.csv"],
    "510300.SS": ["510300.SS.csv", "510300.ss.csv"],
    "399006.SZ": ["399006.SZ.csv", "399006.sz.csv"],
    "000300.SS": ["000300.SS.csv", "000300.ss.csv"],
    "^HSI": ["^HSI.csv", "HSI.csv", "hsi.csv"],
    "3032.HK": ["3032.HK.csv", "3032.hk.csv"],
    "2800.HK": ["2800.HK.csv", "2800.hk.csv"],
}


@dataclass
class FourLegPred:
    as_of: str
    posture: Optional[str]
    bias: Optional[str]
    binding: List[str]
    combined_budget: Optional[float]
    stance: Optional[str]
    path: str
    denom_ceiling: Optional[float] = None


@dataclass
class TechPred:
    as_of: str
    strongest: List[Dict[str, Any]]  # name, proxy, score
    weakest: List[Dict[str, Any]]
    selected_theme: Optional[str]
    a_share_mapping: Dict[str, Any]
    path: str
    selected_rel5_pct: Optional[float] = None  # 百分数单位（与 tech rel5_pct 一致）


@dataclass
class SpxPred:
    as_of: str
    pine_top3: List[Dict[str, Any]]  # name, ticker, score
    c_weights: List[Dict[str, Any]]  # ticker, name, pct
    path: str


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_fourleg(as_of: str) -> Optional[FourLegPred]:
    path = MACRO_OS / "output" / f"daily_macro_{as_of}.json"
    d = _read_json(path)
    if not d:
        return None
    pa = d.get("plain_advice") or {}
    cr = d.get("combined_risk_budget") or {}
    syn = d.get("synthesis") or {}
    return FourLegPred(
        as_of=str(d.get("report_date") or as_of),
        posture=pa.get("posture"),
        bias=syn.get("bias"),
        binding=list(cr.get("binding") or []),
        combined_budget=cr.get("combined_budget"),
        stance=pa.get("stance"),
        path=str(path),
        denom_ceiling=cr.get("denominator_ceiling"),
    )


def load_tech(as_of: str) -> Optional[TechPred]:
    path = TECH_ROT / "output" / f"tech_rotation_{as_of}.json"
    d = _read_json(path)
    if not d:
        # tradingview-desk mirror
        alt = TV_DESK / "output" / f"tech_rotation_{as_of}.json"
        d = _read_json(alt)
        path = alt if d else path
    if not d:
        return None
    themes = list(d.get("themes") or [])
    # Prefer ETF proxies (skip 篮子)
    scored = sorted(
        [t for t in themes if t.get("proxy") and t.get("proxy") != "篮子"],
        key=lambda t: float(t.get("score") or 0),
        reverse=True,
    )
    strongest = [
        {"name": t.get("name"), "proxy": t.get("proxy"), "score": t.get("score"), "state": t.get("state")}
        for t in scored[:3]
    ]
    weakest = [
        {"name": t.get("name"), "proxy": t.get("proxy"), "score": t.get("score"), "state": t.get("state")}
        for t in scored[-2:]
    ]
    selected_name = d.get("selected_theme")
    sel_rel5 = None
    if selected_name:
        for t in themes:
            if t.get("name") == selected_name or t.get("id") == selected_name:
                v = t.get("rel5_pct")
                if v is not None:
                    try:
                        sel_rel5 = float(v)
                    except (TypeError, ValueError):
                        sel_rel5 = None
                break
    return TechPred(
        as_of=str(d.get("as_of") or as_of),
        strongest=strongest,
        weakest=weakest,
        selected_theme=selected_name,
        a_share_mapping=dict(d.get("a_share_mapping") or {}),
        path=str(path),
        selected_rel5_pct=sel_rel5,
    )


def load_spx(as_of: str) -> Optional[SpxPred]:
    """Prefer daily_abc (pine Top3 + C weights); fall back to sector_rotation Top3 by score."""
    # daily_abc date often = report run day; try as_of and next business day naming
    candidates = [
        TV_DESK / "rotation_results" / f"daily_abc_{as_of}.json",
    ]
    # also try files whose meta.sector points at this as_of
    abc_dir = TV_DESK / "rotation_results"
    if abc_dir.is_dir():
        for p in sorted(abc_dir.glob("daily_abc_*.json"), reverse=True):
            if "_blend" in p.name or "_mom12" in p.name:
                continue
            candidates.append(p)

    seen = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        d = _read_json(path)
        if not d:
            continue
        meta = d.get("meta") or {}
        sector_md = str(meta.get("sector_md") or "")
        # Accept if date matches or sector md mentions as_of
        date_field = str(d.get("date") or "")
        if as_of not in (date_field, sector_md) and f"sector_rotation_{as_of}" not in sector_md:
            # also accept if spy top3 pine scores align with sector file as_of via trinity snap
            snaps = ((d.get("trinity") or {}).get("snaps") or {})
            spy_asof = str((snaps.get("SPY") or {}).get("as_of") or "")
            if spy_asof != as_of and date_field != as_of:
                continue
        spy = d.get("spy") or {}
        top3 = list(spy.get("top3") or [])
        c_weights: List[Dict[str, Any]] = []
        for s in spy.get("strategies") or []:
            if s.get("id") == "C":
                c_weights = list(s.get("weights") or [])
                break
        return SpxPred(
            as_of=as_of,
            pine_top3=[
                {"name": t.get("name"), "ticker": t.get("ticker"), "score": t.get("score")}
                for t in top3
            ],
            c_weights=c_weights,
            path=str(path),
        )

    # Fallback: sector_rotation JSON top3 by score
    for base in (SECTOR_ROT, MACRO_OS, TV_DESK):
        path = base / "output" / f"sector_rotation_{as_of}.json"
        d = _read_json(path)
        if not d:
            continue
        themes = sorted(
            [t for t in (d.get("themes") or []) if t.get("proxy")],
            key=lambda t: float(t.get("score") or 0),
            reverse=True,
        )
        # Prefer 11 GICS sectors for pine-like Top3
        gics = {"XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"}
        ranked = [t for t in themes if t.get("proxy") in gics] or themes
        top3 = ranked[:3]
        return SpxPred(
            as_of=str(d.get("as_of") or as_of),
            pine_top3=[
                {"name": t.get("name"), "ticker": t.get("proxy"), "score": t.get("score")}
                for t in top3
            ],
            c_weights=[],
            path=str(path),
        )
    return None


def _find_cache_csv(ticker: str) -> Optional[Path]:
    names = TICKER_FILES.get(ticker.upper(), [f"{ticker.upper()}.csv", f"{ticker.lower()}.csv"])
    for root in CACHE_CANDIDATES:
        if not root.is_dir():
            continue
        for name in names:
            p = root / name
            if p.exists():
                return p
    return None


def load_close_series(ticker: str) -> Optional[pd.Series]:
    p = _find_cache_csv(ticker)
    if p is None:
        return None
    try:
        df = pd.read_csv(p, parse_dates=["Date"])
        if df.empty or "Close" not in df.columns:
            return None
        s = df.set_index("Date")["Close"].astype(float).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        return s
    except Exception:
        return None


def ensure_bars(
    tickers: Sequence[str],
    *,
    force_refresh: bool = False,
    need_through: Optional[date] = None,
) -> Dict[str, str]:
    """Refresh caches via bar_sources (SECTOR_PROXY=off). Returns ticker->source.

    If ``need_through`` is set and the local cache already has that date (and
    enough history), skip the network — calibration must not hammer yfinance.
    """
    os.environ.setdefault("SECTOR_PROXY", "off")
    import bar_sources as bs  # type: ignore

    bs.reset_yf_circuit()
    sources: Dict[str, str] = {}
    # Write back into the first existing cache root that already has the file,
    # else tech_price_cache / sector_price_cache.
    write_roots = [r for r in CACHE_CANDIDATES if r.is_dir()]
    if not write_roots:
        write_roots = [TECH_ROT / "data" / "tech_price_cache"]
        write_roots[0].mkdir(parents=True, exist_ok=True)

    for ticker in tickers:
        cached = load_close_series(ticker)
        if (
            not force_refresh
            and need_through is not None
            and cached is not None
            and len(cached) >= 40
        ):
            idx_dates = {pd.Timestamp(ts).date() for ts in cached.index}
            if need_through in idx_dates:
                sources[ticker] = "cache"
                continue
        existing = None
        if cached is not None:
            vol = pd.Series(float("nan"), index=cached.index)
            existing = pd.DataFrame({"Close": cached, "Volume": vol})
        cpath = _find_cache_csv(ticker)
        mtime = cpath.stat().st_mtime if cpath and cpath.exists() else None
        merged, notes, source = bs.fetch_with_fallback(
            ticker,
            existing,
            full_period="1y",
            min_bars=40,
            force_refresh=force_refresh,
            cached_mtime=mtime,
        )
        sources[ticker] = source
        if merged is None or merged.empty:
            continue
        # persist
        target_root = cpath.parent if cpath else write_roots[0]
        target_root.mkdir(parents=True, exist_ok=True)
        out = target_root / f"{ticker.upper()}.csv"
        try:
            frame = merged[["Close"]].copy()
            if "Volume" in merged.columns:
                frame["Volume"] = merged["Volume"]
            else:
                frame["Volume"] = float("nan")
            frame.index.name = "Date"
            tmp = out.with_suffix(".csv.tmp")
            frame.tail(800).to_csv(tmp, encoding="utf-8")
            tmp.replace(out)
        except Exception:
            pass
    return sources


def session_return(
    series: pd.Series,
    as_of: date,
    horizon: int = 1,
) -> Tuple[Optional[float], Optional[date]]:
    """Close-to-close return over `horizon` trading sessions after as_of.

    as_of must be in the series; target = as_of index + horizon.
    """
    if series is None or series.empty:
        return None, None
    # normalize to dates
    idx_dates = [pd.Timestamp(ts).date() for ts in series.index]
    mapping = {d: float(v) for d, v in zip(idx_dates, series.values)}
    dates = sorted(mapping.keys())
    if as_of not in mapping:
        return None, None
    i = dates.index(as_of)
    j = i + horizon
    if j >= len(dates):
        return None, None
    base = mapping[as_of]
    tgt_d = dates[j]
    tgt = mapping[tgt_d]
    if base <= 0 or tgt <= 0:
        return None, tgt_d
    return tgt / base - 1.0, tgt_d


def relative_return(
    ticker_series: pd.Series,
    bench_series: pd.Series,
    as_of: date,
    horizon: int = 1,
) -> Optional[float]:
    r_t, _ = session_return(ticker_series, as_of, horizon)
    r_b, _ = session_return(bench_series, as_of, horizon)
    if r_t is None or r_b is None:
        return None
    return r_t - r_b


def prior_report_as_of(completed: Optional[date] = None) -> date:
    import bar_sources as bs  # type: ignore

    done = completed or bs.completed_us_cash_session()
    return bs.prior_us_business_day(done)


def next_session_return(
    series: pd.Series,
    as_of: date,
) -> Tuple[Optional[float], Optional[date]]:
    """First trading session strictly after as_of: that day's close-to-close return.

    Used for A-share / HK calendars that may not align with US as_of.
    """
    if series is None or series.empty:
        return None, None
    idx_dates = [pd.Timestamp(ts).date() for ts in series.index]
    mapping = {d: float(v) for d, v in zip(idx_dates, series.values)}
    dates = sorted(mapping.keys())
    after = [d for d in dates if d > as_of]
    if not after:
        return None, None
    tgt_d = after[0]
    # previous close in series
    i = dates.index(tgt_d)
    if i == 0:
        return None, tgt_d
    prev_d = dates[i - 1]
    base = mapping[prev_d]
    tgt = mapping[tgt_d]
    if base <= 0 or tgt <= 0:
        return None, tgt_d
    return tgt / base - 1.0, tgt_d

