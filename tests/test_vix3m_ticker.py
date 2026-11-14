# -*- coding: utf-8 -*-
"""VIX3M 必须用 yfinance 的 ^VIX3M（裸 VIX3M 在 Yahoo 404，旧缓存因此靠人工盘中报价补，2026-11 出现 8 行偏差）。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "scripts"


def test_denominator_uses_caret_ticker():
    for f in ("denominator_state_daily.py", "backtest_denominator_state.py"):
        s = (ROOT / f).read_text(encoding="utf-8")
        assert '"vix3m": "^VIX3M"' in s, f
    assert '("vix3m", "^VIX3M")' in (ROOT / "prefetch_us_daily_bars.py").read_text(encoding="utf-8")


def test_tv_map_has_caret_alias():
    import sys
    sys.path.insert(0, str(ROOT))
    import bar_sources as bs
    assert bs.tv_symbol("^VIX3M") == "CBOE:VIX3M"
