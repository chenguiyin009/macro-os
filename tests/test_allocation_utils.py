"""Tests for shared allocation helpers."""

from __future__ import annotations

import pytest

from core.allocation_utils import cap_group_exposure, normalize_allocation


ALLOWED = {"QQQ", "CASH", "GLD", "THEME_DEF", "SPY"}


def test_normalize_allocation_preserves_cash_remainder() -> None:
    alloc = normalize_allocation({"QQQ": 0.1, "CASH": 0.1}, ALLOWED, "CASH")

    assert alloc["QQQ"] == pytest.approx(0.1)
    assert alloc["CASH"] == pytest.approx(0.9)
    assert sum(alloc.values()) == pytest.approx(1.0)


def test_normalize_allocation_scales_down_overallocated_portfolio() -> None:
    alloc = normalize_allocation({"QQQ": 0.7, "CASH": 0.7}, ALLOWED, "CASH")

    assert alloc["QQQ"] == pytest.approx(0.5)
    assert alloc["CASH"] == pytest.approx(0.5)
    assert sum(alloc.values()) == pytest.approx(1.0)


def test_cap_group_exposure_scales_all_group_assets() -> None:
    target, capped = cap_group_exposure({"QQQ": 0.2, "SPY": 0.7, "CASH": 0.1}, {"QQQ", "SPY"}, 0.8)

    assert capped == pytest.approx(0.8)
    assert target["QQQ"] == pytest.approx(0.1777777778)
    assert target["SPY"] == pytest.approx(0.6222222222)
    assert target["CASH"] == pytest.approx(0.2)
