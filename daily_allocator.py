# -*- coding: utf-8 -*-
"""
daily_allocator.py — 分数闸 + Trinity 滤网双方案（生产C / 研究A）
============================================================
回测锁定：
  分数闸 score = 1*denom + 1*nq_hard + 1*eq_acute + 0.5*eq_soft
    BEAR ≥1.5 · WARN ≥1.0 · 否则 OFF
  Trinity Phase1（trinity_phase1_bt_summary）：
    仅分数闸 OFF 时，科技卫星 × QQQ 结构执行系数；
    BEAR/WARN 分数闸绝对优先（Trinity 不得加仓对抗）。
  Trinity Phase2：
    industry_pack trinity_eligible → 个股袖；合计 ≤ min(15%, C卫星预算)；
    从 ETF 卫星挤出，不碰核心；BEAR 清零。

默认方案：
  C 稳健生产 + Trinity 滤网（上线默认）
  A 研究最优 + Trinity 滤网（研究/进攻对照）
  B 折中仅对照

Top3 排序（light_ablation_bt 落地）：
  科技生产主引擎 = mom6m_S3 裸 6M 动量 + 可选拥挤软罚 5pp（默认开；无 hard Top1 帽）
  标普 Top3 = pine_half_crowd（观测分 −12.5×拥挤）
  降回撤仍靠分数闸 + Book C，选股层不再加重罚（不做 −vol/−dd 全套）
  Gemini评审落地：
    闸连续化 sat_mult=clamp((1.5-score)/(1.5-0.5),0,1)；标签仍 OFF/WARN/BEAR
    Top3 按 rank_score 加权，单票≤15%

科技：核心 QQQ / 卫星 6 主题 ETF
标普：核心 SPY / 卫星 11 板块 ETF（Phase1 滤网默认只改科技卫星）
"""
from __future__ import annotations

import os
import re
import sys
import json
import glob
import argparse
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))

def _trinity_bundle(extra_symbols=None):
    """Phase0/1: 指数关键位 + 科技执行系数。失败则空。"""
    try:
        from trinity_levels import analyze_many, format_levels_md, index_exec_mult, apply_sat_mult
    except Exception as e:
        print(f"  ! trinity_levels 不可用: {e}", file=sys.stderr)
        return {"md": "", "mult": 1.0, "mult_why": "trinity不可用", "snaps": {}, "apply_sat_mult": None}
    syms = ["QQQ", "SPY", "SOXX"]
    if extra_symbols:
        for s in extra_symbols:
            if s and s not in syms:
                syms.append(s)
    snaps = analyze_many(syms)
    md = format_levels_md(snaps)
    mult, why = index_exec_mult(snaps, prefer="QQQ")
    return {"md": md, "mult": mult, "mult_why": why, "snaps": {k: (v.to_dict() if v else None) for k, v in snaps.items()}, "apply_sat_mult": apply_sat_mult}


THEME_ETF = {
    "半导体": "SMH", "软件": "IGV", "云计算": "SKYY",
    "网络安全": "CIBR", "机器人": "BOTZ", "AI综合": "AIQ",
}
SECTOR_ETF = {
    "科技": "XLK", "金融": "XLF", "能源": "XLE", "医疗保健": "XLV",
    "工业": "XLI", "材料": "XLB", "必需消费": "XLP", "公用事业": "XLU",
    "房地产": "XLRE", "可选消费": "XLY", "通信服务": "XLC", "小盘": "IWM",
    "等权": "RSP", "大科技": "QQQ",
}
SECTOR_MOM_POOL = [
    "科技", "金融", "能源", "医疗保健", "工业", "材料",
    "必需消费", "公用事业", "房地产", "可选消费", "通信服务",
]

# 分数闸参数（回测锁定）
SCORE_BEAR_THR = 1.5
SCORE_WARN_LO = 1.0
EQ_ACUTE_SMOOTH = -0.07
EQ_ACUTE_RAW = -0.10
EQ_SOFT_LO = -0.07
EQ_SOFT_HI = -0.05
# eq_acute 恢复退出（2026-11-14，回测见 output/research/gate_recovery_exit_bt_2026-11-14.*）
EQ_ACUTE_RAW_MODE = "current"     # current=当前20日峰值回撤(trajectory.naive)；window=旧口径近21根最坏峰谷
RECOVERY_EXIT_ENABLED = True      # acute 且 自谷底反弹≥10% 且 5日回撤斜率>0 → acute 降为 soft(0.5)
RECOVERY_MIN_REBOUND = 0.10
RECOVERY_SLOPE_DAYS = 5
WARN_A_EQUITY = 0.75   # Book A WARN 时保留权益比例
WARN_SAT_FRAC = 0.50   # Book B/C WARN 时卫星保留比例
TRINITY_FILTER_ENABLED = True  # 生产默认开启 Phase1 滤网（回测 C/A + Trinity）
TRINITY_APPLY_SPY = False      # 标普卫星暂不打折（与 phase1 回测一致）
TRINITY_PHASE2_ENABLED = True  # Phase2 个股卫星：写入指令 + ≤C卫星预算硬约束
TRINITY_PHASE2_PACKS = ("tech_semi",)

# ---- Top3 排序引擎（light_ablation_bt）----
TECH_TOP3_ENGINE = "mom6m"       # 科技生产：6M 价格动量（非 pine 分）
TECH_S3_SOFT_CROWD = True        # mom6m - 5pp×拥挤；不做 hard 过热 Top1 帽
SECTOR_TOP3_ENGINE = "pine_half_crowd"  # 标普默认：score - 12.5×拥挤；可选 mom12（见环境变量/CLI）
# 旗标：GYBOT_SECTOR_TOP3_ENGINE 或 SECTOR_TOP3_ENGINE = pine_half_crowd | mom12 | blend
_env_sec_eng = (os.environ.get("GYBOT_SECTOR_TOP3_ENGINE") or os.environ.get("SECTOR_TOP3_ENGINE") or "").strip()
if _env_sec_eng in ("pine_half_crowd", "mom12", "blend", "raw"):
    SECTOR_TOP3_ENGINE = _env_sec_eng
SECTOR_MOM12_SOFT_CROWD = True  # mom12 引擎拥挤软罚（与科技 S3 对称）
PINE_HALF_CROWD_PENALTY = 12.5
S3_SOFT_CROWD_PP = 0.05

def _apply_gate_params():
    """从 gate_params.json 覆盖分数闸阈值（标定结果）。"""
    global SCORE_BEAR_THR, SCORE_WARN_LO, EQ_ACUTE_SMOOTH, EQ_ACUTE_RAW
    global EQ_SOFT_LO, EQ_SOFT_HI, GATE_SAT_FULL_BELOW, GATE_SAT_ZERO_AT
    global EQ_ACUTE_RAW_MODE, RECOVERY_EXIT_ENABLED, RECOVERY_MIN_REBOUND, RECOVERY_SLOPE_DAYS
    path = os.path.join(HERE, "gate_params.json")
    if not os.path.isfile(path):
        return False
    try:
        d = json.load(open(path, encoding="utf-8"))
        pr = d.get("params") or d
        if "bear_thr" in pr:
            SCORE_BEAR_THR = float(pr["bear_thr"])
        if "warn_lo" in pr:
            SCORE_WARN_LO = float(pr["warn_lo"])
        if "eq_acute_smooth" in pr:
            EQ_ACUTE_SMOOTH = float(pr["eq_acute_smooth"])
        if "eq_acute_raw" in pr:
            EQ_ACUTE_RAW = float(pr["eq_acute_raw"])
        if "eq_soft_lo" in pr:
            EQ_SOFT_LO = float(pr["eq_soft_lo"])
        if "eq_soft_hi" in pr:
            EQ_SOFT_HI = float(pr["eq_soft_hi"])
        if "sat_full_below" in pr:
            GATE_SAT_FULL_BELOW = float(pr["sat_full_below"])
        if "sat_zero_at" in pr:
            GATE_SAT_ZERO_AT = float(pr["sat_zero_at"])
        if "eq_acute_raw_mode" in pr:
            EQ_ACUTE_RAW_MODE = str(pr["eq_acute_raw_mode"])
        if "recovery_exit_enabled" in pr:
            RECOVERY_EXIT_ENABLED = bool(pr["recovery_exit_enabled"])
        if "recovery_min_rebound" in pr:
            RECOVERY_MIN_REBOUND = float(pr["recovery_min_rebound"])
        if "recovery_slope_days" in pr:
            RECOVERY_SLOPE_DAYS = int(pr["recovery_slope_days"])
        return True
    except Exception as e:
        print(f"  ! gate_params 加载失败: {e}", file=sys.stderr)
        return False

