# -*- coding: utf-8 -*-
"""对A结构标签：Yahoo 最新日 NaN 时用 Sina 日K 补位（2026-11-14）。"""
import datetime as dt
import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("tech_rotation_daily_t", ROOT / "scripts" / "tech_rotation_daily.py")
trd = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_spec.loader.exec_module(trd)

DAYS = pd.to_datetime(["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"])
YF = {"159915.SZ": [2.509, 2.59, 2.524, np.nan], "510300.SS": [4.135, 4.15, 4.114, np.nan]}
SINA = {"sz159915": [2.509, 2.59, 2.524, 2.442], "sh510300": [4.135, 4.15, 4.114, 4.083]}


def _fake_yf(monkeypatch, data):
    def download(ticker, **kw):
        return pd.DataFrame({"Close": data[ticker]}, index=DAYS)
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=download))


def test_sina_fills_latest_day(monkeypatch):
    _fake_yf(monkeypatch, YF)
    monkeypatch.setattr(trd, "_sina_daily", lambda sym, datalen=40: (pd.Series(SINA[sym], index=DAYS), None))
    o = trd.fetch_a_share_structure_obs(dt.date(2026, 11, 13))
    assert o["as_of"] == "2026-11-13"
    assert abs(o["cyb_ret"] - (2.442 / 2.524 - 1)) < 1e-6
    assert abs(o["hs300_ret"] - (4.083 / 4.114 - 1)) < 1e-6
    assert o["label"] == "成长杀/红利托底"
    assert "sina" in o["source"]


def test_yfinance_preferred_when_same_day(monkeypatch):
    full = {k: v[:3] + [v[2] * 0.99] for k, v in YF.items()}
    _fake_yf(monkeypatch, full)
    monkeypatch.setattr(trd, "_sina_daily", lambda sym, datalen=40: (pd.Series(SINA[sym], index=DAYS), None))
    o = trd.fetch_a_share_structure_obs(dt.date(2026, 11, 13))
    assert o["as_of"] == "2026-11-13" and abs(o["cyb_ret"] + 0.01) < 1e-6
    assert o["source"].startswith("159915:yfinance")


def test_sina_down_falls_back_to_yf_prior_day(monkeypatch):
    _fake_yf(monkeypatch, YF)
    monkeypatch.setattr(trd, "_sina_daily", lambda sym, datalen=40: (None, "down"))
    o = trd.fetch_a_share_structure_obs(dt.date(2026, 11, 13))
    assert o["as_of"] == "2026-11-12"


def test_mismatched_dates_use_common_day(monkeypatch):
    _fake_yf(monkeypatch, YF)
    def sina(sym, datalen=40):
        if sym == "sz159915":
            return pd.Series(SINA[sym], index=DAYS), None
        return None, "down"
    monkeypatch.setattr(trd, "_sina_daily", sina)
    o = trd.fetch_a_share_structure_obs(dt.date(2026, 11, 13))
    assert o["as_of"] == "2026-11-12"
    assert abs(o["hs300_ret"] - (4.114 / 4.15 - 1)) < 1e-6


def test_sina_rows_parse():
    s, e = trd.sina_rows_to_series([{"day": "2026-11-13", "close": "2.442"}])
    assert e is None and float(s.iloc[-1]) == 2.442
    assert trd.sina_rows_to_series([])[0] is None
