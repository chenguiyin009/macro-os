"""Phase 2 回归测试：Trinity OS v2.2.1 结构解析器 (StructureParser)。

锁定：
  * 分形摆动低点识别（左右窗口严格小于邻 bar）；
  * D3 低点 = 最近 lookback 内最低摆动低点；
  * J-1 回抽确认（更高低点 + 收阳）及其反面（下破结构）。
"""
from __future__ import annotations

import pandas as pd

from core.structure_parser import StructureParser, build_market_state


def _ohlc(lows, opens=None, closes=None, highs=None):
    n = len(lows)
    opens = opens if opens is not None else lows
    closes = closes if closes is not None else [x + 0.5 for x in lows]
    highs = highs if highs is not None else [x + 1.0 for x in lows]
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})


def test_detect_pivots_finds_swing_lows():
    lows = [10, 8, 12, 9, 14, 7, 15]
    df = _ohlc(lows)
    sp = StructureParser(lookback=20, pivot_left=1, pivot_right=1)
    idxs = [i for (i, _) in sp._detect_pivots(df)]
    assert 1 in idxs and 3 in idxs and 5 in idxs
    assert 0 not in idxs and 6 not in idxs


def test_find_d3_low_returns_lowest_recent_pivot():
    lows = [10, 8, 12, 9, 14, 7, 15]
    df = _ohlc(lows)
    sp = StructureParser(lookback=20, pivot_left=1, pivot_right=1)
    pivots = sp._detect_pivots(df)
    assert sp._find_d3_low(pivots, current_idx=6) == 7.0


def test_find_d3_low_respects_lookback():
    lows = [3, 8, 12, 9, 14, 7, 15]
    df = _ohlc(lows)
    sp = StructureParser(lookback=3, pivot_left=1, pivot_right=1)
    pivots = sp._detect_pivots(df)
    # 在 idx=6 处，lookback=3 只看 idx>=4 的摆动低点 -> 只有 5(7.0)
    assert sp._find_d3_low(pivots, current_idx=6) == 7.0


def test_j1_confirmation_true_when_higher_low_and_bullish():
    df = pd.DataFrame(
        {
            "open": [10.0, 8.5, 9.0],
            "high": [10.5, 9.0, 9.6],
            "low": [9.5, 8.0, 8.4],
            "close": [10.2, 8.7, 9.3],
        }
    )
    sp = StructureParser(j1_tolerance=0.005)
    # prev(idx1) low=8.0 == d3, 收阳(8.7>8.5) -> 确认
    assert sp._check_j1_pullback_confirmation(df, 2, 8.0) is True


def test_j1_confirmation_false_when_bearish_close():
    df = pd.DataFrame(
        {
            "open": [10.0, 8.5, 9.0],
            "high": [10.5, 9.0, 9.6],
            "low": [9.5, 8.0, 8.4],
            "close": [10.2, 8.3, 9.3],  # idx1 收阴
        }
    )
    sp = StructureParser(j1_tolerance=0.005)
    assert sp._check_j1_pullback_confirmation(df, 2, 8.0) is False


def test_j1_confirmation_false_when_undercut():
    df = pd.DataFrame(
        {
            "open": [10.0, 8.5, 9.0],
            "high": [10.5, 9.0, 9.6],
            "low": [9.5, 7.5, 8.4],  # prev 下破 d3=8.0
            "close": [10.2, 8.7, 9.3],
        }
    )
    sp = StructureParser(j1_tolerance=0.005)
    assert sp._check_j1_pullback_confirmation(df, 2, 8.0) is False


def test_parse_returns_structure_dict():
    lows = [10, 8, 12, 9, 14, 7, 15]
    df = _ohlc(lows)
    sp = StructureParser(lookback=20)
    out = sp.parse(df, 6)
    assert set(out.keys()) == {"d3_low", "j1_confirmed", "structure_valid"}
    assert out["d3_low"] == 7.0
    assert out["structure_valid"] is True


def test_build_market_state_wires_parser_and_scorer():
    class FakeScorer:
        def calculate(self, df, idx):
            return 0.9

    lows = [10, 8, 12, 9, 14, 7, 15]
    df = _ohlc(lows, highs=[x + 1.0 for x in lows])
    df["atr"] = [1.0] * len(lows)
    sp = StructureParser(lookback=20)
    ms = build_market_state(sp, FakeScorer(), df, 6, {"vix": 30.0})
    assert ms.d3_low == 7.0
    assert ms.spacetime_score == 0.9
    assert ms.macro_vix == 30.0
    assert ms.current_price == 15.5
