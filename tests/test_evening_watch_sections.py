# -*- coding: utf-8 -*-
"""晚报旁证 8–11 节：Q4 规则 v2 阈值判读与 consolidator 挂接（2026-11-14）。"""
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


# 2026-11-14：删除 v1 的「复现 10-29/11-12 手写结论」断言（用户：q4不能反推）。
# 以下只检验 v2 各阈值的边界语义，输入为人造数，不对应任何历史日期。
q4 = ews.q4


def test_rules_version_and_notes():
    assert q4.RULES_VERSION == "q4_view_rules_v2"
    assert set(q4.THRESHOLD_NOTES) == {"a1", "a2", "a3", "b", "c", "d", "e", "f"}


def test_a1_hike_path_thresholds():
    # ≥2 次加息被定价且 2Y 高于 EFFR ≥25bp → 支持
    assert q4.judge_a1(50.0, 4.13, 3.88)["verdict"] == "支持"
    # 1y 只定价约 1 次 → 未定
    assert q4.judge_a1(40.0, 4.30, 3.88)["verdict"] == "未定"
    # 1y ≥50 但 2Y−EFFR 只有 +20bp → 未定（两腿须同时确认）
    assert q4.judge_a1(80.0, 4.08, 3.88)["verdict"] == "未定"
    # 1y ≤25bp → 证伪；2Y 低于政策利率 → 证伪
    assert q4.judge_a1(25.0, 4.30, 3.88)["verdict"] == "证伪"
    assert q4.judge_a1(90.0, 3.85, 3.88)["verdict"] == "证伪"


def test_a2_five_percent_band():
    assert q4.judge_a2(4.70, 4.80)["verdict"] == "支持"   # 均值 4.75
    assert q4.judge_a2(4.50, 4.60)["verdict"] == "未定"
    assert q4.judge_a2(4.40, 4.55)["verdict"] == "证伪"   # 均值 4.475 < 4.50


def test_a3_long_bond_avoid():
    assert q4.judge_a3(-2.0, 0.0)["verdict"] == "支持"
    assert q4.judge_a3(0.5, 15.0)["verdict"] == "支持"
    assert q4.judge_a3(1.0, 5.0)["verdict"] == "未定"
    assert q4.judge_a3(3.0, 20.0)["verdict"] == "证伪"   # 证伪优先：长端大涨即回避付出代价


def test_b_tradability_and_deadline():
    assert q4.judge_b(2.0, -20.0, 1.0, "2026-11-13")["verdict"] == "支持"
    assert q4.judge_b(-1.9, 19.0, 9.9, "2026-11-13")["verdict"] == "未定"
    assert q4.judge_b(0.1, 1.0, 1.0, "2026-11-13")["verdict"] == "未定"   # 窗口内不证伪
    assert q4.judge_b(0.1, 1.0, 1.0, "2027-04-01")["verdict"] == "证伪"


def test_c_four_subsignals():
    # 实际现金 1.6pp 支持、DXY +1% 支持、2 个大跌日 支持、SPY−RSP +1pp 支持
    r = q4.judge_c(4.0, 2.4, 1.0, 2, -2.0, 2.0, 1.0)
    assert r["verdict"] == "支持" and r["score"] == 4
    # 实际现金 0.5pp 证伪、DXY -1% 证伪、0 大跌日且回撤 -1% 证伪、等权领先 证伪
    assert q4.judge_c(2.9, 2.4, -1.0, 0, -1.0, 0.0, 1.0)["verdict"] == "证伪"
    # 支持 2 证伪 1 → 净 1 → 未定
    r = q4.judge_c(4.0, 2.4, 1.0, 0, -1.0, 0.0, 0.5)
    assert r["subs"] == {"c1": "支持", "c2": "支持", "c3": "证伪", "c4": "未定"} and r["verdict"] == "未定"
    # 20D 回撤 -5% 即便无单日大跌也算回撤
    assert q4.judge_c(4.0, 2.4, 0.0, 0, -5.0, 0.0, 0.0)["subs"]["c3"] == "支持"


