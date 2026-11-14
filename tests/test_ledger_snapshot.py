# -*- coding: utf-8 -*-
"""生产 pass A 自动落纸面账本快照（2026-11-14）。"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location("daily_allocator", os.path.join(HERE, "daily_allocator.py"))
da = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(da)


def test_prod_pass_targets_daily_abc(tmp_path):
    t = da.ledger_snapshot_target("output/daily_allocator_2026-11-13.json", "pine_half_crowd", str(tmp_path))
    assert t == os.path.join(str(tmp_path), "daily_abc_2026-11-13.json")


def test_shadow_and_recompute_skip(tmp_path):
    r = str(tmp_path)
    assert da.ledger_snapshot_target("output/daily_allocator_2026-11-13_mom12.json", "mom12", r) is None
    assert da.ledger_snapshot_target("output/daily_allocator_2026-11-13_blend.json", "blend", r) is None
    # 影子文件名但引擎被改回默认，也不落
    assert da.ledger_snapshot_target("output/daily_allocator_2026-11-13_blend.json", "pine_half_crowd", r) is None
    # 重算副本不落
    assert da.ledger_snapshot_target("x/daily_allocator_2026-11-12_recovery.json", "pine_half_crowd", r) is None
    # 默认文件名但非默认引擎不落
    assert da.ledger_snapshot_target("output/daily_allocator_2026-11-13.json", "mom12", r) is None
    assert da.ledger_snapshot_target(None, "pine_half_crowd", r) is None


def test_env_disable(tmp_path, monkeypatch):
    monkeypatch.setenv("GYBOT_NO_LEDGER_SNAPSHOT", "1")
    assert da.ledger_snapshot_target("output/daily_allocator_2026-11-13.json", "pine_half_crowd", str(tmp_path)) is None


def test_write_no_overwrite(tmp_path):
    r = str(tmp_path)
    jo = "output/daily_allocator_2026-11-12.json"
    p, st = da.write_ledger_snapshot(jo, "pine_half_crowd", '{"a":1}', r)
    assert st == "written" and open(p).read() == '{"a":1}'
    p, st = da.write_ledger_snapshot(jo, "pine_half_crowd", '{"a":1}', r)
    assert st == "same"
    p, st = da.write_ledger_snapshot(jo, "pine_half_crowd", '{"a":2}', r)
    assert st == "exists-kept" and open(p).read() == '{"a":1}'
    p, st = da.write_ledger_snapshot(jo, "pine_half_crowd", '{"a":2}', r, overwrite=True)
    assert st == "written" and open(p).read() == '{"a":2}'
