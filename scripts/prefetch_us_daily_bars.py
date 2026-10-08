#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Post-close incremental prefetch for the morning 09:15 and evening 21:45 reports.

Writes into the caches those jobs already read. It does not score, and it does
not touch the composite min or Obsidian.

Run from the daily-macro-stable checkout, after the US cash close
(about 04:00-05:00 Asia/Shanghai during daylight time):

  cd /workspace/macro-os
  SECTOR_PROXY=off .venv/bin/python scripts/prefetch_us_daily_bars.py --dry-run
  SECTOR_PROXY=off .venv/bin/python scripts/prefetch_us_daily_bars.py --symbols QQQ,SPY,XLK
  SECTOR_PROXY=off .venv/bin/python scripts/prefetch_us_daily_bars.py

Order per symbol: yfinance (recent window only when history exists; 429 waits
30s/60s/120s) -> TradingView mcp-tv-get-ohlcv via TV_MCP_CMD -> Stooq CSV.
A TradingView 429 is recorded and skipped. Existing dates before the completed
session are not replaced.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import pandas as pd  # noqa: E402

import bar_sources as bs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("prefetch-us-daily")

TECH_CODES = [
    "QQQ", "SMH", "IGV", "SKYY", "CIBR", "BOTZ", "AIQ",
    "PLTR", "APP", "SNOW", "DDOG", "MDB", "COHR", "LITE", "AAOI",
    "ANET", "CIEN", "CSCO", "MU", "WDC", "STX", "SNDK",
    "VRT", "ETN", "PWR", "CEG", "GEV", "MSFT", "AMZN", "GOOGL", "META",
    "NVDA", "AVGO", "AMD", "MRVL",
]
SECTOR_PAIRS = [
    ("spy", "SPY"), ("xlk", "XLK"), ("xlf", "XLF"), ("xlv", "XLV"), ("xly", "XLY"),
    ("xlp", "XLP"), ("xle", "XLE"), ("xli", "XLI"), ("xlb", "XLB"), ("xlu", "XLU"),
    ("xlre", "XLRE"), ("xlc", "XLC"), ("iwm", "IWM"), ("rsp", "RSP"), ("qqq", "QQQ"),
    ("tlt", "TLT"), ("hyg", "HYG"), ("gld", "GLD"), ("kre", "KRE"),
]
THEME_TICKERS = [
    "^IRX", "^TNX", "^TYX", "TIP", "DX-Y.NYB", "EURUSD=X", "JPY=X",
    "GLD", "CL=F", "HG=F", "SPY", "QQQ", "RSP", "BTC-USD",
]
NQ_TICKERS = ["QQQ", "SPY", "IWM", "SOXX", "TLT", "TIP", "UUP", "FXY", "BTC-USD", "GLD", "HYG"]
SHOCK_TICKERS = ["ZN=F", "UB=F", "DX-Y.NYB", "ES=F", "NQ=F", "BTC-USD", "GC=F", "HYG", "LQD", "KRE", "^VIX"]
DENOM_PAIRS = [
    ("qqq", "QQQ"), ("sox", "SOXX"), ("iwm", "IWM"), ("kre", "KRE"),
    ("gold", "GLD"), ("spx", "^GSPC"), ("vix3m", "VIX3M"),
]
STRESS_TICKERS = ["SOXX", "QQQ"]


def _discover() -> Dict[str, Path]:
    parent = REPO.parent
    tech = parent / "tech-rotation"
    loose_sector = parent / "sector-rotation" / "sector_rotation_daily.py"
    roots = {"macro": REPO}
    if (tech / "data" / "tech_price_cache").is_dir():
        roots["tech_cache"] = tech / "data" / "tech_price_cache"
    elif (REPO / "data" / "tech_price_cache").is_dir():
        roots["tech_cache"] = REPO / "data" / "tech_price_cache"
    # Live 标普轮动 script lives one directory above its cache
    # (/workspace/sector-rotation/sector_rotation_daily.py -> /workspace/data/...).
    if loose_sector.exists():
        live_cache = loose_sector.resolve().parents[1] / "data" / "sector_price_cache"
        if live_cache.is_dir() and any(live_cache.glob("*.csv")):
            roots["sector_live_cache"] = live_cache
    macro_sector = REPO / "data" / "sector_price_cache"
    if macro_sector.is_dir() and any(macro_sector.glob("*.csv")):
        roots["sector_macro_cache"] = macro_sector
    return roots


