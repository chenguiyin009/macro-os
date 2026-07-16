"""yfinance macro adapter tests with injectable downloader (no network)."""

from __future__ import annotations

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from adapters.yfinance_macro import YFinanceMacroAdapter


def _fake_download(symbols, period="15d"):
    idx = pd.date_range(end=datetime(2026, 7, 16), periods=10, freq="B")
    data = {}
    # Build multiindex columns like yfinance group_by=ticker
    frames = {}
    values = {
        "^VIX": np.linspace(15.5, 16.0, len(idx)),
        "^TNX": np.linspace(4.50, 4.55, len(idx)),
        "^TYX": np.linspace(5.00, 5.08, len(idx)),
        "DX-Y.NYB": np.linspace(101.0, 100.6, len(idx)),
        "GLD": np.linspace(370.0, 372.0, len(idx)),
        "QQQ": np.linspace(710.0, 717.0, len(idx)),
        "TIP": np.linspace(108.2, 108.0, len(idx)),
        "HYG": np.linspace(80.0, 79.8, len(idx)),
        "^IRX": np.linspace(3.70, 3.69, len(idx)),
    }
    for sym in symbols:
        close = values.get(sym, np.linspace(1, 2, len(idx)))
        frames[sym] = pd.DataFrame(
            {
                "Open": close,
                "High": close,
                "Low": close,
                "Close": close,
                "Volume": np.zeros(len(idx)),
            },
            index=idx,
        )
    return pd.concat(frames, axis=1)


def test_yfinance_adapter_maps_yields_and_vix() -> None:
    a = YFinanceMacroAdapter(downloader=_fake_download)
    fs = a.fetch()
    assert fs is not None
    assert fs.vix == 16.0
    assert abs(fs.nominal_10y - 4.55) < 1e-6
    assert abs(fs.nominal_30y - 5.08) < 1e-6
    assert fs.dxy is not None
    assert fs.nominal_10y_change_5d_bp is not None
    # must NOT invent tips_yield level from TIP ETF price
    assert fs.tips_yield is None
