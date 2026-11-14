# -*- coding: utf-8 -*-
"""校准对A列：缓存缺下一交易日时用 Sina 只追加（2026-11-14，复用 tech-rotation a_share_bars）。"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import loaders  # noqa: E402


def _setup(tmp_path, monkeypatch):
    root = tmp_path / "tech_price_cache"
    root.mkdir()
    p = root / "159915.SZ.csv"
    p.write_text("Date,Close,Volume\n2026-11-10,2.509,\n2026-11-11,2.59,\n", encoding="utf-8")
    monkeypatch.setattr(loaders, "CACHE_CANDIDATES", [root])
    return p


def test_fill_appends_only_missing(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch)
    idx = pd.to_datetime(["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"])
    fresh = pd.Series([9.9, 9.9, 2.524, 2.442], index=idx)  # 旧日期故意不同，不得覆盖
    added, err = loaders.fill_a_share_from_sina("159915.SZ", date(2026, 11, 12), sina_fetch=lambda t: (fresh, None))
    assert err is None and added == [date(2026, 11, 12)]
    df = pd.read_csv(p)
    assert list(df["Close"]) == [2.509, 2.59, 2.524]
    added, err = loaders.fill_a_share_from_sina("159915.SZ", date(2026, 11, 12), sina_fetch=lambda t: (fresh, None))
    assert added == [] and err is None


def test_fill_skips_non_a_and_errors(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert loaders.fill_a_share_from_sina("^HSI", date(2026, 11, 12))[1] == "not a-share etf"
    added, err = loaders.fill_a_share_from_sina("159915.SZ", date(2026, 11, 12), sina_fetch=lambda t: (None, "sina down"))
    assert added == [] and err == "sina down"
