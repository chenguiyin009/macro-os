#!/usr/bin/env python3
"""Denominator (funding-price) state machine — DAILY headless Python port of
《资金价格斜率状态机 v1.6》(by 本杰明乌萨奇).

================================================================================
WHY THIS EXISTS
================================================================================
* macro-os/core/denominator_state.py is already a FAITHFUL logic port of the
  Pine v1.6 state machine. This script is the *daily data + output layer* that
  makes it run headlessly (no TradingView, no browser) every morning.
* It reuses the EXACT data pipeline validated in
  scripts/backtest_denominator_state.py (build_frame + DXY_MONTHLY rebasing),
  so the port stays faithful. FRED core series are refreshed into
  data/_*_daily.csv (RAW FRED units; build_frame applies the *100 HY scaling).
  yfinance confirmation is cached in data/_eq_*.csv.

OUTPUT: output/denominator_state_<date>.{md,json}
  where <date> = latest fully-populated FRED date (FRED lags ~3-5 bus days,
  so it is usually a few days before "today" — honest and expected).
================================================================================
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

# Defensive: cap BLAS/OpenMP threads BEFORE numpy import. On memory-pressured
# sandboxes the default thread fan-out can trigger spurious "out of memory"
# tokenizing crashes during pd.read_csv; single-thread is harmless at this
# data scale and removes the failure mode for both headless and automated runs.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_MAX_THREADS", "1")

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
DATA_DIR = REPO_ROOT / "data"
DEFAULT_REPORT_DIR = REPO_ROOT.parent / "output"

# FRED series id -> (cache filename, frame column)
FRED_SERIES = {
    "DFII10": ("_tips_daily.csv", "tips_yield"),
    "DGS2":   ("_nom2y_daily.csv", "nominal_2y"),
    "DGS10":  ("_nom10y_daily.csv", "nominal_10y"),
    "DGS30":  ("_nom30y_daily.csv", "nominal_30y"),
    "T10YIE": ("_bei_daily.csv", "bei_10y"),
    "BAMLH0A0HYM2": ("_hy_daily.csv", "hy_credit_spread"),
    "VIXCLS": ("_vix_daily.csv", "vix"),
    "DTWEXBGS": ("_dxy_daily.csv", "dxy"),
}
LOOKBACK_DAYS = 1300  # ~3.5y calendar -> >756 trading days for the pct window

# yfinance ticker per confirmation column (Pine symbol in comment)
EQUITY_MAP = {
    "qqq": "QQQ",        # NASDAQ:QQQ
    "sox": "SOXX",       # NASDAQ:SOXX
    "iwm": "IWM",        # AMEX:IWM
    "kre": "KRE",        # AMEX:KRE
    "gold": "GLD",       # AMEX:GLD
    "spx": "^GSPC",      # SP:SPX (price proxy)
    "vix3m": "VIX3M",    # CBOE:VIX3M (may be unavailable)
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("denominator-daily")


# --------------------------------------------------------------------------- #
# Proxy + FRED refresh
# --------------------------------------------------------------------------- #
def _ensure_proxy() -> Optional[urllib.request.OpenerDirector]:
    # SECTOR_PROXY 开关（供无本地代理环境，如 grokbot 云机）：
    #   off/none/0/false/no/""  → 完全不设代理（依赖直连/环境自带代理）
    #   http://host:port        → 使用指定代理
    #   未设置                   → 默认 127.0.0.1:7890（作者本机）
    #   已设 HTTPS_PROXY/HTTP_PROXY → 以环境既有代理为准
    sp = os.environ.get("SECTOR_PROXY")
    OFF = ("off", "none", "0", "false", "no", "")
    if sp is not None:
        if sp.strip().lower() in OFF:
            return None
        proxy = sp.strip()
    else:
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not proxy:
        try:
            os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7890")
            os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7890")
            proxy = os.environ["HTTPS_PROXY"]
        except Exception:
            return None
    try:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    except Exception:
        return None


def _last_date_in_cache(path: Path) -> Optional[dt.date]:
    """Max observation_date in an existing FRED/yfinance cache CSV, or None."""
    try:
        old = pd.read_csv(path)
        if "observation_date" in old.columns and len(old) > 0:
            return pd.to_datetime(old["observation_date"]).max().date()
    except Exception:  # noqa: BLE001
        pass
    return None


def refresh_fred(force: bool):
    """Refresh data/_*_daily.csv with RAW FRED units.

    为什么这么写（踩过的坑）：
      * FRED 经 127.0.0.1:7890 代理必超时 -> 直连。用空 ProxyHandler({}) 构造
        opener，显式忽略 HTTP(S)_PROXY 环境变量，强制直连 fred.stlouisfed.org。
      * pct 窗口要 ~756 交易日历史，不能只拉小窗口重算 -> 增量追加：缓存已
        存在时只拉 [cache_last-10d, 今天] 缺失段并去重合并，绝不整体覆盖 3.5y 文件。
      * 日频尾部追加：缓存末日未达「今天」即拉 [末日-10d, 今天] 增量段；只有缓存已到今天才跳过。不再用 7 天闸门，否则日频跑永远追不上 FRED 最新日。
    任何失败都回退到现有缓存，保证流水线仍能动（顶多少旧一点）。"""
    # 直连 opener：空 ProxyHandler 忽略所有代理环境变量。
    direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    from adapters.fred import fetch_fred_series
    today = datetime.now(timezone.utc).date()
    for sid, (fname, _col) in FRED_SERIES.items():
        path = DATA_DIR / fname
        last_date = _last_date_in_cache(path)
        if last_date is not None and not force:
            if last_date >= today:
                logger.info("fred cache already at today (%s): %s", last_date, fname)
                continue
            # 日频尾部追加：缓存末日未到今天即拉 [末日-10d, 今天] 增量段。
            # 不再用 7 天闸门跳过——否则日频跑永远追不上 FRED 最新日。
            start = last_date - timedelta(days=10)  # 10 天重叠缓冲，防缺口
        else:
            start = today - timedelta(days=LOOKBACK_DAYS)
        lookback = max(30, (today - start).days)
        try:
            pts = fetch_fred_series(sid, lookback_days=lookback, opener=direct_opener)
            if not pts:
                logger.warning("fred empty: %s (keep cache if any)", sid)
                continue
            new = pd.Series({p.date: p.value for p in pts})
            new.index = pd.to_datetime(new.index)
            new = new.sort_index().rename(sid)  # 值列名用 series id，匹配 _load() 与现有缓存
            if path.exists() and last_date is not None:
                old = pd.read_csv(path)
                old["observation_date"] = pd.to_datetime(old["observation_date"])
                old_val = old.set_index("observation_date").iloc[:, 0]  # 第2列即值（列名兼容 sid / value）
                old_val.name = sid
                merged = pd.concat([old_val, new])
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                merged = merged.rename(sid).reset_index()
                merged.columns = ["observation_date", sid]
            else:
                merged = new.rename(sid).reset_index()
                merged.columns = ["observation_date", sid]
            merged.to_csv(path, index=False)
            logger.info("fred refreshed: %s (%d pts, last %s)", fname, len(merged), merged["observation_date"].iloc[-1].date())
        except Exception as exc:  # noqa: BLE001
            logger.warning("fred fetch failed %s: %s (keep existing cache)", sid, exc)


# --------------------------------------------------------------------------- #
# Frame builder (copied + parameterized from scripts/backtest_denominator_state.py
# so the daily run is forward-looking and not tied to the backtest's frozen
# PREPEND/EQUITY end dates). Semantic identical to the validated backtest.
# --------------------------------------------------------------------------- #
def refresh_akshare_rates():
    """可选尾部补位：akshare `bond_zh_us_rate` 通常比 FRED 早 1 天发布美债
    2/10/30Y 收益率（FRED 还停在 07-29 时它已到 07-30）。仅当 akshare 末日
    严格新于 FRED 缓存时才合并写入同名 _nom*_daily.csv（sid 列），FRED 后续
    刷新会按日期去重 keep_last 覆盖，故 akshare 只是抢先 1 天的过渡源。
    akshare 没有 TIPS/VIX/信用利差/美元指数等价源，这些仍必须靠 FRED。"""
    try:
        import akshare as ak
    except Exception:
        logger.info("akshare not installed -> skip nominal tail-fill")
        return
    try:
        df = ak.bond_zh_us_rate()
    except Exception as exc:  # noqa: BLE001
        logger.warning("akshare bond_zh_us_rate failed: %s (skip)", exc)
        return
    if df is None or len(df) == 0 or "日期" not in df.columns:
        return
    df["日期"] = pd.to_datetime(df["日期"])
    col_map = {
        "美国国债收益率2年": ("_nom2y_daily.csv", "DGS2"),
        "美国国债收益率10年": ("_nom10y_daily.csv", "DGS10"),
        "美国国债收益率30年": ("_nom30y_daily.csv", "DGS30"),
    }
    for akcol, (fname, sid) in col_map.items():
        if akcol not in df.columns:
            continue
        sub = df[["日期", akcol]].rename(columns={"日期": "observation_date", akcol: sid})
        sub[sid] = pd.to_numeric(sub[sid], errors="coerce")
        sub = sub.dropna(subset=[sid]).sort_values("observation_date")
        if sub.empty:
            continue
        path = DATA_DIR / fname
        ak_last = sub["observation_date"].max().date()
        cache_last = _last_date_in_cache(path)
        if cache_last is not None and ak_last <= cache_last:
            continue  # FRED 缓存已更新到同日或更晚，无需 akshare 补
        if path.exists():
            old = pd.read_csv(path)
            old["observation_date"] = pd.to_datetime(old["observation_date"])
            old_val = old.set_index("observation_date").iloc[:, 0].rename(sid)
            merged = pd.concat([old_val.reset_index(), sub])
            merged = merged.drop_duplicates(subset=["observation_date"], keep="last").sort_values("observation_date")
            merged.columns = ["observation_date", sid]
        else:
            merged = sub.rename(columns={sid: sid})
            merged.columns = ["observation_date", sid]
        merged.to_csv(path, index=False)
        logger.info("akshare tail-fill: %s -> last %s", fname, merged["observation_date"].iloc[-1].date())


def refresh_vix_yahoo():
    """尾部补位 VIX：yfinance ^VIX 是 CBOE VIX 收盘指数的同口径源（实测
    07-28/07-29 与 FRED VIXCLS 逐一吻合），通常比 FRED VIXCLS 早 1 天发布。
    仅接受已收盘确认的交易日（date < today，排除今天的盘前/实时快照），
    仅当 yfinance 末日 > FRED 缓存末日时合并写入 _vix_daily.csv（值列 VIXCLS）。
    FRED 后续刷新会按日期去重 keep_last 覆盖，故 yahoo 只是抢先 1 天的过渡源。"""
    try:
        import yfinance as yf
    except Exception:
        logger.info("yfinance not installed -> skip VIX tail-fill")
        return
    try:
        t = yf.Ticker("^VIX")
        hist = t.history(period="14d", auto_adjust=False)
        if hist is None or "Close" not in hist.columns:
            logger.warning("yfinance ^VIX: no data"); return
        s = hist["Close"].dropna()
        if getattr(s.index, "tz", None) is not None:
            s.index = s.index.tz_localize(None)
        s.index = s.index.normalize()
        today = datetime.now(timezone.utc).date()
        s = s[s.index.date < today]  # 排除今天的实时/盘前快照，只用收盘确认日
        if s.empty:
            return
        sub = s.rename("VIXCLS").reset_index()
        sub.columns = ["observation_date", "VIXCLS"]
        path = DATA_DIR / "_vix_daily.csv"
        yf_last = sub["observation_date"].max().date()
        cache_last = _last_date_in_cache(path)
        if cache_last is not None and yf_last <= cache_last:
            logger.info("vix yahoo cache fresh (last %s): skip", cache_last); return
        if path.exists():
            old = pd.read_csv(path)
            old["observation_date"] = pd.to_datetime(old["observation_date"])
            old_val = old.set_index("observation_date").iloc[:, 0].rename("VIXCLS")
            merged = pd.concat([old_val.reset_index(), sub])
            merged = merged.drop_duplicates(subset=["observation_date"], keep="last").sort_values("observation_date")
            merged.columns = ["observation_date", "VIXCLS"]
        else:
            merged = sub.copy()
            merged.columns = ["observation_date", "VIXCLS"]
        merged.to_csv(path, index=False)
        logger.info("vix yahoo tail-fill: _vix_daily.csv -> last %s", merged["observation_date"].iloc[-1].date())
    except Exception as exc:  # noqa: BLE001
        logger.warning("vix yahoo fetch failed: %s (skip)", exc)


def backfill_tips_via_spread():
    """TIPS 尾部补位（恒等式推导，零误差）：在 FRED 体系内
    DFII10(10Y TIPS 收益率) ≡ DGS10 − T10YIE（实测重叠 643 点，相关系数 1.0，
    残差均值/标准差=0）。当 DFII10 缓存末日落后 DGS10/T10YIE 末日时，对每个
    共同交易日 t 用 DFII10(t)=DGS10(t)−T10YIE(t) 填补到 min(二者末日)。
    无需任何外部抓取——DGS10 已由 akshare 抢先到 07-30，T10YIE 由 FRED 到 07-30，
    故 TIPS 07-30 可直接算出来，与 FRED 真值完全一致。"""
    tips_path = DATA_DIR / "_tips_daily.csv"
    n10_path = DATA_DIR / "_nom10y_daily.csv"
    bei_path = DATA_DIR / "_bei_daily.csv"
    tips_last = _last_date_in_cache(tips_path)
    n10_last = _last_date_in_cache(n10_path)
    bei_last = _last_date_in_cache(bei_path)
    if n10_last is None or bei_last is None:
        return
    frontier = min(n10_last, bei_last)  # 两个源都有的最新日
    if tips_last is not None and frontier <= tips_last:
        return  # 已对齐，无需推导
    try:
        n10 = pd.read_csv(n10_path); n10["observation_date"] = pd.to_datetime(n10["observation_date"])
        n10 = n10.set_index("observation_date")["DGS10"]
        bei = pd.read_csv(bei_path); bei["observation_date"] = pd.to_datetime(bei["observation_date"])
        bei = bei.set_index("observation_date")["T10YIE"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("tips derivation load failed: %s", exc); return
    join = pd.concat([n10, bei], axis=1).dropna()
    if tips_last is None:
        new_rows = join
    else:
        new_rows = join[join.index.date > tips_last]
    if new_rows.empty:
        return
    derived = (new_rows["DGS10"] - new_rows["T10YIE"]).rename("DFII10").reset_index()
    derived.columns = ["observation_date", "DFII10"]
    if tips_path.exists():
        old = pd.read_csv(tips_path); old["observation_date"] = pd.to_datetime(old["observation_date"])
        old_val = old.set_index("observation_date").iloc[:, 0].rename("DFII10")
        merged = pd.concat([old_val.reset_index(), derived])
        merged = merged.drop_duplicates(subset=["observation_date"], keep="last").sort_values("observation_date")
        merged.columns = ["observation_date", "DFII10"]
    else:
        merged = derived.copy(); merged.columns = ["observation_date", "DFII10"]
    merged.to_csv(tips_path, index=False)
    logger.info("tips derived via spread: _tips_daily.csv -> last %s", merged["observation_date"].iloc[-1].date())


def _load(csv_name, value_col):
    df = pd.read_csv(DATA_DIR / csv_name)
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    df = df.set_index("observation_date").sort_index()
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    return df[value_col]


def _fetch_yf(ticker, col, start, end):
    path = DATA_DIR / f"_eq_{col}.csv"
    if path.exists():
        try:
            s = pd.read_csv(path)
            s["observation_date"] = pd.to_datetime(s["observation_date"])
            if getattr(s["observation_date"].dt, "tz", None) is not None:
                s["observation_date"] = s["observation_date"].dt.tz_localize(None)
            last = s["observation_date"].max().date()
            age = (datetime.now(timezone.utc).date() - last).days
            if age <= 7:
                logger.info("yf cache fresh (last %s, %dd): %s", last, age, ticker)
                return s.set_index("observation_date")["close"]
        except Exception:  # noqa: BLE001
            pass
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(start=start, end=end, auto_adjust=True)
        if hist is None or "Close" not in hist.columns or hist["Close"].dropna().empty:
            logger.warning("yfinance %s: no data", ticker)
            return None
        close = hist["Close"]
        if getattr(close.index, "tz", None) is not None:
            close = close.tz_localize(None)
        saved = close.rename("close").reset_index()
        saved.columns = ["observation_date", "close"]
        saved.to_csv(path, index=False)
        return close
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance fetch %s failed: %s", ticker, exc)
        return None


def build_frame(prepend_start: str, equity_end: str):
    from scripts.backtest_regime import DXY_MONTHLY

    vix = _load("_vix_daily.csv", "VIXCLS")
    tips = _load("_tips_daily.csv", "DFII10")
    n10 = _load("_nom10y_daily.csv", "DGS10")
    n02 = _load("_nom2y_daily.csv", "DGS2")
    n30 = _load("_nom30y_daily.csv", "DGS30")
    bei = _load("_bei_daily.csv", "T10YIE")
    hy_pct = _load("_hy_daily.csv", "BAMLH0A0HYM2")
    dxy_raw = _load("_dxy_daily.csv", "DTWEXBGS")

    idx = vix.index
    tips_a = tips.reindex(idx).ffill().bfill()
    n02_a = n02.reindex(idx).ffill().bfill()
    n10_a = n10.reindex(idx).ffill().bfill()
    n30_a = n30.reindex(idx).ffill().bfill()
    bei_a = bei.reindex(idx).ffill().bfill()
    hy_a = (hy_pct.reindex(idx).ffill().bfill()) * 100.0  # percent -> bp (matches Pine/backtest)

    # Rebase DTWEXBGS to ICE monthly anchors (same as daily pipeline)
    dxy_raw_d = dxy_raw.reindex(idx).interpolate(method="time").ffill().bfill()
    ratio_series = pd.Series(dtype=float)
    for m, ice_val in DXY_MONTHLY.items():
        y, mo = map(int, m.split("-"))
        mend = pd.Timestamp(y, mo, 1) + pd.offsets.MonthEnd(0)
        nearest = dxy_raw.index[dxy_raw.index.get_indexer([mend], method="nearest")[0]]
        dtwex_val = dxy_raw.asof(nearest)
        if dtwex_val is not None and not pd.isna(dtwex_val):
            ratio_series.loc[nearest] = ice_val / float(dtwex_val)
    ratio_daily = ratio_series.reindex(idx).interpolate(method="time").bfill()
    dxy_daily = dxy_raw_d * ratio_daily

    frame = pd.DataFrame({
        "vix": vix.reindex(idx).ffill().bfill(),
        "tips_yield": tips_a.reindex(idx),
        "nominal_2y": n02_a.reindex(idx),
        "nominal_10y": n10_a.reindex(idx),
        "nominal_30y": n30_a.reindex(idx),
        "bei_10y": bei_a.reindex(idx),
        "hy_credit_spread": hy_a.reindex(idx),
        "dxy": dxy_daily.reindex(idx),
    })

    present_eq = []
    for col, ticker in EQUITY_MAP.items():
        s = _fetch_yf(ticker, col, prepend_start, equity_end)
        if s is not None:
            frame[col] = s.reindex(idx).ffill().bfill()
            present_eq.append(col)
    logger.info("confirmation columns present: %s", present_eq)

    drop_cols = ["vix", "tips_yield", "nominal_10y", "nominal_30y",
                 "bei_10y", "hy_credit_spread", "dxy"]
    if "qqq" in frame.columns:
        drop_cols.append("qqq")
    frame = frame.dropna(subset=drop_cols)
    return frame, present_eq


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def compute_and_write(report_dir: Path, force: bool) -> dict:
    _ensure_proxy()
    refresh_fred(force)
    refresh_akshare_rates()
    refresh_vix_yahoo()
    backfill_tips_via_spread()

    today = dt.date.today()
    prepend_start = (today - timedelta(days=int(LOOKBACK_DAYS * 0.9))).isoformat()
    equity_end = (today + timedelta(days=1)).isoformat()

    frame, present_eq = build_frame(prepend_start, equity_end)
    from core.denominator_state import compute_denominator_states, DenominatorParams
    ds = compute_denominator_states(frame, DenominatorParams())
    last = ds.iloc[-1]
    date_str = ds.index[-1].strftime("%Y-%m-%d")

    payload = {
        "date": date_str,
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "symbol": "MACRO",
        "script": "资金价格斜率状态机 v1.6 (Python headless port)",
        "state": last["state"],
        "raw_state": last["raw_state"],
        "confidence_label": last["confidence_label"],
        "confidence_score": last["confidence_score"],
        "confidence_detail": last["confidence_detail"],
        "quadrant": last["quadrant"],
        "nominal_source": last["nominal_source"],
        "dxy_tag": last["dxy_tag"],
        "tips_note": last["tips_note"],
        "dont_do": last["dont_do"],
        "z5": {
            "tips": round(float(last["tips_z5"]), 3),
            "dxy": round(float(last["dxy_z5"]), 3),
            "cs": round(float(last["cs_z5"]), 3),
            "n30": round(float(last["n30_z5"]), 3),
        },
        "confirmation_columns": present_eq,
        "note": "headless python port; no intraday trigger layer; FRED may lag ~3-5 bus days",
    }

    md = _build_md(payload)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"denominator_state_{date_str}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (report_dir / f"denominator_state_{date_str}.md").write_text(md, encoding="utf-8")
    logger.info("written denominator_state_%s.{md,json}", date_str)
    return payload


def _build_md(p: dict) -> str:
    L = []
    L.append("# 市场分母状态机 · 每日读数（Python Headless 复刻）\n")
    L.append(f"- **日期**：{p['date']}（数据截至 FRED 最新可得日）")
    L.append(f"- **生成时间**：{p['as_of']}（北京时间盘前）")
    L.append(f"- **脚本**：{p['script']}")
    L.append("")
    L.append("## 核心状态\n")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| 主状态 | **{p['state']}** ［置信：{p['confidence_label']}］ |")
    L.append(f"| 四象限 | {p['quadrant']} |")
    L.append(f"| 今天不做什么 | **{p['dont_do']}** |")
    L.append(f"| 名义来源 | {p['nominal_source']} |")
    L.append(f"| DXY 性质 | {p['dxy_tag'] or '—'} |")
    L.append(f"| TIPS 判读 | {p['tips_note']} |")
    L.append("")
    L.append("## 变量 z5（原样）\n")
    L.append("| 变量 | Z5 |")
    L.append("|---|---|")
    L.append(f"| TIPS | {p['z5']['tips']} |")
    L.append(f"| DXY | {p['z5']['dxy']} |")
    L.append(f"| 信用利差 | {p['z5']['cs']} |")
    L.append(f"| 30Y | {p['z5']['n30']} |")
    L.append("")
    L.append("> ⚠️ 边界声明：本读数来自观察工具，非交易信号/投资建议。仅回答“今天资金压力来自哪一层”。")
    L.append("> 完整逻辑见 `core/denominator_state.py`；Intraday 触发层为 TV 专有，本本地版不含。")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Daily headless denominator state machine (v1.6 port)")
    ap.add_argument("--date", default=dt.date.today().isoformat(),
                    help="(kept for CLI symmetry; output uses latest FRED date)")
    ap.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    ap.add_argument("--force", action="store_true",
                    help="force re-fetch FRED (ignore cache); yfinance still cached")
    args = ap.parse_args()
    compute_and_write(Path(args.report_dir), args.force)


if __name__ == "__main__":
    main()
