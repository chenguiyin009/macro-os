# -*- coding: utf-8 -*-
from datetime import date
import pandas as pd

from scripts.loaders import (
    classify_missing_next_session,
    close_to_close,
    equal_weight_return_to,
    next_session_need_through,
    next_session_return,
    next_weekday,
    relative_return,
    session_return,
    theme_forward_return,
    theme_is_basket,
)


def _s(rows):
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.Series([r[1] for r in rows], index=idx, dtype=float)


def test_session_return_1d():
    s = _s([("2026-10-07", 100.0), ("2026-10-08", 102.0), ("2026-10-09", 101.0)])
    r, d = session_return(s, date(2026, 10, 8), 1)
    assert d == date(2026, 10, 9)
    assert abs(r - (101 / 102 - 1)) < 1e-9


def test_session_return_missing_horizon():
    s = _s([("2026-10-08", 100.0), ("2026-10-09", 101.0)])
    r, d = session_return(s, date(2026, 10, 9), 1)
    assert r is None and d is None


def test_session_return_5d():
    rows = [(f"2026-10-{d:02d}", 100.0 + d) for d in range(8, 16)]
    s = _s(rows)
    r, d = session_return(s, date(2026, 10, 8), 5)
    assert d == date(2026, 10, 13)
    assert abs(r - ((113 / 108) - 1)) < 1e-9


def test_relative_return():
    a = _s([("2026-10-08", 100.0), ("2026-10-09", 110.0)])
    b = _s([("2026-10-08", 100.0), ("2026-10-09", 105.0)])
    rel = relative_return(a, b, date(2026, 10, 8), 1)
    assert abs(rel - ((1.10 - 1) - (1.05 - 1))) < 1e-9


def test_next_weekday_skips_weekend():
    assert next_weekday(date(2026, 11, 11)) == date(2026, 11, 12)
    assert next_weekday(date(2026, 11, 6)) == date(2026, 11, 9)  # Friday → Monday


def test_need_through_returns_cached_next_session_when_present():
    s = _s([("2026-11-10", 4.0), ("2026-11-11", 4.1), ("2026-11-12", 4.2)])
    # already has a session after 11-11 → return that date so ensure_bars can skip
    need = next_session_need_through(s, date(2026, 11, 11), expected=date(2026, 11, 12))
    assert need == date(2026, 11, 12)
    # as_of 11-09 already has 11-10 in cache
    need2 = next_session_need_through(s, date(2026, 11, 9), expected=date(2026, 11, 10))
    assert need2 == date(2026, 11, 10)


def test_need_through_falls_back_to_expected_when_cache_stale():
    s = _s([("2026-11-10", 4.0), ("2026-11-11", 4.1)])
    need = next_session_need_through(s, date(2026, 11, 11), expected=date(2026, 11, 12))
    assert need == date(2026, 11, 12)
    need2 = next_session_need_through(None, date(2026, 11, 11), expected=date(2026, 11, 12))
    assert need2 == date(2026, 11, 12)


def test_next_session_return_first_bar_after_as_of():
    s = _s([("2026-11-11", 2.59), ("2026-11-12", 2.60)])
    r, d = next_session_return(s, date(2026, 11, 11))
    assert d == date(2026, 11, 12)
    assert abs(r - (2.60 / 2.59 - 1)) < 1e-9
    r2, d2 = next_session_return(s, date(2026, 11, 12))
    assert r2 is None and d2 is None


def test_classify_missing_next_session_reasons():
    r = classify_missing_next_session(
        as_of=date(2026, 11, 11),
        expected=date(2026, 11, 12),
        last_date=date(2026, 11, 11),
        source="cache-stale",
        notes=["yfinance failed 159915.SZ"],
        today=date(2026, 11, 13),
    )
    assert "抓取失败" in r
    assert "2026-11-12" in r
    r2 = classify_missing_next_session(
        as_of=date(2026, 11, 11),
        expected=date(2026, 11, 12),
        last_date=date(2026, 11, 11),
        source="yfinance",
        notes=[],
        today=date(2026, 11, 13),
    )
    assert "疑似假期或空数据" in r2
    r3 = classify_missing_next_session(
        as_of=date(2026, 11, 12),
        expected=date(2026, 11, 13),
        last_date=date(2026, 11, 12),
        source="cache",
        today=date(2026, 11, 13),
    )
    assert "尚未收盘" in r3
    r4 = classify_missing_next_session(
        as_of=date(2026, 11, 11),
        expected=date(2026, 11, 12),
        last_date=None,
        source="none",
        notes=["yfinance failed", "tv unmapped"],
        today=date(2026, 11, 13),
    )
    assert "抓取失败" in r4
    r5 = classify_missing_next_session(
        as_of=date(2026, 11, 11),
        expected=date(2026, 11, 12),
        last_date=date(2026, 11, 11),
        source="yfinance",
        notes=["yfinance跳过2026-11-12（其后出现 2026-11-13，疑似未收盘bar，未写入缓存）"],
        today=date(2026, 11, 13),
    )
    assert "抓取失败" in r5
    assert "疑似假期或空数据" not in r5


