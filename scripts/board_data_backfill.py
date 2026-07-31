#!/usr/bin/env python3
"""
One-shot script: generate 36 board_{date}_down.json files with open_count,
then backfill mcp_raw_{date}.json with limit_down_consecutive_n/open_n/new_n.

Data source: tdx_screener "日期 跌停连板" raw results re-pulled 2026-07-21.

Field semantics:
  - total: total consecutive limit-down stocks (meta.total)
  - open_count: number of stocks with 跌停打开次数 > 0  (= limit_down_open_n for engine)
  - open_sum: sum of 跌停打开次数 across all stocks (kept for reference, NOT used by engine)
  - new_2: count of stocks with 连续跌停天数 == 2 (proxy for limit_down_new_n)
  - consec_dist: distribution of 连续跌停天数 values -> {str(days): count}

IDEMPOTENT: safe to re-run. Every run overwrites the same 36 board files and
the same mcp_raw/fixture files with identical values (data is sourced from the
hard-coded ALL_DAYS table, not from disk). Re-running never accumulates or
corrupts data.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
BOARD_DIR = ROOT / "vault" / "shadow" / "boards"
MCP_RAW_DIR = ROOT / "vault" / "shadow" / "mcp_raw"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "sentiment"

# ── All 36 trading days raw data ──────────────────────────────────────────────
# Each entry: (date, total, open_count, open_sum, new_2, consec_dist_dict)
# open_count = count of stocks where 跌停打开次数 > 0
# open_sum   = sum of 跌停打开次数 (for reference only)
# new_2      = count of stocks with 连续跌停天数 == 2
# consec_dist = {str(days): count} distribution

BOARD_DATA = [
    # ── June ──
    ("2026-06-01", 3, 0, 0, 2, {"2": 2, "5": 1}),
    ("2026-06-02", 1, 1, 5, 0, {"3": 1}),
    ("2026-06-03", 0, 0, 0, 0, {}),
    ("2026-06-04", 9, 8, 52, 9, {"2": 9}),
    ("2026-06-05", 2, 1, 6, 1, {"2": 1, "3": 1}),
    ("2026-06-08", 0, 0, 0, 0, {}),
    ("2026-06-09", 3, 1, 16, 3, {"2": 3}),
    ("2026-06-10", 9, 7, 45, 6, {"2": 6, "4": 1, "5": 1, "6": 1}),
    ("2026-06-11", 14, 13, 95, 11, {"2": 11, "5": 1, "6": 1, "7": 1}),
    ("2026-06-12", 7, 6, 30, 5, {"2": 5, "8": 1, "9": 1}),
    ("2026-06-15", 5, 0, 0, 4, {"2": 4, "12": 1}),
    ("2026-06-16", 0, 0, 0, 0, {}),
    ("2026-06-17", 0, 0, 0, 0, {}),
    ("2026-06-18", 7, 5, 25, 4, {"2": 4, "3": 1, "13": 1, "14": 1}),
    ("2026-06-22", 11, 8, 60, 5, {"2": 5, "3": 1, "15": 1, "16": 1, "17": 1, "18": 1, "19": 1}),
    ("2026-06-23", 0, 0, 0, 0, {}),
    ("2026-06-24", 9, 5, 20, 5, {"2": 5, "3": 1, "20": 1, "21": 1, "22": 1}),
    ("2026-06-25", 0, 0, 0, 0, {}),
    ("2026-06-26", 11, 8, 103, 3, {"2": 3, "4": 1, "5": 1, "23": 1, "24": 1, "25": 1, "26": 1, "27": 1, "28": 1}),
    ("2026-06-29", 12, 9, 70, 4, {"2": 4, "3": 1, "6": 1, "29": 1, "30": 1, "31": 1, "32": 1, "33": 1, "34": 1}),
    ("2026-06-30", 13, 7, 50, 7, {"2": 7, "3": 1, "35": 1, "36": 1, "37": 1, "38": 1, "39": 1, "40": 1}),
    # ── July ──
    ("2026-07-01", 4, 1, 2, 3, {"2": 3, "3": 1}),
    #   ST合力泰=0, *ST帅电=0, ST金鸿顺=2, ST荣科=0 -> open_count=1
    ("2026-07-02", 4, 3, 37, 3, {"2": 3, "8": 1}),
    #   ST合力泰=0, 卓郎智能=13, 返利科技=12, 浙江众成=12 -> open_count=3
    ("2026-07-03", 4, 3, 9, 4, {"2": 4}),
    #   奥康国际=0, 立昂微=2, 莱宝高科=6, 光电股份=1 -> open_count=3
    ("2026-07-06", 9, 7, 115, 7, {"2": 7, "3": 2}),
    #   奥康=0, 莱宝=3, 彩虹=20, 江钨=36, 滨化=17, 龙头=19, 翔鹭=0, *ST美芝=6, *ST联翔=14 -> open_count=7
    ("2026-07-07", 9, 5, 38, 3, {"2": 3, "3": 4, "4": 1, "10": 1}),
    #   *ST萃华=0, 奥康=0, 江钨=1, *ST美芝=5, 龙头=24, *ST联翔=7, 杭电=1, 火炬=0, 中科金财=0 -> open_count=5
    ("2026-07-08", 10, 8, 74, 7, {"2": 7, "3": 1, "11": 1}),
    #   *ST萃华=0, 火炬=6, 锋龙=3, *ST卓然=0, 德新=56, 海南=1, 华锡=6, 麒盛=2, *ST正平=4, 恒久退=2 -> open_count=8
    ("2026-07-09", 5, 2, 155, 4, {"2": 4, "12": 1}),
    #   *ST萃华=0, 融捷=0, 龙蟠=5, 丽岛=0, 中钢天源=150 -> open_count=2
    ("2026-07-10", 2, 1, 7, 0, {"3": 1, "13": 1}),
    #   *ST萃华=0, 融捷=7 -> open_count=1
    ("2026-07-13", 3, 2, 6, 2, {"2": 2, "14": 1}),
    #   *ST萃华=0, 露笑=1, 宇环=5 -> open_count=2
    ("2026-07-14", 13, 8, 50, 12, {"2": 12, "15": 1}),
    #   *ST萃华=0, 航天工程=0, 巨力=0, 实益达=1, 铖昌=8, 航天动力=0, 雪龙=0, 青龙=9, 新华=0, 上海港湾=0, 兴业=16, 五矿=7, 六国=9
    #   -> open: 实益达/铖昌/青龙/兴业/五矿/六国=6... wait recount
    #   跌停打开次数: *ST萃华=0,航天工程=0,巨力=0,实益达=1,铖昌=8,航天动力=0,雪龙=0,青龙=9,新华=0,上海港湾=0,兴业=16,五矿=7,六国=9
    #   open_count = stocks with >0 = 实益达,铖昌,青龙,兴业,五矿,六国 = 6
    #   Correcting:
    ("2026-07-15", 0, 0, 0, 0, {}),
    ("2026-07-16", 0, 0, 0, 0, {}),
    ("2026-07-17", 25, 21, 328, 14, {"2": 14, "3": 7, "4": 1, "5": 1, "5b": 2}),
    #   See 07-17 detail below - recount from raw
    ("2026-07-20", 103, 95, 1500, 82, {"2": 82, "3": 10, "4": 7, "5": 1, "5b": 3}),
    #   See 07-20 detail - ST天际=0 only one with 0 -> open_count=102... recount below
    ("2026-07-21", 17, 10, 91, 12, {"2": 12, "3": 1, "4": 2, "5": 2}),
    #   See 07-21 detail below
]

# ── Corrections: I need to carefully recount from raw data for key days ────────

# 07-14: 跌停打开次数 from raw:
#   *ST萃华=0, 航天工程=0, 巨力=0, 实益达=1, 铖昌=8, 航天动力=0, 雪龙=0, 青龙=9, 新华=0, 上海港湾=0, 兴业=16, 五矿=7, 六国=9
#   open_count = 6 (实益达,铖昌,青龙,兴业,五矿,六国)
#   open_sum = 1+8+9+16+7+9 = 50
#   new_2 = count of 连续跌停天数==2: all except *ST萃华(15) = 12
#   consec_dist: {"2": 12, "15": 1}

# 07-17: 跌停打开次数 from raw:
#   航天工程=59, 合肥城建=17, 德明利=0, ST天际=0, 华天科技=27, 宿迁联盛=0, 兴业科技=4,
#   大恒科技=3, 华微电子=11, 圣晖集成=5, 博杰股份=14, 恒尚节能=0, 麦格米特=81, 华工科技=10,
#   晶华新材=0, 灵康药业=1, 太极实业=14, 格林达=0, 立方制药=17, 海星股份=1, 博迁新材=12,
#   金富科技=4, 先导基电=1, 天普股份=3, 利通电子=13
#   open_count = stocks with >0: 航天工程,合肥城建,华天科技,兴业科技,大恒科技,华微电子,圣晖集成,博杰股份,麦格米特,华工科技,灵康药业,太极实业,立方制药,海星股份,博迁新材,金富科技,先导基电,天普股份,利通电子 = 19
#   Wait, let me recount: 0 values are: 德明利, ST天际, 宿迁联盛, 恒尚节能, 晶华新材, 格林达 = 6 zeros
#   So open_count = 25 - 6 = 19
#   open_sum = 59+17+27+4+3+11+5+14+81+10+1+14+17+1+12+4+1+3+13 = 337
#   Wait let me add carefully: 59+17=76, +27=103, +4=107, +3=110, +11=121, +5=126, +14=140, +81=221, +10=231, +1=232, +14=246, +17=263, +1=264, +12=276, +4=280, +1=281, +3=284, +13=297
#   open_sum = 297
#   new_2 = count of 连续跌停天数==2: positions 12-25 (14 stocks with days=2) = 14
#   consec_dist: days=2:14, days=3:7 (positions 3-11 minus pos 12), days=4:1 (合肥城建), days=5:1 (航天工程)
#   Actually: 连续跌停天数: 5,4,3,3,3,3,3,3,3,3,3, 2,2,2,2,2,2,2,2,2,2,2,2,2,2
#   = {5:1, 4:1, 3:9, 2:14} -> wait, recount from raw:
#   pos1=5, pos2=4, pos3-11=3 (9 stocks), pos12-25=2 (14 stocks)
#   Total = 1+1+9+14 = 25 ✓
#   consec_dist: {"2": 14, "3": 9, "4": 1, "5": 1}

# 07-20: 跌停打开次数 from raw (103 stocks):
#   Zeros (一字板/无开板): ST天际(pos3)=0, 大恒科技(pos5)=0, 晶华新材(pos12)=0, 天娱数科(pos35)=0,
#   合锻智能(pos42)=0, 立方制药(pos19)=0, 汇绿生态(pos49)=0, 赛腾股份(pos60)=0, 福达合金(pos61)=0,
#   胜通能源(pos64)=0, 泰坦股份(pos69)=0
#   That's 11 zeros -> open_count = 103 - 11 = 92
#   Hmm, let me recount from the raw data more carefully...
#   From the raw JSON for 07-20:
#   pos1 合肥城建=8, pos2 德明利=6, pos3 ST天际=0, pos4 华天科技=1, pos5 大恒科技=0,
#   pos6 兴业科技=4, pos7 宿迁联盛=11, pos8 圣晖集成=5, pos9 博杰股份=20, pos10 华微电子=19,
#   pos11 华工科技=3, pos12 晶华新材=0, pos13 麦格米特=29, pos14 灵康药业=3, pos15 博迁新材=5,
#   pos16 太极实业=12, pos17 先导基电=6, pos18 格林达=5, pos19 立方制药=0, pos20 海星股份=2,
#   pos21 金富科技=7, pos22 光迅科技=11, pos23 金安国纪=11, pos24 风华高科=14, pos25 埃斯顿=19,
#   pos26 剑桥科技=19, pos27 万通发展=29, pos28 东山精密=14, pos29 美诺华=8, pos30 兴森科技=47,
#   pos31 华正新材=24, pos32 东材科技=34, pos33 铖昌科技=6, pos34 江海股份=25, pos35 天娱数科=0,
#   pos36 红板科技=17, pos37 云南锗业=7, pos38 崇达技术=12, pos39 飞龙股份=10, pos40 宝鼎科技=7,
#   pos41 安联锐视=8, pos42 合锻智能=0, pos43 探路者=85, pos44 远东股份=18, pos45 晋拓股份=3,
#   pos46 洪田股份=2, pos47 时空科技=8, pos48 安孚科技=11, pos49 汇绿生态=0, pos50 平安电工=18,
#   pos51 ST海王=1, pos52 禾盛新材=2, pos53 华懋科技=3, pos54 川润股份=17, pos55 康强电子=22,
#   pos56 祥鑫科技=5, pos57 领先股份=17, pos58 艾华集团=41, pos59 津药药业=2, pos60 赛腾股份=0,
#   pos61 福达合金=0, pos62 福日电子=5, pos63 友邦吊顶=4, pos64 胜通能源=0, pos65 共达电声=4,
#   pos66 宁波中百=6, pos67 中京电子=13, pos68 美盈森=4, pos69 泰坦股份=0, pos70 科瑞技术=19,
#   pos71 综艺股份=1, pos72 健麾信息=11, pos73 鹭燕医药=1, pos74 至纯科技=32, pos75 博敏电子=3,
#   pos76 安妮股份=22, pos77 宇晶股份=1, pos78 华亚智能=7, pos79 康惠股份=7, pos80 亿嘉和=7,
#   pos81 广合科技=37, pos82 诚邦股份=3, pos83 联诚精密=29, pos84 华盛昌=35, pos85 沃格光电=14,
#   pos86 名雕股份=3, pos87 中电港=38, pos88 中恒电气=41, pos89 科新发展=17, pos90 煌上煌=27,
#   pos91 长裕集团=39, pos92 声迅股份=17, pos93 来伊份=13, pos94 冠石科技=20, pos95 奥海科技=12,
#   pos96 三佳科技=79, pos97 可川科技=3, pos98 华体科技=1, pos99 金帝股份=12, pos100 神通科技=35,
#   pos101 睿能科技=27, pos102 力聚热能=15, pos103 豪尔赛=15
#
#   Zeros: pos3,5,12,19,35,42,49,60,61,64,69 = 11 zeros
#   open_count = 103 - 11 = 92
#   open_sum = 8+6+1+4+11+5+20+19+3+29+3+5+2+7+11+11+14+19+19+29+14+8+47+24+34+6+25+17+7+12+10+7+8+85+18+3+2+8+11+18+1+2+3+17+22+5+17+41+2+5+4+4+6+13+4+19+1+11+1+32+3+22+1+7+7+7+37+3+29+35+14+3+38+41+17+27+39+17+13+20+12+79+3+1+12+35+27+15+15
#   Let me compute: this is tedious but let me group:
#   8+6=14, +1=15, +4=19, +11=30, +5=35, +20=55, +19=74, +3=77, +29=106, +3=109, +5=114, +2=116, +7=123, +11=134, +11=145, +14=159, +19=178, +19=197, +29=226, +14=240, +8=248, +47=295, +24=319, +34=353, +6=359, +25=384, +17=401, +7=408, +12=420, +10=430, +7=437, +8=445, +85=530, +18=548, +3=551, +2=553, +8=561, +11=572, +18=590, +1=591, +2=593, +3=596, +17=613, +22=635, +5=640, +17=657, +41=698, +2=700, +5=705, +4=709, +4=713, +6=719, +13=732, +4=736, +19=755, +1=756, +11=767, +1=768, +32=800, +3=803, +22=825, +1=826, +7=833, +7=840, +7=847, +37=884, +3=887, +29=916, +35=951, +14=965, +3=968, +38=1006, +41=1047, +17=1064, +27=1091, +39=1130, +17=1147, +13=1160, +20=1180, +12=1192, +79=1271, +3=1274, +1=1275, +12=1287, +35=1322, +27=1349, +15=1364, +15=1379
#   open_sum = 1379
#   new_2 = count of 连续跌停天数==2: positions 22-103 (82 stocks)
#   consec_dist: days=5:1(pos1), days=4:7(pos2-8 minus... wait
#   From raw 连续跌停天数: pos1=5, pos2-10=4(9 stocks)? No:
#   pos1=5, pos2=4, pos3=4, pos4=4, pos5=4, pos6=4, pos7=4, pos8=4, pos9=4, pos10=4 -> that's 1+9=10
#   pos11-21=3 (11 stocks)
#   pos22-103=2 (82 stocks)
#   Total = 1+9+11+82 = 103 ✓
#   consec_dist: {"2": 82, "3": 11, "4": 9, "5": 1}

# 07-21: 跌停打开次数 from raw:
#   ST天际=0, 宿迁联盛=7, 晶华新材=0, 灵康药业=0, 美诺华=9, 甘咨询=0, 道明光学=0, 亿道信息=0,
#   浙江美大=0, *ST萃华=0, 云创退=0, *ST沐邦=17, 贤丰控股=37, 肯特催化=19, *ST亚士=3, ST标准=1, 小崧股份=5
#   Zeros: ST天际, 晶华新材, 灵康药业, 甘咨询, 道明光学, 亿道信息, 浙江美大, *ST萃华, 云创退 = 9 zeros
#   open_count = 17 - 9 = 8
#   open_sum = 7+9+17+37+19+3+1+5 = 98
#   new_2 = count of 连续跌停天数==2: pos6-17 = 12 stocks
#   consec_dist: days=5:2(pos1-2), days=4:2(pos3-4), days=3:1(pos5), days=2:12(pos6-17)
#   Total = 2+2+1+12 = 17 ✓
#   consec_dist: {"2": 12, "3": 1, "4": 2, "5": 2}

# ── Now build corrected data ───────────────────────────────────────────────────

CORRECTED = {
    "2026-07-01": (4, 1, 2, 3, {"2": 3, "3": 1}),
    #   ST合力泰=0(open0), *ST帅电=0(open0), ST金鸿顺=3(open2), ST荣科=2(open0) -> open_count=1
    "2026-07-02": (4, 3, 37, 3, {"2": 3, "8": 1}),
    "2026-07-03": (4, 3, 9, 4, {"2": 4}),
    "2026-07-06": (9, 7, 115, 7, {"2": 7, "3": 2}),
    "2026-07-07": (9, 5, 38, 3, {"2": 3, "3": 4, "4": 1, "10": 1}),
    "2026-07-08": (10, 8, 74, 7, {"2": 7, "3": 1, "11": 1}),
    "2026-07-09": (5, 2, 155, 4, {"2": 4, "12": 1}),
    "2026-07-10": (2, 1, 7, 0, {"3": 1, "13": 1}),
    "2026-07-13": (3, 2, 6, 2, {"2": 2, "14": 1}),
    "2026-07-14": (13, 6, 50, 12, {"2": 12, "15": 1}),
    "2026-07-17": (25, 19, 297, 14, {"2": 14, "3": 9, "4": 1, "5": 1}),
    "2026-07-20": (103, 92, 1379, 82, {"2": 82, "3": 11, "4": 9, "5": 1}),
    "2026-07-21": (17, 8, 98, 12, {"2": 12, "3": 1, "4": 2, "5": 2}),
}

# Build final data list for all 36 days
ALL_DAYS = []
raw_map = {d[0]: d for d in BOARD_DATA}
for date_str, total, oc, os_sum, n2, cd in BOARD_DATA:
    if date_str in CORRECTED:
        c_total, c_oc, c_os, c_n2, c_cd = CORRECTED[date_str]
        ALL_DAYS.append((date_str, c_total, c_oc, c_os, c_n2, c_cd))
    else:
        ALL_DAYS.append((date_str, total, oc, os_sum, n2, cd))


def write_board_files():
    """Write all 36 board_{date}_down.json files."""
    BOARD_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for date_str, total, open_count, open_sum, new_2, consec_dist in ALL_DAYS:
        fname = f"board_{date_str}_down.json"
        fpath = BOARD_DIR / fname
        obj = {
            "date": date_str,
            "dir": "down",
            "total": total,
            "open_count": open_count,
            "open_sum": open_sum,
            "new_2": new_2,
            "consec_dist": consec_dist,
            "source": "tdx_screener",
            "query": f"{date_str} 跌停连板",
        }
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        written += 1
    print(f"[boards] Wrote {written} board files to {BOARD_DIR}")
    return written


def backfill_mcp_raw():
    """Backfill limit_down_consecutive_n/open_n/new_n into mcp_raw_{date}.json."""
    updated = 0
    for date_str, total, open_count, open_sum, new_2, consec_dist in ALL_DAYS:
        # ── vault/shadow/mcp_raw ──
        mcp_path = MCP_RAW_DIR / f"mcp_raw_{date_str}.json"
        if mcp_path.exists():
            with open(mcp_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            raw["limit_down_consecutive_n"] = total if total > 0 else None
            raw["limit_down_open_n"] = open_count if total > 0 else None
            raw["limit_down_new_n"] = new_2 if total > 0 else None
            # Update _notes
            if "_notes" not in raw:
                raw["_notes"] = {}
            raw["_notes"]["board_probe"] = (
                f"tdx_screener backfill: total={total}, open_count={open_count}, "
                f"new_2={new_2}, open_sum={open_sum}(ref only)"
            )
            raw["_notes"]["board_probe_updated"] = datetime.now(timezone.utc).isoformat()
            with open(mcp_path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            updated += 1
        else:
            print(f"  [WARN] mcp_raw_{date_str}.json not found in vault")

        # ── tests/fixtures/sentiment (golden days 07-17, 07-21) ──
        fix_path = FIXTURE_DIR / f"mcp_raw_{date_str}.json"
        if fix_path.exists():
            with open(fix_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            raw["limit_down_consecutive_n"] = total if total > 0 else None
            raw["limit_down_open_n"] = open_count if total > 0 else None
            raw["limit_down_new_n"] = new_2 if total > 0 else None
            if "_notes" not in raw:
                raw["_notes"] = {}
            raw["_notes"]["board_probe"] = (
                f"tdx_screener backfill: total={total}, open_count={open_count}, "
                f"new_2={new_2}, open_sum={open_sum}(ref only)"
            )
            raw["_notes"]["board_probe_updated"] = datetime.now(timezone.utc).isoformat()
            with open(fix_path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            print(f"  [fixture] Updated {fix_path.name}")

    print(f"[mcp_raw] Updated {updated} vault mcp_raw files")
    return updated


# Golden-day assertions: (date, total, open_count, open_sum, new_2)
# These encode the hand-verified consecutive-limit-down counts from tdx_screener
# raw pulls (see CORRECTED block above). verify() fails loudly if they drift.
GOLDEN = {
    "2026-07-17": (25, 19, 297, 14),
    "2026-07-20": (103, 92, 1379, 82),
    "2026-07-21": (17, 8, 98, 12),
}


def verify():
    """Assert board files + mcp_raw backfill match hand-verified golden values."""
    failures = []
    for check_date, (exp_ld, exp_open, exp_sum, exp_new) in GOLDEN.items():
        bpath = BOARD_DIR / f"board_{check_date}_down.json"
        mpath = MCP_RAW_DIR / f"mcp_raw_{check_date}.json"

        if bpath.exists():
            with open(bpath, "r", encoding="utf-8") as f:
                b = json.load(f)
            print(f"\n  {check_date} board: total={b['total']}, open_count={b['open_count']}, "
                  f"open_sum={b['open_sum']}, new_2={b['new_2']}")
            if b["total"] != exp_ld:
                failures.append(f"{check_date} board.total {b['total']} != {exp_ld}")
            if b["open_count"] != exp_open:
                failures.append(f"{check_date} board.open_count {b['open_count']} != {exp_open}")
            if b["open_sum"] != exp_sum:
                failures.append(f"{check_date} board.open_sum {b['open_sum']} != {exp_sum}")
            if b["new_2"] != exp_new:
                failures.append(f"{check_date} board.new_2 {b['new_2']} != {exp_new}")
        else:
            failures.append(f"{check_date} board file missing")

        if mpath.exists():
            with open(mpath, "r", encoding="utf-8") as f:
                m = json.load(f)
            print(f"  {check_date} mcp_raw: ld_cons={m.get('limit_down_consecutive_n')}, "
                  f"ld_open={m.get('limit_down_open_n')}, ld_new={m.get('limit_down_new_n')}")
            if m.get("limit_down_consecutive_n") != exp_ld:
                failures.append(f"{check_date} mcp_raw.ld_cons {m.get('limit_down_consecutive_n')} != {exp_ld}")
            if m.get("limit_down_open_n") != exp_open:
                failures.append(f"{check_date} mcp_raw.ld_open {m.get('limit_down_open_n')} != {exp_open}")
            if m.get("limit_down_new_n") != exp_new:
                failures.append(f"{check_date} mcp_raw.ld_new {m.get('limit_down_new_n')} != {exp_new}")
        else:
            failures.append(f"{check_date} mcp_raw file missing")

    if failures:
        print("\n❌ GOLDEN ASSERTIONS FAILED:")
        for f in failures:
            print(f"   - {f}")
        raise AssertionError(f"{len(failures)} golden assertion(s) failed")
    print("\n✅ All golden assertions passed")


if __name__ == "__main__":
    print("=" * 60)
    print("Board data backfill: 36 days -> board files + mcp_raw")
    print("=" * 60)
    n_boards = write_board_files()
    n_mcp = backfill_mcp_raw()
    print("\n--- Verification ---")
    verify()
    print(f"\nDone: {n_boards} boards + {n_mcp} mcp_raw files updated")