def test_d_e_f_thresholds():
    assert q4.judge_d(4.0)["verdict"] == "支持"
    assert q4.judge_d(3.9)["verdict"] == "未定"
    assert q4.judge_d(-4.0)["verdict"] == "证伪"
    assert q4.judge_e(6.0, 6.25)["verdict"] == "支持"
    assert q4.judge_e(6.0, 6.1)["verdict"] == "未定"
    assert q4.judge_e(6.5, 6.25)["verdict"] == "证伪"
    assert q4.judge_f(-5.0, 0.5, -1.0, 0.0)["verdict"] == "支持"
    assert q4.judge_f(-5.0, 0.5, -1.0, -2.0)["verdict"] == "未定"   # 金未弱于股
    assert q4.judge_f(1.0, 1.0, -2.0, -1.0)["verdict"] == "证伪"


def test_window_helpers():
    assert q4.drop_days([100, 98, 98, 99], -1.5) == 1
    assert abs(q4.max_drawdown_pct([100, 110, 99, 105]) - (-10.0)) < 1e-9
    assert q4.drop_days([100], -1.5) is None


def test_missing_data_never_judged():
    assert q4.judge_a1(None, 4.1, 3.9)["verdict"] == q4.MISSING
    assert q4.judge_b(1.0, None, 1.0, "2026-11-13")["verdict"] == q4.MISSING
    assert q4.judge_c(None, 2.4, 1.0, 2, -2.0, 2.0, 1.0)["verdict"] == q4.MISSING
    assert q4.judge_e(None, 6.0)["verdict"] == q4.MISSING
    assert q4.judge_f(None, 1, 1, 1)["verdict"] == q4.MISSING
    p = ews.build("2026-11-13", Path("/nonexistent"), fetch=False)
    assert set(p["verdicts"].values()) == {q4.MISSING}


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


BBK_CSV = """DATAFLOW;BBK_STD_FREQ;TIME_PERIOD;OBS_VALUE;BBK_TITLE
BBK:BBSSY(1.0);D;2026-11-06;3.22;x
BBK:BBSSY(1.0);D;2026-11-07;.;x
BBK:BBSSY(1.0);D;2026-11-09;3.26;x
BBK:BBSSY(1.0);D;2026-11-10;3.24;x
BBK:BBSSY(1.0);D;2026-11-11;3.30;x
BBK:BBSSY(1.0);D;2026-11-12;3.29;x
BBK:BBSSY(1.0);D;2026-11-13;3.37;x
"""


def test_parse_bbk_and_5d():
    pts = ews.parse_bbk_csv(BBK_CSV)
    assert len(pts) == 6 and pts[-1] == ("2026-11-13", 3.37)
    b = ews.bund_from_points(pts, "2026-11-13", "BBK")
    assert b["last"] == 3.37 and abs(b["chg5_bp"] - 15.0) < 1e-9
    assert ews.parse_bbk_csv('{"status":404}') == []


def test_fetch_bund_primary_then_fallback():
    b, notes = ews.fetch_bund10y("2026-11-13", get=lambda url, h: BBK_CSV)
    assert b["source"].startswith("Bundesbank BBSSY") and not notes
    calls = []
    def get(url, h):
        calls.append(url)
        if "BBSSY" in url:
            raise OSError("down")
        return BBK_CSV.replace("BBSSY", "BBSIS")
    b, notes = ews.fetch_bund10y("2026-11-13", get=get)
    assert "Svensson" in b["source"] and any("失败" in n for n in notes)


def test_manual_override_wins(tmp_path):
    p = ews.build("2026-11-13", tmp_path, bund=(3.3891, 3.2535), fetch=False)
    assert p["bund10y"]["source"].startswith("手工覆盖") and abs(p["bund10y"]["chg5_bp"] - 13.6) < 1e-9