def test_equal_weight_return_requires_all_members():
    a = _s([("2026-11-11", 100.0), ("2026-11-12", 110.0)])
    b = _s([("2026-11-11", 100.0), ("2026-11-12", 105.0)])
    r, reason = equal_weight_return_to([a, b], date(2026, 11, 11), date(2026, 11, 12))
    assert reason is None
    assert abs(r - ((0.10 + 0.05) / 2)) < 1e-9
    short = _s([("2026-11-11", 100.0)])  # missing target
    r2, reason2 = equal_weight_return_to([a, short], date(2026, 11, 11), date(2026, 11, 12))
    assert r2 is None
    assert reason2 and "缺" in reason2


def test_theme_forward_return_skips_basket_without_substituting_etf(monkeypatch):
    theme = {"name": "AI应用软件", "proxy": "篮子", "key": "app", "members": ["PLTR", "APP"]}

    def _load(ticker):
        if ticker == "PLTR":
            return _s([("2026-11-11", 100.0), ("2026-11-12", 110.0)])
        return None  # APP missing — must skip, not use an ETF

    monkeypatch.setattr("scripts.loaders.load_close_series", _load)
    ret, tgt, reason = theme_forward_return(theme, date(2026, 11, 11), 1, target=date(2026, 11, 12))
    assert ret is None
    assert reason and "APP" in reason and "跳过该主题" in reason


def test_theme_forward_return_basket_when_members_present(monkeypatch):
    theme = {"name": "AI应用软件", "proxy": "篮子", "key": "app", "members": ["PLTR", "APP"]}

    def _load(ticker):
        if ticker == "PLTR":
            return _s([("2026-11-11", 100.0), ("2026-11-12", 110.0)])
        if ticker == "APP":
            return _s([("2026-11-11", 100.0), ("2026-11-12", 104.0)])
        return None

    monkeypatch.setattr("scripts.loaders.load_close_series", _load)
    ret, tgt, reason = theme_forward_return(theme, date(2026, 11, 11), 1, target=date(2026, 11, 12))
    assert reason is None
    assert tgt == date(2026, 11, 12)
    assert abs(ret - ((0.10 + 0.04) / 2)) < 1e-9


def test_theme_is_basket_by_proxy_or_key():
    assert theme_is_basket({"proxy": "篮子", "key": "app"})
    assert theme_is_basket({"proxy": "SMH", "key": "app"})  # key in BASKETS
    assert not theme_is_basket({"proxy": "SMH", "key": "semi"})


def test_close_to_close_aligns_on_common_date():
    s = _s([("2026-11-11", 100.0), ("2026-11-12", 101.0), ("2026-11-13", 110.0)])
    assert abs(close_to_close(s, date(2026, 11, 11), date(2026, 11, 13)) - 0.10) < 1e-9
    assert close_to_close(s, date(2026, 11, 11), date(2026, 11, 14)) is None


def test_load_tech_selects_by_state_not_etf_rank(tmp_path, monkeypatch):
    import json
    from scripts import loaders as L

    payload = {
        "as_of": "2026-11-11",
        "selected_theme": "半导体",
        "selected_theme_score": 54.0,
        "overall": {"avg_score": 42.69},
        "a_share_mapping": {"force_semi_downgrade": False, "semi_chip_stance": "中性对照（未达偏多门槛）"},
        "themes": [
            {"id": 1, "name": "半导体", "proxy": "SMH", "score": 54, "state": "中性观察", "rel5_pct": 0.76},
            {"id": 2, "name": "软件", "proxy": "IGV", "score": 44, "state": "中性观察", "rel5_pct": -1.0},
            {"id": 7, "name": "AI应用软件", "proxy": "篮子", "score": 74, "state": "早期轮动", "rel5_pct": 10.8},
            {"id": 13, "name": "AI硬件卖方", "proxy": "篮子", "score": 62, "state": "分数够、动能不足", "rel5_pct": 3.1},
            {"id": 3, "name": "云计算", "proxy": "SKYY", "score": 36, "state": "资金撤出", "rel5_pct": -1.3},
        ],
    }
    outdir = tmp_path / "output"
    outdir.mkdir()
    out = outdir / "tech_rotation_2026-11-11.json"
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(L, "TECH_ROT", tmp_path)
    monkeypatch.setattr(L, "TV_DESK", tmp_path)
    pred = L.load_tech("2026-11-11")
    assert pred is not None
    assert [t["name"] for t in pred.strongest] == ["AI应用软件"]
    assert [t["name"] for t in pred.weakest] == ["云计算"]
    assert pred.selected_score == 54.0
    assert pred.avg_score == 42.69
    assert pred.selected_rel5_pct == 0.76
    assert pred.selected_state == "中性观察"
