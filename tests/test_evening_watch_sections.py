# -*- coding: utf-8 -*-
"""晚报旁证 8–11 节：规则 v1 判读与 consolidator 挂接（2026-11-14）。"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("evening_watch_sections", ROOT / "scripts" / "evening_watch_sections.py")
ews = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ews)


def test_helpers():
    pts = [("2026-11-0%d" % i, float(i)) for i in range(1, 8)]
    last, chg, d = ews.pct_change_n(pts, "2026-11-06", 5)
    assert last == 6.0 and d == "2026-11-06" and abs(chg - 500.0) < 1e-9
    last, diff, d = ews.diff_n(pts, "2026-11-07", 1)
    assert diff == 1.0
    assert ews.pct_change_n([], "2026-11-06", 5) == (None, None, None)


def test_verdicts_match_hand_written_1029_1112():
    # 10-29 手写：a 支持 / b 未定 / c 未定 / d 偏支持/未定 / f 证伪
    assert ews.verdict_a(64) == "支持"
    assert ews.verdict_b(-0.3, 3.8) == "未定"
    assert ews.verdict_c(0.2, 4.09, 17.19, 0.7) == "未定"
    assert ews.verdict_d(0.4, 3.2, -0.1) == "偏支持/未定"
    assert ews.verdict_f(-2.4, 0.2, -0.1, 0.7)[0] == "证伪"
    # 11-12 手写：a 支持 / b 未定 / c 偏支持 / d 证伪 / e 未定（H.8 同比缺）/ f 证伪
    assert ews.verdict_a(88.5) == "支持"
    assert ews.verdict_b(-1.5, -2.8) == "未定"
    assert ews.verdict_c(1.7, 4.23, 17.75, -1.1) == "偏支持"
    assert ews.verdict_d(-2.2, -1.0, -5.3) == "证伪"
    assert ews.verdict_e(None, None, 4.2).startswith("未定")
    assert ews.verdict_f(6.5, 1.7, -5.3, -1.1)[0] == "证伪"


def test_missing_data_never_judged():
    assert ews.verdict_a(None) == "数据缺失"
    assert ews.verdict_c(None, 4, 20, -1) == "数据缺失"
    assert ews.verdict_f(None, 1, 1, 1)[0] == "数据缺失"


def test_render_cp_units(tmp_path):
    p = ews.build("2026-11-13", tmp_path, fetch=False)
    p["cp"] = {"last": 1507.513, "wchg": 4.23, "date": "2026-11-11"}
    p["h8"] = {"last": 20083.1, "wchg": 58.0, "date": "2026-11-04", "yoy_pct": 6.44, "yoy_4w_ago_pct": 6.12}
    md = ews.render(p)
    assert "1507.5 十亿美元" in md and "1.508 万亿" in md and "亿美元（" not in md.split("10.")[1][:20]
    assert "shock_absorption_2026-11-13.json 缺失" in md


def test_consolidator_appends(tmp_path, monkeypatch):
    import daily_macro_consolidated as dmc
    (tmp_path / "evening_sections_2026-11-13.md").write_text("## ⑦ X\n", encoding="utf-8")
    calls = []
    md = dmc.append_evening_sections("# A\n", "2026-11-13", tmp_path, run=lambda s, a, e: calls.append(a) or True)
    assert md.endswith("## ⑦ X\n") and "---" in md and calls
    monkeypatch.setenv("GYBOT_NO_EVENING_SECTIONS", "1")
    assert dmc.append_evening_sections("# A\n", "2026-11-13", tmp_path, run=lambda *a: True) == "# A\n"


def test_consolidator_survives_failure(tmp_path):
    import daily_macro_consolidated as dmc
    assert dmc.append_evening_sections("# A\n", "2026-11-13", tmp_path, run=lambda *a: False) == "# A\n"