def _read_cv(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    df = pd.read_csv(path)
    date_col = "Date" if "Date" in df.columns else df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col).sort_index()
    if "Close" in df.columns:
        close_col = "Close"
    elif "close" in df.columns:
        close_col = "close"
    else:
        close_col = next((c for c in df.columns if c != date_col), None)
    if close_col is None:
        return None
    out = pd.DataFrame({"Close": pd.to_numeric(df[close_col], errors="coerce")})
    if "Volume" in df.columns:
        out["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
    else:
        out["Volume"] = float("nan")
    return out.dropna(subset=["Close"])


def _write_cv(path: Path, frame: pd.DataFrame, *, with_volume: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if with_volume:
        out = frame[["Close", "Volume"]].copy()
    else:
        out = frame[["Close"]].copy()
    out = out[out["Close"].notna()].sort_index()
    out.index.name = "Date"
    tmp = path.with_suffix(path.suffix + ".tmp")
    out.to_csv(tmp, encoding="utf-8")
    tmp.replace(path)


def _write_eq(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = frame["Close"].dropna().rename("close").reset_index()
    saved.columns = ["observation_date", "close"]
    tmp = path.with_suffix(path.suffix + ".tmp")
    saved.to_csv(tmp, index=False, encoding="utf-8")
    tmp.replace(path)


def _write_stress_undated(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write("Close\n")
        for v in frame["Close"].dropna().tolist():
            fh.write(f"{float(v):.4f}\n")
    tmp.replace(path)


def _write_stress_dated(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = frame[["Close"]].dropna().copy()
    out.index = pd.to_datetime(out.index)
    out.index.name = "date"
    out.index = [ts.date().isoformat() for ts in out.index]
    tmp = path.with_suffix(path.suffix + ".tmp")
    out.to_csv(tmp, encoding="utf-8")
    tmp.replace(path)


def _selected(ticker: str, wanted: Optional[set]) -> bool:
    return wanted is None or ticker in wanted


def _run_one(
    ticker: str,
    path: Path,
    *,
    reader: Callable[[Path], Optional[pd.DataFrame]],
    writer: Callable[[Path, pd.DataFrame], None],
    min_bars: int,
    full_period: str,
    dry_run: bool,
    label: str,
) -> str:
    existing = None
    if path.exists():
        try:
            existing = reader(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s read %s failed: %s", label, path, exc)
    last = None if existing is None or existing.empty else pd.Timestamp(existing.index[-1]).date()
    completed = bs.completed_us_cash_session()
    if dry_run:
        covers = bs.cache_covers(existing, completed, min_bars)
        mode = "skip" if covers else ("incremental" if existing is not None and len(existing) >= min_bars else "full")
        logger.info("DRY %s %s last=%s completed=%s plan=%s", label, ticker, last, completed, mode)
        return mode
    mtime = path.stat().st_mtime if path.exists() else None
    merged, notes, source = bs.fetch_with_fallback(
        ticker,
        existing,
        full_period=full_period,
        min_bars=min_bars,
        cached_mtime=mtime,
    )
    for note in notes:
        logger.warning("%s %s: %s", label, ticker, note)
    new_last = None if merged is None or merged.empty else pd.Timestamp(merged.index[-1]).date()
    if source in ("yfinance", "tradingview", "stooq") and merged is not None:
        writer(path, merged)
    logger.info("%s %s source=%s last %s -> %s", label, ticker, source, last, new_last)
    return source


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Prefetch completed US daily bars into local caches")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, do not hit the network")
    parser.add_argument("--symbols", default="", help="comma yfinance tickers to limit the run (verification)")
    parser.add_argument(
        "--groups",
        default="tech,sector,theme,nq,shock,denom,stress",
        help="comma subset of tech,sector,theme,nq,shock,denom,stress",
    )
    args = parser.parse_args(argv)
    wanted = {s.strip() for s in args.symbols.split(",") if s.strip()} or None
    groups = {s.strip() for s in args.groups.split(",") if s.strip()}
    roots = _discover()
    logger.info("completed US session %s", bs.completed_us_cash_session())
    logger.info("roots %s", {k: str(v) for k, v in roots.items()})
    bs.reset_yf_circuit()
    results: List[str] = []

    def keep(ticker: str) -> bool:
        return _selected(ticker, wanted)

    if "tech" in groups and "tech_cache" in roots:
        cache = roots["tech_cache"]
        for code in TECH_CODES:
            if not keep(code):
                continue
            results.append(_run_one(
                code, cache / f"{code}.csv", reader=_read_cv,
                writer=lambda p, f: _write_cv(p, f, with_volume=True),
                min_bars=60, full_period="2y", dry_run=args.dry_run, label="tech",
            ))
    elif "tech" in groups:
        logger.warning("tech cache dir not found; skip tech group")

    if "sector" in groups:
        for label, root_key in (("sector-live", "sector_live_cache"), ("sector-macro", "sector_macro_cache")):
            if root_key not in roots:
                logger.warning("%s cache not found", label)
                continue
            cache = roots[root_key]
            for key, code in SECTOR_PAIRS:
                if not keep(code):
                    continue
                results.append(_run_one(
                    code, cache / f"{key}.csv", reader=_read_cv,
                    writer=lambda p, f: _write_cv(p, f, with_volume=True),
                    min_bars=60, full_period="2y", dry_run=args.dry_run, label=label,
                ))

    macro = roots["macro"]
    if "theme" in groups:
        cache = macro / "data" / "_theme_state_closes"
        for code in THEME_TICKERS:
            if not keep(code):
                continue
            results.append(_run_one(
                code, cache / f"{code}.csv", reader=_read_cv,
                writer=lambda p, f: _write_cv(p, f, with_volume=False),
                min_bars=30, full_period="2y", dry_run=args.dry_run, label="theme",
            ))
    if "nq" in groups:
        cache = macro / "data" / "cache" / "nq_driver"
        for code in NQ_TICKERS:
            if not keep(code):
                continue
            safe = code.replace("=", "_").replace("^", "")
            results.append(_run_one(
                code, cache / f"{safe}.csv", reader=_read_cv,
                writer=lambda p, f: _write_cv(p, f, with_volume=False),
                min_bars=40, full_period="2y", dry_run=args.dry_run, label="nq",
            ))
    if "shock" in groups:
        cache = macro / "data" / "cache" / "shock_absorption"
        for code in SHOCK_TICKERS:
            if not keep(code):
                continue
            safe = code.replace("=", "_").replace("^", "")
            results.append(_run_one(
                code, cache / f"{safe}.csv", reader=_read_cv,
                writer=lambda p, f: _write_cv(p, f, with_volume=False),
                min_bars=60, full_period="3y", dry_run=args.dry_run, label="shock",
            ))
    if "denom" in groups:
        for col, code in DENOM_PAIRS:
            if not keep(code):
                continue
            results.append(_run_one(
                code, macro / "data" / f"_eq_{col}.csv", reader=_read_cv,
                writer=_write_eq,
                min_bars=60, full_period="5y", dry_run=args.dry_run, label="denom",
            ))
        if wanted is None or "^VIX" in wanted:
            results.append(_run_one(
                "^VIX", macro / "data" / "_vix_daily.csv", reader=_read_cv,
                writer=_write_eq_as_vix,
                min_bars=30, full_period="1mo", dry_run=args.dry_run, label="vix",
            ))
    if "stress" in groups:
        for code, undated, dated in (
            ("SOXX", macro / "data" / "_tech_drawdown_sox.csv", macro / "data" / "_tech_drawdown_sox_dated.csv"),
            ("QQQ", macro / "data" / "_tech_drawdown_qqq.csv", macro / "data" / "_tech_drawdown_qqq_dated.csv"),
        ):
            if not keep(code):
                continue
            # Dated file is the incremental base. Undated close list is what the dampener reads.
            src = _run_one(
                code, dated, reader=_read_cv,
                writer=_write_stress_dated,
                min_bars=30, full_period="3mo", dry_run=args.dry_run, label="stress",
            )
            results.append(src)
            if not args.dry_run and src in ("yfinance", "tradingview", "stooq"):
                frame = _read_cv(dated)
                if frame is not None:
                    _write_stress_undated(undated, frame)

    logger.info("done %d steps; yfinance circuit=%s", len(results), bs.yf_circuit_reason() or "clear")
    return 0


def _write_eq_as_vix(path: Path, frame: pd.DataFrame) -> None:
    """VIX FRED cache uses observation_date,VIXCLS. Do not rename the value column."""
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = frame["Close"].dropna().rename("VIXCLS").reset_index()
    saved.columns = ["observation_date", "VIXCLS"]
    tmp = path.with_suffix(path.suffix + ".tmp")
    saved.to_csv(tmp, index=False, encoding="utf-8")
    tmp.replace(path)


if __name__ == "__main__":
    # SECTOR_PROXY is honored by the report scripts. This prefetch does not set a proxy.
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
