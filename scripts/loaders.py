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

from rules import select_themes_by_state  # noqa: E402

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
    "AIQ": ["AIQ.csv", "aiq.csv"],
    "159915.SZ": ["159915.SZ.csv", "159915.sz.csv"],
    "510300.SS": ["510300.SS.csv", "510300.ss.csv"],
    "399006.SZ": ["399006.SZ.csv", "399006.sz.csv"],
    "000300.SS": ["000300.SS.csv", "000300.ss.csv"],
    "^HSI": ["^HSI.csv", "HSI.csv", "hsi.csv"],
    "3032.HK": ["3032.HK.csv", "3032.hk.csv"],
    "2800.HK": ["2800.HK.csv", "2800.hk.csv"],
}

# Mirrors tech-rotation THEME_ORDER / BASKETS. Do not import that module (production).
# Equal-weight basket members — used only to measure forward returns.
BASKETS: Dict[str, List[str]] = {
    "app": ["PLTR", "APP", "SNOW", "DDOG", "MDB"],
    "opt": ["COHR", "LITE", "AAOI"],
    "net": ["ANET", "CIEN", "CSCO"],
    "mem": ["MU", "WDC", "STX", "SNDK"],
    "infra": ["VRT", "ETN", "PWR", "CEG", "GEV"],
    "hyp": ["MSFT", "AMZN", "GOOGL", "META"],
    "hw": ["NVDA", "AVGO", "AMD", "MRVL"],
}
THEME_META: List[Tuple[int, str, str, str]] = [
    (1, "semi", "半导体", "SMH"),
    (2, "soft", "软件", "IGV"),
    (3, "cloud", "云计算", "SKYY"),
    (4, "cyber", "网络安全", "CIBR"),
    (5, "robot", "机器人", "BOTZ"),
    (6, "ai", "AI综合", "AIQ"),
    (7, "app", "AI应用软件", "篮子"),
    (8, "opt", "光通信零部件", "篮子"),
    (9, "net", "AI网络", "篮子"),
    (10, "mem", "储存", "篮子"),
    (11, "infra", "数据中心电力散热", "篮子"),
    (12, "hyp", "云巨头/AI买方", "篮子"),
    (13, "hw", "AI硬件卖方", "篮子"),
]
THEME_KEY_BY_NAME = {name: key for _id, key, name, _px in THEME_META}
THEME_KEY_BY_ID = {i: key for i, key, _name, _px in THEME_META}


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
    strongest: List[Dict[str, Any]]  # name, proxy, score, state — by state, may include 篮子
    weakest: List[Dict[str, Any]]
    selected_theme: Optional[str]
    a_share_mapping: Dict[str, Any]
    path: str
    selected_rel5_pct: Optional[float] = None  # 百分数单位（与 tech rel5_pct 一致）
    themes: List[Dict[str, Any]] = field(default_factory=list)
    selected_score: Optional[float] = None
    avg_score: Optional[float] = None
    selected_state: Optional[str] = None


@dataclass
class SpxPred:
    as_of: str
    pine_top3: List[Dict[str, Any]]  # name, ticker, score
    c_weights: List[Dict[str, Any]]  # ticker, name, pct
    path: str


@dataclass
class BarStatus:
    source: str
    notes: List[str] = field(default_factory=list)
    last_date: Optional[date] = None
    persist_error: Optional[str] = None
    skipped_cache: bool = False


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


def _theme_key(t: Dict[str, Any]) -> Optional[str]:
    if t.get("key"):
        return str(t["key"])
    if t.get("id") in THEME_KEY_BY_ID:
        return THEME_KEY_BY_ID[t["id"]]
    name = t.get("name")
    if name in THEME_KEY_BY_NAME:
        return THEME_KEY_BY_NAME[name]
    return None


