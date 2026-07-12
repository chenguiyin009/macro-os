"""Phase 2 回归测试：Trinity OS v2.2.1 结构解析器（D 结构修订版）。

锁定：
  * 2-bar 窗口 H/L 分形识别；
  * D 结构校验（最近 4 pivot 须为 H-L-H-L 且 D3<D1）；
  * J-1 回抽确认（当前 low 守住结构 + 近 5 根出现过更低低点）；
  * parse 的早退/有效性门控（idx<20、pivot 不足、atr>0）。
"""
from __future__ import annotations

import pandas as pd

from core.structure_parser import StructureParser, StructureResult


def test_detect_pivots_finds_hl():
    highs = [10, 11, 15, 12, 9, 11, 16, 13, 10]
    lows = [9, 10, 14, 11, 8, 10, 15, 12, 9]
    df = pd.DataFrame({"open": lows, "high": highs, "low": lows, "close": lows, "atr": [1.0] * 9})
    sp = StructureParser()
    tuples = [(t, v, i) for (t, v, i) in sp._detect_pivots(df)]
    assert ("H", 15.0, 2) in tuples
    assert ("L", 8.0, 4) in tuples


def test_detect_pivots_too_short():
    df = pd.DataFrame({"open": [1, 2], "high": [2, 3], "low": [1, 2], "close": [1, 2], "atr": [1, 1]})
    sp = StructureParser()
    assert sp._detect_pivots(df) == []


def test_validate_d_structure_valid():
    sp = StructureParser()
    assert sp._validate_d_structure([("H", 12, 1), ("L", 8, 3), ("H", 14, 5), ("L", 7, 7)]) == 7.0


def test_validate_d_structure_d3_not_below_d1():
    sp = StructureParser()
    assert sp._validate_d_structure([("H", 12, 1), ("L", 8, 3), ("H", 14, 5), ("L", 9, 7)]) is None


def test_validate_d_structure_wrong_types():
    sp = StructureParser()
    assert sp._validate_d_structure([("H", 12, 1), ("H", 8, 3), ("H", 14, 5), ("L", 7, 7)]) is None


def test_j1_confirmation_true():
    sp = StructureParser()
    df = pd.DataFrame({"low": [10.0] * 20 + [8.0, 8.0, 5.0, 8.0, 8.0]})
    assert sp._check_j1_pullback_confirmation(df, 7.0) is True


def test_j1_confirmation_false_no_dip():
    sp = StructureParser()
    df = pd.DataFrame({"low": [10.0] * 20 + [8.0, 8.0, 8.0, 8.0, 8.0]})
    assert sp._check_j1_pullback_confirmation(df, 7.0) is False


def test_parse_too_short_invalid():
    df = pd.DataFrame(
        {"open": [1.0] * 10, "high": [2.0] * 10, "low": [0.5] * 10, "close": [1.0] * 10, "atr": [1.0] * 10}
    )
    sp = StructureParser()
    res = sp.parse(df, 9)
    assert res.structure_valid is False
    assert res.d3_low is None


def test_parse_returns_structure_result():
    low = [8.0] * 30
    df = pd.DataFrame(
        {"open": low, "high": [x + 1 for x in low], "low": low, "close": low, "atr": [1.0] * 30}
    )
    sp = StructureParser()
    # 注入合法 D 结构，隔离 parse 的高层逻辑
    sp._detect_pivots = lambda d: [("H", 12.0, 1), ("L", 8.0, 3), ("H", 14.0, 5), ("L", 7.0, 7)]
    res = sp.parse(df, 29)
    assert isinstance(res, StructureResult)
    assert res.d3_low == 7.0
    assert res.structure_valid is True  # low[-1]=8 > d3=7 且 atr>0
    assert res.j1_confirmed is False    # 近 5 根无更低低点


def test_parse_j1_true_when_recent_dip():
    low = [8.0] * 25 + [8.0, 8.0, 5.0, 8.0, 8.0]
    df = pd.DataFrame(
        {"open": low, "high": [x + 1 for x in low], "low": low, "close": low, "atr": [1.0] * 30}
    )
    sp = StructureParser()
    sp._detect_pivots = lambda d: [("H", 12.0, 1), ("L", 8.0, 3), ("H", 14.0, 5), ("L", 7.0, 7)]
    res = sp.parse(df, 29)
    assert res.d3_low == 7.0
    assert res.j1_confirmed is True
    assert res.structure_valid is True
