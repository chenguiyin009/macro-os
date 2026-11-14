# -*- coding: utf-8 -*-
"""
quote_200ma.py — QQQ / SPY 现价 vs 200 日均线 校验器
====================================================
为 daily_allocator.py 的 L1 轮动信号门控提供**精确的 200MA 状态**。
输出 JSON 到 stdout（供 run_daily_instruction.py 解析后传给 daily_allocator 的
--qqq-above-200ma / --spy-above-200ma）。

用法：
  python quote_200ma.py
  python quote_200ma.py --tickers QQQ SPY --sma 200
  python quote_200ma.py --json   # 同默认，仅打印 JSON

输出示例：
  {"qqq_above_200ma": true,  "spy_above_200ma": true,
   "qqq_close": 718.45, "qqq_sma200": 691.02,
   "spy_close": 642.10, "spy_sma200": 601.33,
   "as_of": "2026-08-11", "ok": true}

失败时 ok=false，above 字段为 null，调用方应回退到启发式（默认 ON）。
"""
import sys
import json
import argparse
from datetime import date

try:
    import yfinance as yf
    import pandas as pd
except Exception as exc:  # 极兜底：环境缺包时直接报错 JSON
    print(json.dumps({"ok": False, "error": f"import failed: {exc}",
                      "qqq_above_200ma": None, "spy_above_200ma": None}))
    sys.exit(2)


def above_200ma(ticker, sma=200, lookback_days=400):
    """返回 (above_bool_or_None, close, sma200, as_of_str)。失败返回 (None, None, None, None)。"""
    try:
        df = yf.download(ticker, period=f"{lookback_days}d", interval="1d",
                         auto_adjust=True, progress=False, threads=False)
        if df is None or len(df) == 0:
            return None, None, None, None
        # yfinance 返回 MultiIndex columns，字段名所在层级不固定：
        # 本环境实测为 (Field, Ticker)，即 ('Close','QQQ')；做层级无关提取
        close = None
        if isinstance(df.columns, pd.MultiIndex):
            for lvl in range(df.columns.nlevels):
                if "Close" in df.columns.get_level_values(lvl):
                    close = df.xs("Close", axis=1, level=lvl)
                    break
        if close is None:
            close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = pd.Series(close).dropna()
        if len(close) < sma:
            return None, None, None, None
        last = float(close.iloc[-1])
        sma_val = float(close.rolling(sma).mean().iloc[-1])
        as_of = str(close.index[-1].date()) if hasattr(close.index[-1], "date") else str(date.today())
        return (last > sma_val), last, sma_val, as_of
    except Exception:
        return None, None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs=2, default=["QQQ", "SPY"],
                    help="两个标的：[科技ETF] [宽基ETF]，默认 QQQ SPY")
    ap.add_argument("--sma", type=int, default=200)
    ap.add_argument("--lookback", type=int, default=400,
                    help="下载日历天数（保证拿到 >=200 个交易日）")
    args = ap.parse_args()

    tech, broad = args.tickers[0], args.tickers[1]
    q_above, q_close, q_sma, q_asof = above_200ma(tech, args.sma, args.lookback)
    s_above, s_close, s_sma, s_asof = above_200ma(broad, args.sma, args.lookback)

    as_of = q_asof or s_asof or str(date.today())
    out = {
        "qqq_ticker": tech,
        "spy_ticker": broad,
        "qqq_above_200ma": q_above,
        "spy_above_200ma": s_above,
        "qqq_close": round(q_close, 4) if q_close is not None else None,
        "qqq_sma200": round(q_sma, 4) if q_sma is not None else None,
        "spy_close": round(s_close, 4) if s_close is not None else None,
        "spy_sma200": round(s_sma, 4) if s_sma is not None else None,
        "as_of": as_of,
        "ok": (q_above is not None and s_above is not None),
    }
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0 if out["ok"] else 2)


if __name__ == "__main__":
    main()