def _normalize_theme(t: Dict[str, Any]) -> Dict[str, Any]:
    key = _theme_key(t)
    return {
        "name": t.get("name"),
        "proxy": t.get("proxy"),
        "score": t.get("score"),
        "state": t.get("state"),
        "id": t.get("id"),
        "key": key,
        "rel5_pct": t.get("rel5_pct"),
        "members": list(BASKETS.get(key or "", [])),
    }


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
    raw_themes = list(d.get("themes") or [])
    themes = [_normalize_theme(t) for t in raw_themes]
    strongest, weakest = select_themes_by_state(themes)
    selected_name = d.get("selected_theme")
    sel_rel5 = None
    sel_score = d.get("selected_theme_score")
    sel_state = None
    if selected_name:
        for t in themes:
            if t.get("name") == selected_name or t.get("id") == selected_name or t.get("key") == selected_name:
                v = t.get("rel5_pct")
                if v is not None:
                    try:
                        sel_rel5 = float(v)
                    except (TypeError, ValueError):
                        sel_rel5 = None
                if t.get("score") is not None and sel_score is None:
                    try:
                        sel_score = float(t.get("score"))
                    except (TypeError, ValueError):
                        pass
                sel_state = t.get("state")
                break
    avg = None
    ov = d.get("overall") or {}
    if ov.get("avg_score") is not None:
        try:
            avg = float(ov.get("avg_score"))
        except (TypeError, ValueError):
            avg = None
    if sel_score is not None:
        try:
            sel_score = float(sel_score)
        except (TypeError, ValueError):
            sel_score = None
    return TechPred(
        as_of=str(d.get("as_of") or as_of),
        strongest=strongest,
        weakest=weakest,
        selected_theme=selected_name,
        a_share_mapping=dict(d.get("a_share_mapping") or {}),
        path=str(path),
        selected_rel5_pct=sel_rel5,
        themes=themes,
        selected_score=sel_score,
        avg_score=avg,
        selected_state=sel_state,
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
    names = TICKER_FILES.get(ticker.upper() if not ticker.startswith("^") else ticker, None)
    if names is None:
        names = TICKER_FILES.get(ticker, [f"{ticker}.csv", f"{ticker.upper()}.csv", f"{ticker.lower()}.csv"])
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


def series_last_date(series: Optional[pd.Series]) -> Optional[date]:
    if series is None or series.empty:
        return None
    return pd.Timestamp(series.index.max()).date()


def next_weekday(as_of: date) -> date:
    """First weekday strictly after as_of (Sat/Sun skipped; holidays confirmed later)."""
    d = as_of + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def next_session_need_through(
    series: Optional[pd.Series],
    as_of: date,
    *,
    expected: Optional[date] = None,
) -> date:
    """need_through date for the first session strictly after as_of.

    If the cache already has a later session, return that date so ensure_bars
    can skip the network. Otherwise return the expected next weekday.
    """
    expect = expected or next_weekday(as_of)
    if series is None or series.empty:
        return expect
    dates = [pd.Timestamp(ts).date() for ts in series.index]
    after = sorted(d for d in dates if d > as_of)
    if after:
        return after[0]
    return expect


def classify_missing_next_session(
    *,
    as_of: date,
    expected: date,
    last_date: Optional[date],
    source: Optional[str],
    notes: Optional[Sequence[str]] = None,
    persist_error: Optional[str] = None,
    today: Optional[date] = None,
) -> str:
    """Concrete reason when the next A/HK session after as_of is missing."""
    bits = []
    if persist_error:
        bits.append(f"写入缓存失败：{persist_error}")
    note_txt = "；".join(str(n) for n in (notes or []) if n)
    src = source or "unknown"
    if last_date is None:
        base = f"缓存为空（源={src}"
        if note_txt:
            base += f"；{note_txt}"
        base += "）"
        bits.append(base)
        return "抓取失败：" + "；".join(bits)
    if last_date <= as_of:
        failed = src in {"none", "cache-stale"} or (
            note_txt
            and any(
                k in note_txt.lower()
                for k in ("fail", "失败", "rate", "empty", "unmapped")
            )
        )
        vendor_gap = bool(note_txt) and any(
            k in note_txt for k in ("yfinance跳过", "yfinance未包含", "跳过")
        )
        if today is not None and expected >= today and not vendor_gap:
            bits.append(
                f"次日会话尚未收盘或未入库（期望 {expected.isoformat()}，缓存至 {last_date.isoformat()}，源={src}）"
            )
        elif failed or vendor_gap:
            bits.append(
                f"抓取失败（期望 {expected.isoformat()}，缓存仍止于 {last_date.isoformat()}，源={src}"
                + (f"；{note_txt}" if note_txt else "")
                + "）"
            )
        else:
            bits.append(
                f"缓存未覆盖预期会话 {expected.isoformat()}（缓存至 {last_date.isoformat()}，源={src}"
                + (f"；{note_txt}" if note_txt else "；无新bar")
                + "，疑似假期或空数据）"
            )
        return "；".join(bits)
    return f"次日收益仍缺失（缓存至 {last_date.isoformat()}，源={src}）"



def annotate_source_gap(
    ticker: str,
    need_through: date,
    status: BarStatus,
) -> None:
    """If need_through is still missing, record the raw vendor dates (no new vendor)."""
    import bar_sources as bs  # type: ignore

    frame, reason = bs.yf_download_bars(ticker, period="10d", attempts=1)
    if frame is None or getattr(frame, "empty", True):
        if reason:
            status.notes.append(reason)
        return
    dates = [pd.Timestamp(ts).date() for ts in frame.index]
    status.notes.append(
        "yfinance原始日期=" + ",".join(d.isoformat() for d in dates[-8:])
    )
    if need_through not in dates:
        later = [d for d in dates if d > need_through]
        if later:
            status.notes.append(
                f"yfinance跳过{need_through.isoformat()}（其后出现 {later[0].isoformat()}，疑似未收盘bar，未写入缓存）"
            )
        else:
            status.notes.append(f"yfinance未包含{need_through.isoformat()}")


def ensure_bars(
    tickers: Sequence[str],
    *,
    force_refresh: bool = False,
    need_through: Optional[date] = None,
    status_out: Optional[Dict[str, BarStatus]] = None,
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
        last = series_last_date(cached)
        if (
            not force_refresh
            and need_through is not None
            and cached is not None
            and len(cached) >= 40
        ):
            idx_dates = {pd.Timestamp(ts).date() for ts in cached.index}
            if need_through in idx_dates:
                sources[ticker] = "cache"
                if status_out is not None:
                    status_out[ticker] = BarStatus(
                        source="cache", last_date=last, skipped_cache=True
                    )
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
        persist_error = None
        if merged is not None and not merged.empty:
            # persist — do not swallow; record the reason if write fails
            target_root = cpath.parent if cpath else write_roots[0]
            target_root.mkdir(parents=True, exist_ok=True)
            out_name = cpath.name if cpath else f"{ticker}.csv"
            out = target_root / out_name
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
            except Exception as exc:  # noqa: BLE001
                persist_error = f"{type(exc).__name__}: {exc}"
        if status_out is not None:
            last_after = series_last_date(load_close_series(ticker)) if persist_error is None else last
            if last_after is None and merged is not None and not merged.empty:
                try:
                    last_after = pd.Timestamp(merged.index.max()).date()
                except Exception:
                    last_after = last
            status_out[ticker] = BarStatus(
                source=source,
                notes=list(notes or []),
                last_date=last_after,
                persist_error=persist_error,
            )
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


def close_to_close(
    series: pd.Series,
    start: date,
    end: date,
) -> Optional[float]:
    """Close-to-close return from start date to end date (both must be in series)."""
    if series is None or series.empty:
        return None
    mapping = {pd.Timestamp(ts).date(): float(v) for ts, v in zip(series.index, series.values)}
    if start not in mapping or end not in mapping:
        return None
    base = mapping[start]
    tgt = mapping[end]
    if base <= 0 or tgt <= 0:
        return None
    return tgt / base - 1.0


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


def basket_members_for(theme: Dict[str, Any]) -> List[str]:
    members = theme.get("members") or []
    if members:
        return [str(m) for m in members]
    key = theme.get("key") or _theme_key(theme)
    if key and key in BASKETS:
        return list(BASKETS[key])
    return []


def theme_is_basket(theme: Dict[str, Any]) -> bool:
    proxy = theme.get("proxy")
    if proxy == "篮子":
        return True
    key = theme.get("key") or _theme_key(theme)
    return bool(key and key in BASKETS)


def equal_weight_return_to(
    series_list: Sequence[pd.Series],
    as_of: date,
    target: date,
) -> Tuple[Optional[float], Optional[str]]:
    """Equal-weight close-to-close return; all members must have both dates.

    Returns (ret, skip_reason). skip_reason is set when the basket cannot be measured.
    """
    if not series_list:
        return None, "篮子无成员序列"
    rets: List[float] = []
    for s in series_list:
        r = close_to_close(s, as_of, target)
        if r is None:
            return None, f"篮子成员缺 {as_of.isoformat()}→{target.isoformat()} 收盘，跳过该主题"
        rets.append(r)
    return sum(rets) / len(rets), None


def theme_forward_return(
    theme: Dict[str, Any],
    as_of: date,
    horizon: int = 1,
    *,
    target: Optional[date] = None,
) -> Tuple[Optional[float], Optional[date], Optional[str]]:
    """Forward close-to-close return for an ETF theme or a 篮子.

    If a basket cannot be measured (missing member bars), skip that theme —
    do not substitute an unrelated ETF.
    """
    tgt = target
    if tgt is None:
        # Prefer QQQ calendar so basket members align on the same US session.
        qqq = load_close_series("QQQ")
        if qqq is not None:
            _r, tgt = session_return(qqq, as_of, horizon)
        if tgt is None:
            spy = load_close_series("SPY")
            if spy is not None:
                _r, tgt = session_return(spy, as_of, horizon)
    if theme_is_basket(theme):
        members = basket_members_for(theme)
        if not members:
            return None, tgt, "篮子无成员定义，跳过该主题"
        if tgt is None:
            return None, None, "缺目标交易日，篮子无法测"
        series_list: List[pd.Series] = []
        missing: List[str] = []
        for m in members:
            s = load_close_series(m)
            if s is None:
                missing.append(m)
            else:
                series_list.append(s)
        if missing:
            return None, tgt, f"篮子缺成员缓存 {','.join(missing)}，跳过该主题"
        ret, reason = equal_weight_return_to(series_list, as_of, tgt)
        return ret, tgt, reason
    proxy = theme.get("proxy")
    if not proxy or proxy == "篮子":
        return None, tgt, "无可用代理，跳过该主题"
    s = load_close_series(str(proxy))
    if s is None:
        return None, tgt, f"缺 {proxy} 缓存，跳过该主题"
    if tgt is not None:
        r = close_to_close(s, as_of, tgt)
        return r, tgt, (None if r is not None else f"{proxy} 缺 {as_of}→{tgt} 收盘")
    r, d = session_return(s, as_of, horizon)
    return r, d, (None if r is not None else f"{proxy} 缺 {horizon}D 收益")



def fill_a_share_from_sina(
    ticker: str,
    need_through: date,
    *,
    sina_fetch=None,
) -> Tuple[List[date], Optional[str]]:
    """2026-11-14：A 股 ETF 缓存缺到 need_through 时，用 Sina 日 K 只追加缺失日期（不改旧行）。

    复用 tech-rotation/scripts/a_share_bars.py（与对A结构标签同一套补位逻辑）。
    返回 (追加的日期, 错误)。非 A 股 ETF 直接跳过。
    """
    import a_share_bars as ab  # type: ignore  # TECH_ROT/scripts 已在 sys.path

    if ticker not in ab.SINA_SYMBOL:
        return [], "not a-share etf"
    cached = load_close_series(ticker)
    last = series_last_date(cached)
    if last is not None and last >= need_through:
        return [], None
    fresh, err = (sina_fetch or ab.sina_daily)(ticker)
    if fresh is None:
        return [], err
    merged, added = ab.append_missing(cached, fresh, need_through)
    if not added:
        return [], None
    cpath = _find_cache_csv(ticker) or (TECH_ROT / "data" / "tech_price_cache" / f"{ticker}.csv")
    cpath.parent.mkdir(parents=True, exist_ok=True)
    try:
        old = pd.read_csv(cpath) if cpath.exists() else pd.DataFrame(columns=["Date", "Close", "Volume"])
        rows = pd.DataFrame({"Date": [d.isoformat() for d in added],
                             "Close": [float(merged[pd.Timestamp(d)]) for d in added],
                             "Volume": [float("nan")] * len(added)})
        cols = list(old.columns) if len(old.columns) else ["Date", "Close", "Volume"]
        out = pd.concat([old, rows.reindex(columns=cols)], ignore_index=True)
        tmp = cpath.with_suffix(".csv.tmp")
        out.to_csv(tmp, index=False)
        tmp.replace(cpath)
    except Exception as exc:  # noqa: BLE001
        return [], f"persist {type(exc).__name__}: {exc}"
    return added, None