# ---- Gemini 评审落地：闸连续化 + Top3 分数加权 ----
GATE_CONTINUOUS = True          # 卫星预算按 score 连续缩放（保留 OFF/WARN/BEAR 标签）
GATE_SAT_FULL_BELOW = 0.5       # score<=此值 → 卫星满配
GATE_SAT_ZERO_AT = SCORE_BEAR_THR  # score>=1.5 → 卫星 0
_GATE_PARAMS_LOADED = _apply_gate_params()
TOP3_SCORE_WEIGHT = True        # Top3 按 rank_score 加权（非等权）
TOP3_SINGLE_CAP = 0.15          # 单标的占组合上限 15%


STRATEGY_BOOKS = {
    "A": {
        "id": "A",
        "name": "A 研究最优",
        "role": "研究对照（A+Trinity）",
        "blurb": "100% 科技mom6m Top3（可软罚拥挤）/ 标普pine_half Top3 + 分数闸 + Trinity。BEAR→BIL；WARN→权益75%。研究对照。",
        "core_pct": 0.0,
        "sat_pct": 1.0,
        "switch_cuts": "all",
    },
    "C": {
        "id": "C",
        "name": "C 稳健生产",
        "role": "生产默认（C+Trinity）",
        "blurb": "70% 宽基 + 30% Top3（科技mom6m / 标普pine_half）+ 分数闸 + Trinity。BEAR只砍卫星；WARN卫星减半。**生产默认**。",
        "core_pct": 0.70,
        "sat_pct": 0.30,
        "switch_cuts": "satellite",
    },
    "B": {
        "id": "B",
        "name": "B 折中对照",
        "role": "对照",
        "blurb": "60% 宽基 + 40% Top3 + 同分数闸。仅作 A/C 之间对照，非默认执行锚点。",
        "core_pct": 0.60,
        "sat_pct": 0.40,
        "switch_cuts": "satellite",
    },
}
PRIMARY_BOOKS = ("A", "C")  # 主展示
ALL_BOOKS = ("A", "C", "B")

STATUS_ON = {"确认进入", "拥挤主升"}
STATUS_OFF = {"资金撤出"}
STATUSES_ALL = STATUS_ON | STATUS_OFF | {"中性观察", "中性", "早期转强", "早期轮动", "转弱"}
TRENDS_ALL = {"弱", "强", "偏强", "偏弱", "中性"}
_PCT = re.compile(r"^[−\-+]?[\d.]+%$")
_COMBINED = re.compile(r"^(\d+)\s*/\s*(.+)$")
_INT = re.compile(r"^\d+$")
SCORE_BAN = 45

HDR_TECH = "## 双方案对照（科技 A研究 / C生产）"
HDR_SPY = "## 双方案对照（标普 A研究 / C生产）"
HDR_SYNTH = "## 每日合成交易建议（分数闸 · A/C）"
HDR_GATE = "## 分数闸状态（bear≥1.5 / warn≥1）"
HDR_LEGACY_B = "## 对照 B 折中（非默认）"
HDR_TRINITY = "## 指数 Trinity 关键位"
HDR_PHASE2 = "## Phase2 个股 Trinity 卫星"
OLD_HDRS = [
    "## 交易指令（回测锚点 S3）", "## 交易指令（回测锚点 S6）",
    "## 交易指令（回测锚点 S8）", "## 交易指令（回测锚点 S5）",
    "## 当前建议执行策略", "## 每日合成交易建议",
    "## 每日合成交易建议（A/B/C）",
    "## 三策略对照（科技 A/B/C）", "## 三策略对照（标普 A/B/C）",
    "## 四腿开关状态",
    HDR_TECH, HDR_SPY, HDR_SYNTH, HDR_GATE, HDR_LEGACY_B, HDR_TRINITY,
]


def _to_pct(s):
    m = _PCT.match(s)
    if not m:
        return None
    return float(m.group(0).replace("−", "-").replace("+", "").replace("%", ""))


def _strip_emoji_status(s: str) -> str:
    return re.sub(r"^[🔴🔵🟢⚪🟠🟡]+", "", (s or "")).strip()


def parse_rotation_md(path):
    txt = open(path, encoding="utf-8").read()
    rows = []
    for line in txt.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2:
            continue
        name = cells[0]
        if name in ("主题", "项目", "归因", "检测", "指标", "信号"):
            continue
        pcts = []
        for c in cells[1:]:
            p = _to_pct(c)
            if p is not None:
                pcts.append(p)
        rs5d = pcts[0] if pcts else 0.0
        # 主题表常见列：5D相对, 20D相对, 60D相对, 绝对5D → rel60=第3个相对%
        rel60 = pcts[2] if len(pcts) >= 3 else None
        score, status = None, None
        for i in range(1, len(cells)):
            m = _COMBINED.match(cells[i])
            if m:
                score = int(m.group(1))
                status = _strip_emoji_status(m.group(2))
                break
        if score is None:
            for i in range(1, len(cells)):
                if _INT.match(cells[i]) and i + 1 < len(cells):
                    st = _strip_emoji_status(cells[i + 1])
                    if st in STATUSES_ALL or any(k in st for k in ("观察", "进入", "撤出", "主升")):
                        score = int(cells[i])
                        status = st
                        break
        if score is None:
            continue
        trend = next((c for c in cells if c in TRENDS_ALL), "")
        rows.append({"name": name, "rs5d": rs5d, "rel60": rel60, "trend": trend, "score": score, "status": status or ""})
    if not rows:
        raise ValueError(f"未在 {path} 解析到主题表")
    on = sum(1 for r in rows if r["status"] in STATUS_ON or "确认进入" in r["status"] or "拥挤主升" in r["status"])
    off = sum(1 for r in rows if r["status"] in STATUS_OFF or "资金撤出" in r["status"])
    return {"themes": rows, "breadth_on": on, "breadth_off": off}


def parse_sector_md(path):
    txt = open(path, encoding="utf-8").read()
    bench = None
    for line in txt.splitlines():
        if line.strip().startswith("|") and re.search(r"\d+\.\d+%", line) and "广度" not in line:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 8 and re.match(r"^\d+(\.\d+)?%$", cells[-2]):
                try:
                    bench = {
                        "score": int(float(cells[0])), "status": cells[1], "trend": cells[5],
                        "breadth": float(cells[-2].replace("%", "")), "reason": cells[-1],
                    }
                except ValueError:
                    bench = None
                if bench:
                    break
    sectors = []
    spat = re.compile(
        r"^\|\s*([^|]+?)\s*\|\s*([A-Z]+)\s*\|.*\|\s*(\d+)\s*(🔴|🔵|🟢|⚪|🟠|🟡)?([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*$"
    )
    for line in txt.splitlines():
        m = spat.match(line.strip())
        if not m:
            continue
        sname = m.group(1).strip()
        if sname in ("主题",):
            continue
        score = int(m.group(3))
        status = _strip_emoji_status(m.group(5) or "")
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        pcts = []
        for c in cells[2:8]:
            p = _to_pct(c)
            if p is not None:
                pcts.append(p)
        rs5d = pcts[0] if pcts else 0.0
        rel60 = pcts[2] if len(pcts) >= 3 else None
        sectors.append({"name": sname, "ticker": m.group(2), "score": score, "status": status, "rs5d": rs5d, "rel60": rel60})
    attrs = {}
    for line in txt.splitlines():
        am = re.match(r"^\|\s*([^|]+?)\s*\|\s*(\d+(?:\.\d+)?)\s*\|\s*(.*?)\s*\|\s*$", line.strip())
        if am and am.group(1).strip() not in ("归因",):
            try:
                attrs[am.group(1).strip()] = int(float(am.group(2)))
            except ValueError:
                pass
    if bench is None:
        raise ValueError(f"未在 {path} 解析到基准行")
    return {"bench": bench, "sectors": sectors, "attrs": attrs, "dominant_attr": max(attrs, key=attrs.get) if attrs else None}


