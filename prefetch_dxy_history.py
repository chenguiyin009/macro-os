"""One-off: prefetch full-history DTWEXBGS into data/_theme_backtest/DTWEXBGS.csv.

Uses adaptive windows + TRUE direct connection (all proxy env cleared) to
dodge the proxy/TLS break on large FRED CSV responses.
"""
from __future__ import annotations

import datetime as dt
import io
import os
import ssl
import urllib.request

import pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "data", "_theme_backtest")
os.makedirs(CACHE, exist_ok=True)
OUT = os.path.join(CACHE, "DTWEXBGS.csv")


def fetch_window(start: str, end: str, timeout: float = 45) -> pd.Series:
    url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv"
           f"?id=DTWEXBGS&cosd={start}&coed={end}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    saved = {k: os.environ.pop(k, None) for k in
             ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy",
              "http_proxy", "all_proxy", "NO_PROXY", "no_proxy")}
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            text = r.read().decode("utf-8")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    idx = pd.to_datetime(df[df.columns[0]], errors="coerce")
    vals = pd.to_numeric(df[df.columns[1]], errors="coerce")
    s = vals.where(idx.notna())
    s.index = idx.dropna()
    return s.dropna().sort_index()


def main():
    end = dt.date.today()
    start = dt.date(2006, 1, 1)
    parts = []
    cur = start
    step_pref = [365 * 2, 182, 30]
    n = 0
    while cur <= end:
        n += 1
        if n % 20 == 0:
            print(f"  ... {cur}")
        nxt = cur + dt.timedelta(days=step_pref[0])
        win_end = min(nxt - dt.timedelta(days=1), end)
        s = None
        for step in step_pref:
            we = min(cur + dt.timedelta(days=step) - dt.timedelta(days=1), end)
            try:
                s = fetch_window(cur.isoformat(), we.isoformat())
                if len(s):
                    break
            except Exception as e:
                s = None
        if s is not None and len(s):
            parts.append(s)
        cur = nxt
    full = pd.concat(parts).sort_index()
    full = full[~full.index.duplicated(keep="last")]
    full.to_frame("Close").to_csv(OUT)
    print(f"WROTE {OUT}: {len(full)} rows {full.index[0].date()}..{full.index[-1].date()}")


if __name__ == "__main__":
    main()