def select_top3_tech(qqq):
    """科技生产 Top3：默认 mom6m_S3（+可选拥挤软罚）；pine 分仅作备注。"""
    try:
        from rotation_rank import select_top3_tech_mom6m
    except Exception as e:
        print(f"  ! rotation_rank 不可用，回退 pine 分排序: {e}", file=sys.stderr)
        tradeable = [t for t in qqq["themes"] if t["name"] in THEME_ETF]
        cand = [t for t in tradeable if not (t["status"] in STATUS_OFF or "撤出" in t["status"] or t["score"] < SCORE_BAN)]
        banned = [t for t in tradeable if (t["status"] in STATUS_OFF or "撤出" in t["status"] or t["score"] < SCORE_BAN)]
        if len(cand) < 3:
            for t in sorted(tradeable, key=lambda x: -x["score"]):
                if t not in cand:
                    cand.append(t)
                if len(cand) >= 3:
                    break
        cand.sort(key=lambda t: (-t["score"], -t["rs5d"], t["name"]))
        return cand[:3], banned

    if TECH_TOP3_ENGINE == "pine_half_crowd":
        # 观测/研究旁路：用 half_crowd 分
        from rotation_rank import annotate_pine_half
        tradeable = [t for t in qqq["themes"] if t["name"] in THEME_ETF]
        ann = annotate_pine_half(tradeable)
        banned = [t for t in ann if (t["status"] in STATUS_OFF or "撤出" in t["status"] or t.get("pine_raw", t["score"]) < SCORE_BAN)]
        cand = [t for t in ann if t not in banned]
        if len(cand) < 3:
            for t in sorted(ann, key=lambda x: -float(x.get("adj_score") or 0)):
                if t not in cand:
                    cand.append(t)
                if len(cand) >= 3:
                    break
        cand.sort(key=lambda t: (-float(t.get("adj_score") or 0), -float(t.get("rs5d") or 0), t["name"]))
        picks = cand[:3]
        qqq["_rank_meta"] = {
            "engine": "pine_half_crowd",
            "note": "科技旁路 pine_half_crowd（非默认生产）",
            "picks": picks,
        }
        return picks, banned

    res = select_top3_tech_mom6m(
        qqq["themes"],
        soft_crowd=bool(TECH_S3_SOFT_CROWD),
        soft_pp=float(S3_SOFT_CROWD_PP),
        k=3,
    )
    qqq["_rank_meta"] = {
        "engine": res.engine,
        "note": res.note,
        "mom_map": res.mom_map,
        "picks": res.picks,
    }
    return res.picks, res.banned


def select_top3_sector(spy):
    """标普 Top3：默认 pine_half_crowd；旗标 mom12 / blend。"""
    def _raw_fallback():
        pool = [s for s in spy["sectors"] if s["name"] in SECTOR_MOM_POOL]
        cand = [s for s in pool if not (s["status"] in STATUS_OFF or "撤出" in s["status"] or s["score"] < SCORE_BAN)]
        banned = [s for s in pool if (s["status"] in STATUS_OFF or "撤出" in s["status"] or s["score"] < SCORE_BAN)]
        if len(cand) < 3:
            for s in sorted(pool, key=lambda x: -x["score"]):
                if s not in cand:
                    cand.append(s)
                if len(cand) >= 3:
                    break
        cand.sort(key=lambda s: (-s["score"], -s.get("rs5d", 0), s["name"]))
        return cand[:3], banned

    try:
        from rotation_rank import (
            select_top3_sector_pine_half,
            select_top3_sector_mom12,
            select_top3_sector_blend,
        )
    except Exception as e:
        print(f"  ! rotation_rank 不可用，回退 raw score: {e}", file=sys.stderr)
        return _raw_fallback()

    eng = (SECTOR_TOP3_ENGINE or "pine_half_crowd").strip()
    if eng == "raw":
        return _raw_fallback()

    if eng == "mom12":
        res = select_top3_sector_mom12(
            spy["sectors"],
            SECTOR_MOM_POOL,
            score_ban=SCORE_BAN,
            status_off=STATUS_OFF,
            soft_crowd=bool(SECTOR_MOM12_SOFT_CROWD),
            soft_pp=float(S3_SOFT_CROWD_PP),
            k=3,
        )
        spy["_rank_meta"] = {
            "engine": res.engine,
            "note": res.note,
            "mom_map": res.mom_map,
            "picks": res.picks,
        }
        return res.picks, res.banned

    if eng == "blend":
        res = select_top3_sector_blend(
            spy["sectors"],
            SECTOR_MOM_POOL,
            score_ban=SCORE_BAN,
            status_off=STATUS_OFF,
            soft_crowd=bool(SECTOR_MOM12_SOFT_CROWD),
            soft_pp=float(S3_SOFT_CROWD_PP),
            k=3,
        )
        spy["_rank_meta"] = {
            "engine": res.engine,
            "note": res.note,
            "mom_map": res.mom_map,
            "picks": res.picks,
        }
        return res.picks, res.banned

    # 默认 pine_half_crowd
    res = select_top3_sector_pine_half(
        spy["sectors"],
        SECTOR_MOM_POOL,
        score_ban=SCORE_BAN,
        status_off=STATUS_OFF,
        k=3,
    )
    spy["_rank_meta"] = {
        "engine": res.engine,
        "note": res.note,
        "picks": res.picks,
    }
    return res.picks, res.banned


def load_macro(path: str | None) -> dict | None:
    if path and os.path.isfile(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception as e:
            print(f"  ! macro 读取失败: {e}", file=sys.stderr)
            return None
    pats = [
        os.path.join(HERE, "output", "daily_macro_*.json"),
        os.path.join(HERE, "output", "snapshot_*", "daily_macro_*.json"),
    ]
    cands = []
    for p in pats:
        cands.extend(glob.glob(p))
    if not cands:
        return None
    latest = max(cands, key=os.path.getmtime)
    try:
        return json.load(open(latest, encoding="utf-8"))
    except Exception:
        return None


def equity_legs_from_tech(tech: dict | None, raw_mode: str = "current", recovery: bool = True) -> dict:
    """eq_acute / eq_soft 腿（纯函数，便于单测）。

    raw_mode=current：raw_dd 取当前 20 日峰值回撤（trajectory.naive_20d_peak_dd_pct，
      与 gate_legs 标定口径一致）；缺失时回退到 tech_drawdown_raw（窗口最坏，保守）。
    raw_mode=window：旧口径，tech_drawdown_raw = 近 21 根收盘窗口内最坏峰谷回撤
      （深跌后会原样挂约 20 个交易日，是滞后主因）。
    recovery=True：acute 且 自谷底反弹≥RECOVERY_MIN_REBOUND 且 N日回撤斜率>0 → 降为 soft。
    """
    tech = tech or {}
    td = tech.get("decision") or {}
    traj = tech.get("trajectory") if isinstance(tech.get("trajectory"), dict) else None
    out = {"smooth_dd": None, "raw_dd": None, "raw_dd_window": None, "raw_dd_source": None,
           "eq_acute": False, "eq_soft": False, "eq_acute_why": "", "eq_soft_why": "",
           "recovery_exit": False, "recovery_why": ""}
    smooth = td.get("tech_drawdown")
    if smooth is None:
        smooth = tech.get("tech_drawdown")
    win_raw = tech.get("tech_drawdown_raw")
    cur_raw = None
    if traj is not None:
        try:
            cur_raw = float(traj.get("naive_20d_peak_dd_pct")) / 100.0
        except (TypeError, ValueError):
            cur_raw = None
    if raw_mode == "current" and cur_raw is not None:
        raw, src = cur_raw, "current_20d"
    elif win_raw is not None:
        raw, src = win_raw, "window_worst21"
    else:
        raw, src = cur_raw, ("current_20d" if cur_raw is not None else None)
    out["smooth_dd"] = float(smooth) if smooth is not None else None
    out["raw_dd"] = float(raw) if raw is not None else None
    out["raw_dd_window"] = float(win_raw) if win_raw is not None else None
    out["raw_dd_source"] = src

    acute = False
    bits = []
    if out["raw_dd"] is not None and out["raw_dd"] <= EQ_ACUTE_RAW:
        acute = True
        bits.append(f"raw_dd={out['raw_dd']:.2%}≤{EQ_ACUTE_RAW:.0%}")
    if out["smooth_dd"] is not None and out["smooth_dd"] <= EQ_ACUTE_SMOOTH:
        acute = True
        bits.append(f"smooth_dd={out['smooth_dd']:.2%}≤{EQ_ACUTE_SMOOTH:.0%}")
    if td.get("dampener_active"):
        acute = True
        bits.append("减震器触发")

    soft = False
    soft_bits = []
    if acute and recovery:
        from gate_recovery import recovery_exit_from_trajectory
        ok, why = recovery_exit_from_trajectory(
            traj, {"recovery_min_rebound": RECOVERY_MIN_REBOUND, "recovery_slope_days": RECOVERY_SLOPE_DAYS}
        )
        out["recovery_why"] = why
        if ok:
            out["recovery_exit"] = True
            acute = False
            soft = True
            bits.append(f"→恢复退出降为soft（{why}）")
            soft_bits.append("acute 恢复退出降档")
    out["eq_acute"] = acute
    out["eq_acute_why"] = "；".join(bits) if bits else f"未达急性（raw≤{EQ_ACUTE_RAW:.0%} 或 smooth≤{EQ_ACUTE_SMOOTH:.0%}）"
    if (not acute) and (not soft) and out["smooth_dd"] is not None:
        if EQ_SOFT_LO <= out["smooth_dd"] < EQ_SOFT_HI:
            soft = True
            soft_bits.append(f"smooth∈[{EQ_SOFT_LO:.0%},{EQ_SOFT_HI:.0%})")
    out["eq_soft"] = soft
    out["eq_soft_why"] = "；".join(soft_bits) if soft_bits else "无 soft 带"
    return out


def score_gate(macro: dict | None) -> dict:
    """分数闸：返回 legs + score + state∈{OFF,WARN,BEAR}。"""
    g = {
        "denom": False, "nq": False, "eq_acute": False, "eq_soft": False,
        "denom_why": "无 macro", "nq_why": "无 macro",
        "eq_acute_why": "无 macro", "eq_soft_why": "无 macro",
        "score": 0.0, "state": "OFF", "state_code": 0,
        "smooth_dd": None, "raw_dd": None,
        "bear_thr": SCORE_BEAR_THR, "warn_lo": SCORE_WARN_LO,
        "formula": "1*denom+1*nq+1*eq_acute+0.5*eq_soft",
    }
    if not macro:
        g["why"] = "无 daily_macro → score=0 → OFF 满仓"
        g["sat_mult"] = sat_mult_from_score(0.0)
        g["sat_mult_mode"] = "continuous" if GATE_CONTINUOUS else "discrete"
        return g

    syn = macro.get("synthesis") or {}
    plain = macro.get("plain_advice") or {}
    den = macro.get("denominator_state") or {}
    tech = macro.get("tech_dampener") or {}
    td = tech.get("decision") or {}
    shock = macro.get("shock_absorption") or {}

    # --- denom_tight 代理 ---
    main = str(den.get("main_state") or den.get("state") or "")
    dont = str(den.get("dont_do") or "")
    denom_tag = str(syn.get("denom_tag") or "")
    denom_hit = False
    bits = []
    for key in ("紧缩", "挤压", "TIGHT", "SQUEEZE", "LIQUIDITY_SQUEEZE", "TIGHT_LIQUIDITY"):
        if key.lower() in (main + denom_tag).lower() or key in main or key in denom_tag:
            denom_hit = True
            bits.append(f"标签含{key}")
            break
    # 明确「分裂+降仓」偏弱，只作旁证不够单独点亮；需与压力类并存
    if not denom_hit and "降仓位" in dont and any(k in main for k in ("压力", "紧缩", "挤压")):
        denom_hit = True
        bits.append("分母降仓+压力态")
    g["denom"] = denom_hit
    g["denom_why"] = "；".join(bits) if bits else f"未达紧缩/挤压（{main or '—'}）"

    # --- nq hard only ---
    nq = bool(plain.get("hard_veto") or syn.get("nq_veto"))
    g["nq"] = nq
    g["nq_why"] = (
        f"硬否决={plain.get('nq_state') or syn.get('nq_state') or 'yes'}"
        if nq else "无 hard_veto/nq_veto"
    )

    # --- equity acute / soft ---
    _eq = equity_legs_from_tech(tech, raw_mode=EQ_ACUTE_RAW_MODE, recovery=RECOVERY_EXIT_ENABLED)
    g.update(_eq)
    # 影子：旧规则（窗口最坏 raw + 无恢复退出），仅对照，不进仓位
    _leg = equity_legs_from_tech(tech, raw_mode="window", recovery=False)
    _leg_score = (
        (1.0 if g["denom"] else 0.0) + (1.0 if g["nq"] else 0.0)
        + (1.0 if _leg["eq_acute"] else 0.0) + 0.5 * (1.0 if _leg["eq_soft"] else 0.0)
    )
    g["legacy_rule"] = {
        "eq_acute": _leg["eq_acute"], "eq_soft": _leg["eq_soft"], "raw_dd": _leg["raw_dd"],
        "score": round(_leg_score, 2),
        "state": "BEAR" if _leg_score >= SCORE_BEAR_THR else ("WARN" if _leg_score >= SCORE_WARN_LO else "OFF"),
        "sat_mult": sat_mult_from_score(_leg_score),
    }

    score = (
        1.0 * (1.0 if g["denom"] else 0.0)
        + 1.0 * (1.0 if g["nq"] else 0.0)
        + 1.0 * (1.0 if g["eq_acute"] else 0.0)
        + 0.5 * (1.0 if g["eq_soft"] else 0.0)
    )
    g["score"] = round(score, 2)
    if score >= SCORE_BEAR_THR:
        g["state"] = "BEAR"
        g["state_code"] = 2
        g["why"] = f"score={g['score']}≥{SCORE_BEAR_THR} → BEAR"
    elif score >= SCORE_WARN_LO:
        g["state"] = "WARN"
        g["state_code"] = 1
        g["why"] = f"score={g['score']}∈[{SCORE_WARN_LO},{SCORE_BEAR_THR}) → WARN"
    else:
        g["state"] = "OFF"
        g["state_code"] = 0
        g["why"] = f"score={g['score']}<{SCORE_WARN_LO} → OFF 满配"
    # 兼容旧字段
    g["bear"] = g["state"] == "BEAR"
    g["warn"] = g["state"] == "WARN"
    g["sat_mult"] = sat_mult_from_score(g["score"])
    g["sat_mult_mode"] = "continuous" if GATE_CONTINUOUS else "discrete"
    return g


def sat_mult_from_score(score: float) -> float:
    """卫星预算乘数 ∈[0,1]。

    连续模式（默认）：
      score <= GATE_SAT_FULL_BELOW(0.5) → 1.0
      score >= GATE_SAT_ZERO_AT(1.5)    → 0.0
      中间线性： (1.5 - score) / (1.5 - 0.5)
    离散回滚：OFF=1, WARN=WARN_SAT_FRAC(C) / 对 A 另算, BEAR=0
      此处离散仅返回 C 语义乘数；A 在 weights_for_book 内处理。
    """
    s = float(score or 0.0)
    if not GATE_CONTINUOUS:
        if s >= SCORE_BEAR_THR:
            return 0.0
        if s >= SCORE_WARN_LO:
            return float(WARN_SAT_FRAC)
        return 1.0
    lo = float(GATE_SAT_FULL_BELOW)
    hi = float(GATE_SAT_ZERO_AT)
    if s <= lo:
        return 1.0
    if s >= hi:
        return 0.0
    return max(0.0, min(1.0, (hi - s) / (hi - lo)))


def _topn_sat_weights(topn, sat_share: float) -> list[tuple[str, str, float]]:
    """把 sat_share(0~1) 分给 Top3。默认按 rank_score 加权，单票≤TOP3_SINGLE_CAP。"""
    if sat_share <= 1e-12 or not topn:
        return []
    n = len(topn)
    # scores for weighting
    raw = []
    for t in topn:
        sc = t.get("rank_score")
        if sc is None:
            sc = t.get("mom_rank")
        if sc is None:
            sc = t.get("adj_score")
        if sc is None:
            sc = t.get("mom6m")
        if sc is None:
            sc = t.get("score")
        try:
            sc = float(sc)
        except Exception:
            sc = 0.0
        if sc != sc:  # nan
            sc = 0.0
        raw.append(max(sc, 0.0))  # 负动量当 0，避免负权重

    if (not TOP3_SCORE_WEIGHT) or sum(raw) <= 1e-12:
        w = [sat_share / n] * n
    else:
        # shift if all zero already handled; use softmax-ish via positive raw
        s = sum(raw) or 1.0
        w = [sat_share * (x / s) for x in raw]
        # cap single name at TOP3_SINGLE_CAP of whole portfolio
        cap = float(TOP3_SINGLE_CAP)
        # iterative cap+renorm among uncapped
        for _ in range(5):
            over = [i for i, wi in enumerate(w) if wi > cap + 1e-12]
            if not over:
                break
            excess = 0.0
            for i in over:
                excess += w[i] - cap
                w[i] = cap
            free = [i for i in range(n) if i not in over]
            if not free or excess <= 1e-12:
                break
            base = sum(w[i] for i in free) or 1.0
            for i in free:
                w[i] += excess * (w[i] / base if base > 1e-12 else 1.0 / len(free))
        # final renormalize to sat_share
        tw = sum(w) or 1.0
        w = [wi / tw * sat_share for wi in w]

    rows = []
    for t, wi in zip(topn, w):
        if t["name"] in THEME_ETF:
            tic = THEME_ETF[t["name"]]
        else:
            tic = t.get("ticker") or SECTOR_ETF.get(t["name"], "?")
        rows.append((tic, t["name"], round(wi * 100, 1)))
    return rows


def weights_for_book(spec, topn, core_ticker, state: str, gate: dict | None = None):
    """按闸状态 + 可选连续 sat_mult 生成权重行。

    gate 可选：含 score/sat_mult。GATE_CONTINUOUS 时用 sat_mult 缩放卫星预算；
    标签 state 仍用于 BEAR 核心处理文案。
    """
    core_pct = float(spec["core_pct"])
    sat_pct = float(spec["sat_pct"])
    cuts = spec["switch_cuts"]
    g = gate or {}
    score = float(g.get("score") or 0.0)
    sat_m = g.get("sat_mult")
    if sat_m is None:
        sat_m = sat_mult_from_score(score)
    sat_m = float(sat_m)

    # --- Book A (cuts=all): 权益= sat_pct * f(score)；连续时 f 从满仓平滑到 0 ---
    if cuts == "all":
        if not GATE_CONTINUOUS:
            if state == "BEAR":
                return [("BIL", "短债现金", 100.0)], "BEAR：100% BIL"
            if state == "WARN":
                s = WARN_A_EQUITY
                rows = _topn_sat_weights(topn, s)
                rows.append(("BIL", "短债现金", round((1.0 - s) * 100, 1)))
                return rows, f"WARN：权益{WARN_A_EQUITY:.0%}动量 + {1-WARN_A_EQUITY:.0%}BIL"
            rows = _topn_sat_weights(topn, 1.0)
            mode = "OFF 满配：100%卫星"
            if TOP3_SCORE_WEIGHT:
                mode += "·分数加权"
            return rows, mode
        # continuous A: equity_frac = sat_m 映射到 [0,1]，但 WARN 带不完全等同旧 75%
        # 使用：equity = sat_m * 1.0，在 score=1.0 时 sat_m=0.5 → 50% 权益（介于旧 WARN75 与更紧之间）
        # 为更接近旧 WARN：equity = WARN_A_EQUITY + (1-WARN_A_EQUITY)*sat_m_off 太复杂
        # 采用：equity = sat_m（满=1, 1.5=0），简单一致
        eq = sat_m
        if eq <= 1e-12:
            return [("BIL", "短债现金", 100.0)], f"连续闸 sat_mult={sat_m:.2f} → 100% BIL"
        rows = _topn_sat_weights(topn, eq)
        bil = 1.0 - eq
        if bil > 1e-9:
            rows.append(("BIL", "短债现金", round(bil * 100, 1)))
        mode = f"连续闸 A：权益{eq:.0%}(sat_mult={sat_m:.2f}) score={score:.2f}"
        if TOP3_SCORE_WEIGHT:
            mode += "·分数加权"
        return rows, mode

    # --- Book C/B: 核心固定，卫星 = sat_pct * sat_m ---
    if not GATE_CONTINUOUS:
        if state == "BEAR":
            rows = []
            if core_pct > 0:
                rows.append((core_ticker, core_ticker, round(core_pct * 100, 1)))
            rows.append(("BIL", "短债现金", round((1.0 - core_pct) * 100, 1)))
            return rows, f"BEAR：核心{core_pct:.0%}+卫星→BIL"
        if state == "WARN":
            s = sat_pct * WARN_SAT_FRAC
            c = core_pct
            b = 1.0 - c - s
            rows = []
            if c > 0:
                rows.append((core_ticker, core_ticker, round(c * 100, 1)))
            rows.extend(_topn_sat_weights(topn, s))
            if b > 1e-9:
                rows.append(("BIL", "短债现金", round(b * 100, 1)))
            mode = f"WARN：核心{core_pct:.0%}+卫星{s:.0%}+BIL{b:.0%}"
            if TOP3_SCORE_WEIGHT:
                mode += "·分数加权"
            return rows, mode
        # OFF
        rows = []
        if core_pct > 0:
            rows.append((core_ticker, core_ticker, round(core_pct * 100, 1)))
        rows.extend(_topn_sat_weights(topn, sat_pct))
        mode = f"OFF 满配：核心{core_pct:.0%}+卫星{sat_pct:.0%}"
        if TOP3_SCORE_WEIGHT:
            mode += "·分数加权"
        return rows, mode

    # continuous C/B
    s = sat_pct * sat_m
    c = core_pct
    # BEAR 标签且 cuts=satellite：核心保留（与旧一致）；卫星已由 sat_m→0
    b = max(0.0, 1.0 - c - s)
    rows = []
    if c > 0:
        rows.append((core_ticker, core_ticker, round(c * 100, 1)))
    rows.extend(_topn_sat_weights(topn, s))
    if b > 1e-9:
        rows.append(("BIL", "短债现金", round(b * 100, 1)))
    mode = (
        f"连续闸 C：核心{c:.0%}+卫星{s:.0%}(sat_mult={sat_m:.2f})+BIL{b:.0%} "
        f"score={score:.2f}/{state}"
    )
    if TOP3_SCORE_WEIGHT:
        mode += "·分数加权"
    return rows, mode



def _weights_line(weights):
    return " · ".join(f"{w['ticker']} {float(w['pct']):.0f}%" for w in weights)


def _rebuild_book_table_rows(snaps, universe_label):
    """用（可能已 Trinity 调整的）snaps 重绘主表仓位列。"""
    lines = [
        f"| 方案 | 角色 | 结构 | 闸下动作 | 今日目标仓位 | 今日操作 |",
        f"|---|---|---|---|---|---|",
    ]
    for snap in snaps:
        if snap.get("id") not in ("A", "C"):
            continue
        spec = STRATEGY_BOOKS[snap["id"]]
        struct = "100%卫星" if spec["core_pct"] == 0 else f"核心{spec['core_pct']:.0%}+卫星{spec['sat_pct']:.0%}"
        gate_act = "BEAR砍全部/WARN降权益" if spec["switch_cuts"] == "all" else "BEAR只砍卫星/WARN卫星减半"
        pos = _weights_line(snap["weights"])
        action = snap.get("action") or ""
        tm = snap.get("trinity_mult")
        if tm is not None and snap.get("state") == "OFF":
            pos = pos + f" · (×{float(tm):.2f})"
        lines.append(
            f"| **{spec['name']}** | {spec['role']} | {struct} | {gate_act} | {pos} | {action} |"
        )
    return "\n".join(lines)


def format_weight_rows(rows):
    return " · ".join(f"{tic} {pct:.0f}%" for tic, _n, pct in rows)


def _top_str_tech(items):
    bits = []
    for t in items:
        tic = THEME_ETF.get(t["name"], "?")
        mom = t.get("mom6m")
        if mom is not None:
            try:
                import math
                if mom == mom:  # not nan
                    bits.append(f"{tic}（{t['name']} mom6m {float(mom)*100:+.1f}%）")
                    continue
            except Exception:
                pass
        sc = t.get("adj_score", t.get("score"))
        bits.append(f"{tic}（{t['name']} {sc}）")
    return " · ".join(bits)



def _top_str_sector(items):
    bits = []
    for s in items:
        tic = SECTOR_ETF.get(s["name"], s.get("ticker", "?"))
        raw = s.get("pine_raw", s.get("score"))
        adj = s.get("adj_score")
        if adj is not None and raw is not None and abs(float(adj) - float(raw)) > 0.05:
            bits.append(f"{tic}（{s['name']} adj{float(adj):.0f}/raw{float(raw):.0f}）")
        else:
            bits.append(f"{tic}（{s['name']} {raw}）")
    return " · ".join(bits)



def build_gate_block(g: dict) -> str:
    st = g["state"]
    flag = {"OFF": "🟢 OFF 满配", "WARN": "🟡 WARN 轻降", "BEAR": "🔴 BEAR 降仓"}.get(st, st)
    sm = g.get("smooth_dd")
    rw = g.get("raw_dd")
    sm_s = f"{sm:.2%}" if sm is not None else "—"
    rw_s = f"{rw:.2%}" if rw is not None else "—"
    return (
        f"{HDR_GATE}\n\n"
        f"> 回测锁定：`score=1·denom+1·nq+1·eq_acute+0.5·eq_soft`；"
        f"**BEAR≥{SCORE_BEAR_THR}** · **WARN≥{SCORE_WARN_LO}**。"
        f"方案：**A 研究最优**（100%动量）｜ **C 稳健生产**（70/30）。\n\n"
        f"| 项 | 值 |\n|---|---|\n"
        f"| **状态** | **{flag}** |\n"
        f"| **score** | **{g['score']}**（{g['why']}） |\n"
        f"| **sat_mult** | **{g.get('sat_mult', 1):.2f}**（{'连续' if g.get('sat_mult_mode')=='continuous' else '离散'}；卫星预算×此系数） |\n"
        f"| 分母 denom | {'ON' if g['denom'] else 'off'} · {g['denom_why']} |\n"
        f"| NQ 硬否决 | {'ON' if g['nq'] else 'off'} · {g['nq_why']} |\n"
        f"| equity 急性 | {'ON' if g['eq_acute'] else 'off'} · {g['eq_acute_why']} |\n"
        f"| equity soft | {'ON' if g['eq_soft'] else 'off'} · {g['eq_soft_why']} |\n"
        f"| smooth_dd / raw_dd | {sm_s} / {rw_s}（raw口径 {g.get('raw_dd_source') or '—'}"
        f"{'；窗口最坏 ' + format(g['raw_dd_window'], '.2%') if g.get('raw_dd_window') is not None else ''}） |\n"
        f"| 恢复退出 | {'ON 降档' if g.get('recovery_exit') else 'off'} · {g.get('recovery_why') or '—'} |\n"
        + (
            f"| 旧规则对照（影子） | score={g['legacy_rule']['score']} → {g['legacy_rule']['state']}"
            f" · sat_mult={g['legacy_rule']['sat_mult']:.2f}（不进仓位） |\n"
            if isinstance(g.get("legacy_rule"), dict) else ""
        )
    )


def build_books_table(universe: str, core: str, top_str: str, topn, banned_str: str,
                      g: dict, books=PRIMARY_BOOKS):
    state = g["state"]
    title = HDR_TECH if universe == "tech" else HDR_SPY
    uni = "科技 QQQ·6主题" if universe == "tech" else "标普 SPY·11板块"
    lines = [
        f"{title}\n",
        f"> 宇宙：{uni} ｜ Top3：{top_str}",
        f"> 分数闸：{state}（score={g['score']}）— {g['why']}",
        f"> 禁止入选：{banned_str or '无'}\n",
        "| 方案 | 角色 | 结构 | 闸下动作 | 今日目标仓位 | 今日操作 |",
        "|---|---|---|---|---|---|",
    ]
    snaps = []
    details = []
    for key in books:
        spec = STRATEGY_BOOKS[key]
        rows, mode = weights_for_book(spec, topn, core, state, gate=g)
        pos = format_weight_rows(rows)
        if state == "OFF":
            action = "否（持有；Top3 变更等月末）"
        elif state == "WARN":
            action = "是：按 WARN 轻降（非清仓）"
        else:
            action = "是：按 BEAR 降仓" if spec["switch_cuts"] == "all" else "是：卫星→BIL，核心持有"
        struct = "100%卫星" if spec["core_pct"] == 0 else f"核心{spec['core_pct']:.0%}+卫星{spec['sat_pct']:.0%}"
        lines.append(
            f"| **{spec['name']}** | {spec['role']} | {struct} | "
            f"{'BEAR砍全部/WARN降权益' if spec['switch_cuts']=='all' else 'BEAR只砍卫星/WARN卫星减半'} | "
            f"{pos} | {action} |"
        )
        details.append(
            f"### {spec['name']}\n\n"
            f"- 角色：{spec['role']}\n"
            f"- 定义：{spec['blurb']}\n"
            f"- 模式：{mode}\n"
            f"- 目标仓位：{pos}\n"
            f"- 操作：{action}\n"
        )
        snaps.append({
            "id": key,
            "name": spec["name"],
            "role": spec["role"],
            "core_pct": spec["core_pct"],
            "sat_pct": spec["sat_pct"],
            "switch_cuts": spec["switch_cuts"],
            "state": state,
            "score": g["score"],
            "weights": [{"ticker": a, "name": b, "pct": c} for a, b, c in rows],
            "action": action,
            "mode": mode,
            "sat_mult": float(g.get("sat_mult") or 1.0),
            "score_weight": bool(TOP3_SCORE_WEIGHT),
        })
    lines.append("")
    lines.extend(details)
    lines.append(
        "**执行锚点**：默认纸面/生产跟 **C 稳健生产**；研究对比跟 **A 研究最优**。"
        "B 折中仅对照。优化只调分数腿/阈值，不改回 OR 一票否决。\n"
    )
    return "\n".join(lines), snaps


def build_b_appendix(universe, core, topn, g):
    spec = STRATEGY_BOOKS["B"]
    rows, mode = weights_for_book(spec, topn, core, g["state"], gate=g)
    pos = format_weight_rows(rows)
    return (
        f"{HDR_LEGACY_B}\n\n"
        f"> 非默认。{spec['blurb']}\n\n"
        f"- 模式：{mode}\n"
        f"- 目标：{pos}\n"
    ), {
        "id": "B", "name": spec["name"], "role": spec["role"],
        "core_pct": 0.6, "sat_pct": 0.4, "switch_cuts": "satellite",
        "state": g["state"], "score": g["score"],
        "weights": [{"ticker": a, "name": b, "pct": c} for a, b, c in rows],
        "action": "对照", "mode": mode,
    }


def build_synth(g, tech_snap, spy_snap, tech_top, spy_top) -> str:
    def pos(snaps, key):
        s = next(x for x in snaps if x["id"] == key)
        return " · ".join(f"{w['ticker']} {w['pct']:.0f}%" for w in s["weights"]), s["action"]

    ta, taa = pos(tech_snap, "A")
    tc, tca = pos(tech_snap, "C")
    sa, saa = pos(spy_snap, "A")
    sc, sca = pos(spy_snap, "C")
    st = g["state"]
    return "\n".join([
        f"{HDR_SYNTH}\n",
        "> 分数闸双方案并行；**不自动下单**。生产默认 **C**，研究对照 **A**。\n",
        "### 方案定义\n",
        f"- **A 研究最优**：{STRATEGY_BOOKS['A']['blurb']}",
        f"- **C 稳健生产**：{STRATEGY_BOOKS['C']['blurb']}\n",
        "### 今日分数闸\n",
        f"- **{st}** · score=**{g['score']}** · {g['why']}",
        f"- 腿：denom={'ON' if g['denom'] else 'off'} / nq={'ON' if g['nq'] else 'off'} / "
        f"acute={'ON' if g['eq_acute'] else 'off'} / soft={'ON' if g['eq_soft'] else 'off'}\n",
        "### 今日目标仓位\n",
        "| 方案 | 科技 | 科技操作 | 标普 | 标普操作 |",
        "|---|---|---|---|---|",
        f"| A 研究最优 | {ta} | {taa} | {sa} | {saa} |",
        f"| C 稳健生产 | {tc} | {tca} | {sc} | {sca} |\n",
        "### 今日五件事\n",
        f"1. **分数闸**：{st}（score={g['score']}）",
        f"2. **生产执行锚点 C**：科技 `{tc}` ｜ 标普 `{sc}`",
        f"3. **研究对照 A**：科技 `{ta}` ｜ 标普 `{sa}`",
        f"4. **Top3**：科技 {tech_top} ｜ 标普 {spy_top}",
        "5. **调仓**：WARN/BEAR 可日内执行；Top3 名次默认月末再平衡\n",
    ])


def _cut_section(txt, header):
    if header not in txt:
        return txt
    idx = txt.index(header)
    rest = txt[idx + len(header):]
    m = re.search(r"\n## ", rest)
    if m:
        end = idx + len(header) + m.start()
        return txt[:idx].rstrip() + "\n" + txt[end:].lstrip("\n")
    return txt[:idx].rstrip() + "\n"


def upsert_section(path, header, block):
    txt = open(path, encoding="utf-8").read()
    for ah in OLD_HDRS:
        if ah in txt:
            txt = _cut_section(txt, ah)
    if not txt.endswith("\n"):
        txt += "\n"
    open(path, "w", encoding="utf-8").write(txt + "\n" + block)
    return "updated"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotation", required=True)
    ap.add_argument("--sector", required=True)
    ap.add_argument("--macro", default=None)
    ap.add_argument("--qqq-above-200ma", type=lambda x: x.lower() in ("1", "true", "y"), default=None)
    ap.add_argument("--spy-above-200ma", type=lambda x: x.lower() in ("1", "true", "y"), default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--profile", default="production", help="兼容旧参数；固定输出 A+C")
    ap.add_argument("--acct-a", type=int, default=70)
    ap.add_argument("--acct-b", type=int, default=30)
    ap.add_argument("--calibrated-selection", action="store_true")
    ap.add_argument("--no-calibrated-selection", action="store_true")
    ap.add_argument(
        "--sector-top3-engine",
        default=None,
        choices=["pine_half_crowd", "mom12", "blend", "raw"],
        help="标普 Top3 引擎（默认 pine_half；mom12=12M动量；blend=pine×mom12 融合，均研究旗标）",
    )
    args = ap.parse_args()
    global SECTOR_TOP3_ENGINE
    if getattr(args, "sector_top3_engine", None):
        SECTOR_TOP3_ENGINE = args.sector_top3_engine

    qqq = parse_rotation_md(args.rotation)
    spy = parse_sector_md(args.sector)
    macro = load_macro(args.macro)
    g = score_gate(macro)

    tech_top, tech_ban = select_top3_tech(qqq)
    sec_top, sec_ban = select_top3_sector(spy)
    tech_rank_meta = qqq.get("_rank_meta") or {}
    sec_rank_meta = spy.get("_rank_meta") or {}
    tech_top_str = _top_str_tech(tech_top)
    sec_top_str = _top_str_sector(sec_top)
    tech_ban_str = "｜".join(
        f"{THEME_ETF.get(t['name'], '?')}（{t['name']} {t.get('score', t.get('pine_raw', ''))}）" for t in tech_ban
    ) or "无"
    sec_ban_str = "｜".join(
        f"{SECTOR_ETF.get(s['name'], s.get('ticker', '?'))}（{s['name']} "
        f"adj{float(s.get('adj_score', s.get('score', 0))):.0f}）" for s in sec_ban
    ) or "无"
    gate_sat_mult = float(g.get("sat_mult") or sat_mult_from_score(float(g.get("score") or 0.0)))
    rank_blk = (
        "## Top3 排序引擎（light_ablation）\n\n"
        f"- **科技生产**：`{tech_rank_meta.get('engine') or TECH_TOP3_ENGINE}`"
        f"{' · 拥挤软罚 5pp' if TECH_S3_SOFT_CROWD else ''} · **无 hard 过热 Top1 帽**\n"
        f"- **标普**：`{sec_rank_meta.get('engine') or SECTOR_TOP3_ENGINE}`"
        f"（默认 pine_half；旗标 mom12=12M动量）\n"
        "- 降回撤：分数闸 + Book C（选股层不再上 -vol/-dd 全套）\n"
        f"- **闸连续化**：{'ON' if GATE_CONTINUOUS else 'OFF'} · sat_mult={gate_sat_mult:.2f}（score→卫星预算平滑）\n"
        f"- **Top3加权**：{'ON 单票≤'+str(int(TOP3_SINGLE_CAP*100))+'%' if TOP3_SCORE_WEIGHT else 'OFF 等权'}\n"
        f"- 科技明细：{tech_rank_meta.get('note') or tech_top_str}\n"
        f"- 标普明细：{sec_rank_meta.get('note') or sec_top_str}\n"
    )

    gate_blk = build_gate_block(g)
    tech_blk, tech_snap = build_books_table("tech", "QQQ", tech_top_str, tech_top, tech_ban_str, g)
    spy_blk, spy_snap = build_books_table("spy", "SPY", sec_top_str, sec_top, sec_ban_str, g)
    tech_b_blk, tech_b_snap = build_b_appendix("tech", "QQQ", tech_top, g)
    spy_b_blk, spy_b_snap = build_b_appendix("spy", "SPY", sec_top, g)

    # Phase 0/1 Trinity（默认开启：C生产 + A研究 均带滤网元数据）
    extra = [THEME_ETF[t["name"]] for t in tech_top if t["name"] in THEME_ETF]
    tri = _trinity_bundle(extra_symbols=extra)
    tri_md = tri.get("md") or ""
    exec_mult = float(tri.get("mult") or 1.0)
    mult_why = tri.get("mult_why") or ""
    apply_fn = tri.get("apply_sat_mult")
    trinity_enabled = bool(TRINITY_FILTER_ENABLED)
    trinity_notes = []

    def _apply_side(snaps, core_set, side_name):
        nonlocal trinity_notes
        if not apply_fn:
            return
        for snap in snaps:
            snap["trinity_enabled"] = trinity_enabled
            snap["trinity_mult"] = exec_mult
            snap["trinity_why"] = mult_why
            snap["state"] = g.get("state")
            if (not trinity_enabled) or g.get("state") != "OFF":
                snap["trinity_note"] = (
                    f"滤网未改仓（gate={g.get('state')} 优先）"
                    if g.get("state") != "OFF"
                    else ("滤网关闭" if not trinity_enabled else f"系数{exec_mult:.2f}未改仓")
                )
                continue
            new_w, note = apply_fn(snap["weights"], exec_mult, "OFF", core_tickers=core_set)
            snap["weights_pre_trinity"] = [dict(x) for x in snap["weights"]]
            snap["weights"] = new_w
            snap["trinity_note"] = note
            # 刷新 action 文案
            if abs(exec_mult - 1.0) > 1e-9:
                snap["action"] = f"OFF+Trinity 卫星×{exec_mult:.2f}"
            trinity_notes.append(f"{side_name}-{snap['id']}:{note}")

    _apply_side(tech_snap, {"QQQ", "SPY"}, "科技")
    if TRINITY_APPLY_SPY:
        _apply_side(spy_snap, {"QQQ", "SPY"}, "标普")
    else:
        for snap in spy_snap:
            snap["trinity_enabled"] = trinity_enabled
            snap["trinity_mult"] = exec_mult
            snap["trinity_why"] = mult_why + "（标普侧Phase1默认不改）"
            snap["trinity_note"] = "标普卫星不应用Trinity滤网"
            snap["state"] = g.get("state")

    # ----- Phase2 个股卫星（硬约束 ≤ C 卫星预算）-----
    phase2 = {
        "enabled": False,
        "budget": 0.0,
        "selected": [],
        "stock_rows": [],
        "md": "",
        "note": "未加载",
    }
    phase2_notes = []
    if TRINITY_PHASE2_ENABLED:
        try:
            from trinity_stock_sleeve import build_phase2_bundle, apply_phase2_to_book_weights
            phase2 = build_phase2_bundle(
                gate_state=g.get("state") or "OFF",
                trinity_mult=exec_mult,
                pack_ids=list(TRINITY_PHASE2_PACKS),
                enabled=True,
            )
            for snap in tech_snap:
                new_w, p2note = apply_phase2_to_book_weights(
                    snap["weights"],
                    phase2,
                    g.get("state") or "OFF",
                    snap["id"],
                    core_tickers={"QQQ", "SPY"},
                )
                snap["weights_pre_phase2"] = [dict(x) for x in snap["weights"]]
                snap["weights"] = new_w
                snap["phase2_note"] = p2note
                phase2_notes.append(f"{snap['id']}:{p2note}")
            # B appendix
            if tech_b_snap:
                new_w, p2note = apply_phase2_to_book_weights(
                    tech_b_snap["weights"],
                    phase2,
                    g.get("state") or "OFF",
                    "B",
                    core_tickers={"QQQ", "SPY"},
                )
                tech_b_snap["weights"] = new_w
                tech_b_snap["phase2_note"] = p2note
        except Exception as e:
            phase2 = {
                "enabled": True,
                "budget": 0.0,
                "selected": [],
                "stock_rows": [],
                "md": f"### Phase2 个股卫星\n\n> 加载失败：{e}\n",
                "note": f"err:{e}",
            }
            print(f"  ! Phase2 失败: {e}", file=sys.stderr)


    # 用调整后权重重绘科技主表（A/C）
    if "｜ 今日目标仓位 |" in tech_blk or "| 今日目标仓位 |" in tech_blk:
        # 替换从表头到 ### 详情之前
        m = re.search(r"\| 方案 \| 角色 \| 结构 \|[\s\S]*?\n(?=### )", tech_blk)
        if m:
            tech_blk = tech_blk[:m.start()] + _rebuild_book_table_rows(tech_snap, "tech") + "\n\n" + tech_blk[m.end():]
    # 详情里目标仓位行同步
    for snap in tech_snap:
        if snap["id"] not in ("A", "C"):
            continue
        label = STRATEGY_BOOKS[snap["id"]]["name"]
        pos = _weights_line(snap["weights"])
        tech_blk = re.sub(
            rf"(### {re.escape(label)}[\s\S]*?- 目标仓位：)(.*)",
            rf"\g<1>{pos}",
            tech_blk,
            count=1,
        )
        if snap.get("trinity_note"):
            # append note once under 操作 if not present
            if "Trinity：" not in tech_blk.split(f"### {label}")[1][:400]:
                tech_blk = tech_blk.replace(
                    f"### {label}\n\n",
                    f"### {label}\n\n- Trinity：{snap.get('trinity_note')}（{mult_why}）\n",
                    1,
                )
        if snap.get("phase2_note") and "Phase2：" not in tech_blk.split(f"### {label}")[1][:500]:
            tech_blk = tech_blk.replace(
                f"### {label}\n\n",
                f"### {label}\n\n- Phase2：{snap.get('phase2_note')}\n",
                1,
            )


    filter_status = (
        f"ON×{exec_mult:.2f}" if trinity_enabled else "OFF"
    )
    tech_blk += (
        f"\n> **默认方案落地**：生产 **C+Trinity滤网**；研究 **A+Trinity滤网**。"
        f"今日滤网={filter_status}；{mult_why}。"
        + (("；" + "；".join(trinity_notes)) if trinity_notes else "")
        + "\n"
    )
    p2_md = (phase2.get("md") or "").strip()
    if p2_md:
        tech_blk += "\n" + p2_md + "\n"
    if phase2_notes:
        tech_blk += "> Phase2：" + "；".join(phase2_notes) + "\n"

    synth = build_synth(g, tech_snap, spy_snap, tech_top_str, sec_top_str)
    synth = synth.replace(
        "> 分数闸双方案并行；**不自动下单**。生产默认 **C**，研究对照 **A**。\n",
        "> 分数闸 + **Trinity 滤网**双方案；**不自动下单**。"
        "生产默认 **C+Trinity**，研究对照 **A+Trinity**。\n",
    )
    if "6. **Trinity" not in synth:
        synth = synth.replace(
            "5. **调仓**：WARN/BEAR 可日内执行；Top3 名次默认月末再平衡\n",
            "5. **调仓**：WARN/BEAR 可日内执行；Top3 名次默认月末再平衡\n"
            f"6. **Trinity 滤网**：{'开启' if trinity_enabled else '关闭'} · 系数 **{exec_mult:.2f}**（{mult_why}）；"
            f"仅分数闸 OFF 时折扣**科技**卫星；BEAR/WARN 不叠加\n"
            f"7. **Phase2 个股袖**：{'开' if phase2.get('enabled') else '关'} · 预算 "
            f"**{float(phase2.get('budget') or 0)*100:.1f}%**（≤C卫星）；"
            f"{phase2.get('note') or ''}\n",
        )

    tech_section = rank_blk + "\n" + gate_blk + "\n" + (tri_md + "\n" if tri_md else "") + tech_blk + "\n" + tech_b_blk + "\n" + synth
    spy_section = rank_blk + "\n" + gate_blk + "\n" + (tri_md + "\n" if tri_md else "") + spy_blk + "\n" + spy_b_blk + "\n" + synth

    r = upsert_section(args.rotation, HDR_TECH, tech_section)
    s = upsert_section(args.sector, HDR_SPY, spy_section)

    out = args.out or os.path.join(
        HERE, "rotation_results", f"daily_instruction_{date.today().strftime('%Y-%m-%d')}.md"
    )
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    head = (
        f"# 每日轮动双方案（分数闸 A研究 / C生产）— {date.today().isoformat()}\n\n"
        f"> 来源：{args.rotation} + {args.sector}\n"
        f"> macro：{'已加载' if macro else '缺失→OFF'}\n"
        f"> 闸：score bear≥{SCORE_BEAR_THR} / warn≥{SCORE_WARN_LO} ｜ "
        f"今日 **{g['state']}** score={g['score']}\n"
        f"> 执行锚点：**C 稳健生产 + Trinity 滤网 + Phase2个股袖(≤C卫星)**；研究对照：**A + Trinity + Phase2**。仅供研究，不构成投资建议。\n\n"
    )
    open(out, "w", encoding="utf-8").write(
        head + rank_blk + "\n" + synth + "\n---\n\n" + gate_blk + "\n" + (tri_md + "\n" if tri_md else "") + tech_blk + "\n" + spy_blk
    )

    payload = {
        "date": date.today().isoformat(),
        "gate": "score_b15_w1",
        "score_gate": g,
        "trinity": {
            "exec_mult": exec_mult,
            "why": mult_why,
            "snaps": tri.get("snaps") or {},
            "phase2": {
                "enabled": bool(phase2.get("enabled")),
                "budget": phase2.get("budget"),
                "c_sat_available": phase2.get("c_sat_available"),
                "note": phase2.get("note"),
                "selected": [
                    {
                        "symbol": x.get("symbol"),
                        "name": x.get("name"),
                        "exec_mult": x.get("exec_mult"),
                        "tier": x.get("tier"),
                        "structure_score": x.get("structure_score"),
                    }
                    for x in (phase2.get("selected") or [])
                    if isinstance(x, dict) and x.get("symbol")
                ],
                "stock_rows": phase2.get("stock_rows") or [],
                "candidates": phase2.get("candidates_all") or [],
            },
        },
        "tech": {
            "top3": [{"name": t["name"], "ticker": THEME_ETF[t["name"]], "score": t["score"]} for t in tech_top],
            "strategies": tech_snap + [tech_b_snap],
        },
        "spy": {
            "top3": [{"name": t["name"], "ticker": SECTOR_ETF.get(t["name"]), "score": t["score"]} for t in sec_top],
            "strategies": spy_snap + [spy_b_snap],
        },
        "defaults": {
            "production": "C+Trinity+P2",
            "research": "A+Trinity+P2",
            "trinity_filter": True,
            "trinity_apply_spy": False,
            "trinity_phase2": True,
            "tech_top3": tech_rank_meta.get("engine") or TECH_TOP3_ENGINE,
            "sector_top3": sec_rank_meta.get("engine") or SECTOR_TOP3_ENGINE,
            "s3_soft_crowd": bool(TECH_S3_SOFT_CROWD),
            "s3_soft_crowd_pp": S3_SOFT_CROWD_PP,
            "pine_half_crowd_penalty": PINE_HALF_CROWD_PENALTY,
            "gate_continuous": bool(GATE_CONTINUOUS),
            "top3_score_weight": bool(TOP3_SCORE_WEIGHT),
            "top3_single_cap": TOP3_SINGLE_CAP,
            "sat_mult": float(g.get("sat_mult") or 1.0),
        },
        "ranking": {
            "tech": tech_rank_meta,
            "spy": sec_rank_meta,
        },
        "meta": {
            "rotation_md": args.rotation,
            "sector_md": args.sector,
            "books": STRATEGY_BOOKS,
            "params": {
                "bear_thr": SCORE_BEAR_THR,
                "warn_lo": SCORE_WARN_LO,
                "warn_a_equity": WARN_A_EQUITY,
                "warn_sat_frac": WARN_SAT_FRAC,
            },
        },
    }
    jout = args.json_out or os.path.join(
        HERE, "rotation_results", f"daily_abc_{date.today().strftime('%Y-%m-%d')}.json"
    )
    open(jout, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False, indent=2))

    print(f"[OK] rotation md: {r} -> {args.rotation}")
    print(f"[OK] sector  md: {s} -> {args.sector}")
    print(f"[OK] 合成指令 -> {out}")
    print(f"[OK] JSON -> {jout}")
    print(f"     分数闸: {g['state']} score={g['score']} sat_mult={g.get('sat_mult')} | {g['why']}")
    print(f"     闸参: bear≥{SCORE_BEAR_THR} warn≥{SCORE_WARN_LO} acute≤{EQ_ACUTE_SMOOTH} loaded={_GATE_PARAMS_LOADED}")
    print(f"     生产C / 研究A | 科技Top3[{tech_rank_meta.get('engine') or TECH_TOP3_ENGINE}]: {'/'.join(THEME_ETF.get(t['name'], t['name']) for t in tech_top)}")
    print(f"     标普Top3[{sec_rank_meta.get('engine') or SECTOR_TOP3_ENGINE}]: {'/'.join(SECTOR_ETF.get(t['name'], t.get('ticker', t['name'])) for t in sec_top)}")
    if tech_rank_meta.get("note"):
        print(f"     {tech_rank_meta['note'][:180]}")
    if sec_rank_meta.get("note"):
        print(f"     {sec_rank_meta['note'][:180]}")


if __name__ == "__main__":
    main()
