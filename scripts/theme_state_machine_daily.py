#!/usr/bin/env python3
"""主题状态机日频复刻 — Python port of TradingView《市场主题状态机 v3.1r 宝宝巴士教学版》.

为什么做
--------
免费版 TV 有硬限制（技术指标提醒不可用、每张图指标数少），且原 Pine 脚本依赖
CME/CBOT/COMEX/NYMEX 期货实时数据（免费版仅延迟）。本脚本用 yfinance 代理品种复刻其核心
逻辑（14 品种 z-score -> 17 主题 -> 主导主题），日频产出与 TV 面板**等价**的「市场主题」判断，
完全脱离 TV 限制，并可并入 macro-os 现有日频分析流程。

数据源代理（与 TV 期货等价，日线足够）
--------------------------------------
  短端3M/^IRX + 10Y/^TNX + 30Y/^TYX（收益率水平；^IRX 非 2Y）
  TIP -> TIP | DXY -> DX-Y.NYB | 黄金 -> GLD | 原油 -> CL=F | 铜 -> HG=F
  标普/纳指/等权 -> SPY/QQQ/RSP | 欧元 -> EURUSD=X | 日元 -> JPY=X | 比特币 -> BTC-USD

核心算法（1:1 对齐 Pine v3.1r）
------------------------------
  u1(close) = (roc5 - sma200(roc5)) / stdev200(roc5)   # 5日收益率相对自身200日基线的 z 分数
  品种 x 符号约定：利率类(用收益率, x=+u1)、TIP(x=-u1)、USDJPY(x=-u1)、其余(x=+u1)
    —— 注：Pine 利率类用债券期货价(与利率反向)，故字面 -u1(期货价) 等价于 +u1(收益率)
  17 主题 b01..b17 布尔条件 + 旁证 n/3 + 持续 h(近3根>=2) + 已持续根数 g
  主导主题：h09(美元挤兑)/h10(套息平仓) 无条件置顶；否则旁证优先
  日线模式下 k9 恒为 true（日线不看盘内盘外），故所有"盘外估算"分支不走，直接用实测路径。

输出：output/theme_state_machine_<date>.md + .json（date = 锚品种最新可用日；盘中标 incomplete_bar）
"""
from __future__ import annotations

import argparse
import datetime as dt
from zoneinfo import ZoneInfo
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("theme_state_machine_daily")

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "output"
CACHE_DIR = REPO_ROOT / "data" / "_theme_state_closes"
DEFAULT_PROXY = "http://127.0.0.1:7890"
_PROXY_ENV_KEYS = ("HTTPS_PROXY", "HTTP_PROXY")

# (内部key, yfinance代码, 符号类型, 中文标签)
# 符号类型: rate=收益率(x=+u1) | inv=价格与利率反向(x=-u1, 如TIP) | pos=x=+u1 | neg=x=-u1(如USDJPY)
SYMBOLS: List[Tuple[str, str, str, str]] = [
    ("x01", "^IRX",     "rate", "短端利率(3M/^IRX)"),
    ("x02", "^TNX",     "rate", "中端利率(10Y)"),
    ("x03", "^TYX",     "rate", "超长端利率(30Y)"),
    ("x04", "TIP",      "inv",  "TIP ETF"),
    ("x05", "DX-Y.NYB", "pos",  "美元指数"),
    ("x06", "EURUSD=X", "pos",  "欧元"),
    ("x07", "JPY=X",    "neg",  "日元强度(USDJPY取反)"),
    ("x08", "GLD",      "pos",  "黄金(GLD)"),
    ("x09", "CL=F",     "pos",  "原油(WTI)"),
    ("x10", "HG=F",     "pos",  "铜"),
    ("x11", "SPY",      "pos",  "标普(SPY)"),
    ("x12", "QQQ",      "pos",  "纳指(QQQ)"),
    ("x13", "RSP",      "pos",  "等权RSP"),
    ("x14", "BTC-USD",  "pos",  "比特币"),
]

# 缺任一即 degraded（影响核心压力/利率叙事）
CRITICAL_KEYS = ("x01", "x02", "x03", "x04", "x05", "x11", "x12", "x13")
ANCHOR_KEY = "x11"  # SPY 美股交易日历锚
FFILL_LIMIT = 3
STALE_AFTER_BD = 1  # 相对期望 as_of 落后 >1 个交易日则 stale
DERIVED_LABELS = {
    "x21": "真实利率",
    "x24": "股票整体",
    "x25": "纳指-标普差",
    "x26": "广度:等权-标普",
    "x27": "避险需求(金+日元)",
    "x17": "商品复合(油70铜30)",
}

# theme id -> risk_bias
THEME_RISK_BIAS = {
    1: "risk_off", 2: "risk_off", 3: "risk_off", 4: "risk_off", 5: "risk_off",
    6: "risk_on", 7: "risk_off", 8: "risk_off",
    9: "risk_off", 10: "risk_off",
    11: "risk_on", 12: "mixed", 13: "mixed", 14: "mixed", 15: "risk_off",
    16: "mixed", 17: "mixed",
}
PRESSURE_THEME_IDS = frozenset({9, 10})

SYMBOL_LABELS = {key: label for key, _, _, label in SYMBOLS}

# 今日主角排序顺序（对应 Pine q06 数组：13 项）
LEADER_KEYS = ["x01", "x02", "x03", "x21", "x05", "x07", "x08", "x09", "x10", "x11", "x12", "x13", "x14"]
LEADER_LABELS = ["短端利率", "中端利率", "超长端利率", "真实利率", "美元", "日元",
                 "黄金", "原油", "铜", "标普", "纳指", "等权RSP", "比特币"]

THEME_NAMES = {
    0: "真实利率紧缩", 1: "通胀恐慌", 2: "粘性通胀信号", 3: "期限溢价·财政担忧",
    4: "卖美国", 5: "降息预期升温", 6: "增长恐慌", 7: "地缘避险",
    8: "美元挤兑", 9: "套息平仓·去杠杆", 10: "普涨·风险偏好强", 11: "科技独涨",
    12: "板块轮动·非撤退", 13: "风险偏好扛住", 14: "普跌·全面避险",
    15: "黄金无视真实利率", 16: "币圈独立行情",
}
THEME_DICTION = {
    0: "扣掉通胀后的资金成本在涨, 利率端带头 —— 对高估值资产最不友好的紧缩",
    1: "油带头、利率跟涨 —— 市场在交易通胀重新抬头",
    2: "商品没动但通胀定价在走宽 —— 粘性/服务类通胀信号, 最容易被忽视的一种",
    3: "长债领跌、黄金抗跌 —— 市场在担心债券供给和财政, 不是在担心加息",
    4: "利率升、美元却跌、黄金涨 —— 资金对美元资产的信任出问题, 罕见且重要",
    5: "短端利率带头下行、股票没崩 —— 市场在提前定价降息",
    6: "债涨、股跌、油铜齐跌 —— 市场在交易经济变差, 坏消息就是坏消息",
    7: "债涨股跌, 但油和金一起涨 —— 事件驱动的避险, 与经济基本面无关",
    8: "美元独强、连黄金都被抛 —— 全市场在抢现金, 最高级别的压力状态",
    9: "日元急升、纳指领跌 —— 借日元的杠杆盘在强平, 有自己的节奏, 别急着抄底",
    10: "涨得宽、铜也确认 —— 健康的风险偏好回升",
    11: "只有纳指在涨 —— 别把少数科技股的强当成整个市场的强",
    12: "纳指跌但大盘和等权没跌 —— 是换仓不是逃跑",
    13: "债和金那边有压力, 但股票和币没跌 —— 风险偏好还没被打断",
    14: "股和币一起下跌 —— 全面回避风险",
    15: "真实利率在涨, 黄金按理该跌却在涨 —— 有人在为财政/信用风险买保险; 它何时投降本身就是信号",
    16: "只有比特币在大动, 其他都安静 —— 币圈自己的事, 不必上升为宏观信号",
}
THEME_REVERSE = {
    0: "真实利率压力消退 (TIP 止跌反弹), 或 黄金转涨 + 美元回落",
    1: "油价回落, 且通胀预期差收窄回中性带",
    2: "通胀预期差回到中性带 (与商品重新一致)",
    3: "长债止跌 (超长端压力回落), 或 黄金开始下跌",
    4: "美元转强 —— 利率升美元升的正常关系恢复",
    5: "短端利率重新上行, 或 股票明显走弱",
    6: "油铜止跌企稳, 或 股票收复失地",
    7: "油价回落 (事件溢价消退), 退化为普通避险",
    8: "黄金止跌 且 美元回落 —— 抢现金结束",
    9: "日元升值动能衰竭 (强度回落到 0.5 以下)",
    10: "等权指数掉队 (涨势变窄), 或 铜转弱",
    11: "纳指与标普的强度差收敛",
    12: "标普 / 等权跟跌 —— 轮动证伪为撤退",
    13: "纳指与比特币同时转跌 —— 压力开始扩散",
    14: "任一品种率先企稳, 回到中性带并站住",
    15: "黄金投降转跌 = 压力缓和的确认信号; 一直不跌 = 故事升级",
    16: "其他品种开始共振 —— 升级为系统性事件",
}
THEME_FAMILY = {  # 主题 -> 分区 id
    0: 0, 1: 0, 2: 0, 3: 0, 4: 0,
    5: 1, 6: 1, 7: 1,
    8: 2, 9: 2,
    10: 3, 11: 3, 12: 3, 13: 3, 14: 3,
    15: 4, 16: 4,
}
FAMILY_NAMES = {0: "利率紧缩类", 1: "宽松·避险类", 2: "压力覆盖类", 3: "股票内部类", 4: "背离类"}

# re-key 0..16 -> 1..17 to align with b/h/g/n (b01..b17 in Pine)
THEME_NAMES = {k + 1: v for k, v in THEME_NAMES.items()}
THEME_DICTION = {k + 1: v for k, v in THEME_DICTION.items()}
THEME_REVERSE = {k + 1: v for k, v in THEME_REVERSE.items()}
THEME_FAMILY = {k + 1: v for k, v in THEME_FAMILY.items()}


def _ensure_proxy() -> None:
    # SECTOR_PROXY 开关（供无本地代理环境，如 grokbot 云机）：
    #   off/none/0/false/no/""  → 完全不设代理（依赖直连/环境自带代理）
    #   http://host:port        → 使用指定代理
    #   未设置                   → 默认 127.0.0.1:7890（作者本机）
    #   已设 _PROXY_ENV_KEYS 任一 → 以环境既有代理为准，不覆盖
    sp = os.environ.get("SECTOR_PROXY")
    OFF = ("off", "none", "0", "false", "no", "")
    if sp is not None:
        if sp.strip().lower() in OFF:
            return
        proxy = sp.strip()
    elif any(os.environ.get(k) for k in _PROXY_ENV_KEYS):
        return
    else:
        proxy = DEFAULT_PROXY
    try:
        os.environ.setdefault("HTTPS_PROXY", proxy)
        os.environ.setdefault("HTTP_PROXY", proxy)
    except Exception:
        pass


def _download_close(ticker: str, period: str = "2y", retries: int = 3) -> Optional[pd.Series]:
    try:
        import yfinance as yf
    except Exception as exc:
        logger.warning("yfinance import failed: %s", exc)
        return None
    last_err: Any = None
    for attempt in range(1, retries + 1):
        try:
            _ensure_proxy()
            raw = yf.download(ticker, period=period, auto_adjust=True, progress=False, threads=False)
        except Exception as exc:  # transient network / rate-limit
            last_err = exc
            logger.warning("download %s attempt %d/%d failed: %s", ticker, attempt, retries, exc)
            if attempt < retries:
                time.sleep(2 * attempt)
            continue
        if raw is None or getattr(raw, "empty", True):
            last_err = "empty payload"
            logger.warning("download %s attempt %d/%d returned empty", ticker, attempt, retries)
            if attempt < retries:
                time.sleep(2 * attempt)
            continue
        try:
            cols = raw.columns
            if getattr(cols, "nlevels", 1) > 1:
                # yfinance 1.x: MultiIndex (field=level0, ticker=level1)
                if ("Close", ticker) in cols:
                    close = raw[("Close", ticker)]
                elif (ticker, "Close") in cols:
                    close = raw[(ticker, "Close")]
                elif "Close" in cols.get_level_values(0):
                    cd = raw.xs("Close", axis=1, level=0)
                    close = cd[ticker] if ticker in cd.columns else cd.iloc[:, 0]
                else:
                    close = None
            else:
                close = raw["Close"] if "Close" in cols else (raw[ticker] if ticker in cols else None)
            if close is None:
                last_err = "no Close column"
                if attempt < retries:
                    time.sleep(2 * attempt)
                continue
            s = pd.to_numeric(close, errors="coerce").dropna()
            if len(s) < 30:
                last_err = f"too short ({len(s)})"
                if attempt < retries:
                    time.sleep(2 * attempt)
                continue
            return s
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("parse %s attempt %d/%d failed: %s", ticker, attempt, retries, exc)
            if attempt < retries:
                time.sleep(2 * attempt)
            continue
    if last_err is not None:
        logger.warning("download %s all %d attempts failed: %s", ticker, retries, last_err)
    return None


def _read_cache(ticker: str, max_age_seconds: int):
    """返回 (series, last_date)，仅当缓存文件在 max_age_seconds 之内。

    调用方用 last_date 判断缓存是否已覆盖「最新已完成美股会话」——而非仅看
    文件 mtime。否则会出现「今天写的文件、但数据是 T-2」被当成新鲜而永不刷新。
    """
    p = CACHE_DIR / f"{ticker}.csv"
    if not p.exists():
        return None
    try:
        if dt.datetime.now().timestamp() - p.stat().st_mtime > max_age_seconds:
            return None
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        if "Close" not in df.columns:
            return None
        s = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if len(s) < 30:
            return None
        last_date = pd.Timestamp(s.index[-1]).date()
        return s, last_date
    except Exception:
        return None


def _write_cache(ticker: str, s: pd.Series) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"Close": s}).to_csv(CACHE_DIR / f"{ticker}.csv")
    except Exception as exc:
        logger.warning("cache write %s failed: %s", ticker, exc)


def load_closes(
    force_refresh: bool = False,
    stale_cache_max_age: int = 3 * 86400,
) -> Dict[str, pd.Series]:
    """抓取收盘价。

    健壮性策略（防止盘前自动化因 yfinance 瞬态失败而整体缺失主题腿）：
      1) 优先用覆盖「最新已完成美股会话」的鲜活缓存（按数据日期判定，非文件 mtime）；
      2) 否则实时抓取（含重试）；
      3) 实时仍失败 → 用 <=3天 的陈旧缓存兜底（总比整腿 missing 好）；
      4) 都失败 → 该品种缺失，记 warning。
    """
    closes: Dict[str, pd.Series] = {}
    completed = last_completed_equity_session()
    for key, yf_tkr, _, label in SYMBOLS:
        s = None
        if not force_refresh:
            cached = _read_cache(yf_tkr, max_age_seconds=86400)
            if cached is not None:
                cs, cdate = cached
                # 缓存须覆盖最新已完成美股会话才算新鲜；否则仍去实时抓取。
                # 修复：旧逻辑只看文件 mtime(<1天)，会让「今天写但数据是 T-2」的
                # 缓存被当成新鲜而永远不刷新，导致盘前自动化卡在 T-2、主题腿缺失。
                if cdate >= completed:
                    s = cs
        if s is None:
            s = _download_close(yf_tkr)
            if s is not None:
                _write_cache(yf_tkr, s)
        if s is None and not force_refresh:
            # 实时抓取失败 → 陈旧缓存兜底（<=3天），避免整腿 missing
            cached = _read_cache(yf_tkr, max_age_seconds=stale_cache_max_age)
            if cached is not None:
                s = cached[0]
                logger.warning("实时抓取失败，使用陈旧缓存(<=3天)兜底: %s (%s)", label, yf_tkr)
        if s is None:
            logger.warning("无数据: %s (%s)", label, yf_tkr)
        else:
            closes[key] = s
            logger.info("已获取 %s (%s): %d 根", label, yf_tkr, len(s))
    if len(closes) < len(SYMBOLS):
        logger.warning("部分品种缺失数据（%d/%d），结果可能偏差", len(closes), len(SYMBOLS))
    return closes


def u1(close: pd.Series, k1: int = 5, k2: int = 200) -> pd.Series:
    r = close / close.shift(k1) - 1.0
    m = r.rolling(k2).mean()
    s = r.rolling(k2).std()
    out = (r - m) / s
    return out.where(s > 0, 0.0)


def u2(x: pd.Series, k2: int = 200) -> pd.Series:
    m = x.rolling(k2).mean()
    s = x.rolling(k2).std()
    out = (x - m) / s
    return out.where(s > 0, 0.0)


def _cont(b_series: pd.Series) -> int:
    """连续成立根数（从最新往回数）。"""
    s = b_series.iloc[::-1].reset_index(drop=True)
    c = 0
    for v in s:
        if bool(v):
            c += 1
        else:
            break
    return c


def persist_count_from_h(b_series: pd.Series) -> int:
    """已持续根数：对点亮序列 h（近3根>=2）做连续计数，对齐面板 g 语义。"""
    h = b_series.rolling(3).sum() >= 2
    return _cont(h.fillna(False))


def theme_risk_bias(theme_id: int) -> str:
    if theme_id is None or theme_id < 0:
        return "none"
    return THEME_RISK_BIAS.get(int(theme_id), "mixed")


def theme_pressure_override(theme_id: int) -> bool:
    return int(theme_id) in PRESSURE_THEME_IDS if theme_id is not None and theme_id >= 0 else False


def theme_pressure_level(theme_id: int) -> int:
    """Map a dominant theme id to the kernel's cross-asset pressure level (0..3).

    3 = pressure override (themes 9/10), 2 = risk_off, 1 = mixed, 0 = none/risk_on.
    Mirrors the AND-gate calibration (scripts/backtest_theme_andgate.py) so the
    decision kernel receives a single integer it can gate on. The kernel's AND-gate
    only bites when level>=2 AND (structural weak OR macro subopt).
    """
    if theme_pressure_override(theme_id):
        return 3
    bias = theme_risk_bias(theme_id)
    if bias == "risk_off":
        return 2
    if bias == "mixed":
        return 1
    return 0


def theme_booleans_scalar(x: Dict[str, Any]) -> Dict[int, bool]:
    """单日标量布尔（黄金样例/单测用）。x 含 x01..x14 与派生 x18/x21/x24/x25/x29。"""
    z = x
    x18 = float(z.get("x18", 0.0))
    x21 = float(z.get("x21", 0.0))
    x24 = float(z.get("x24", 0.0))
    x25 = float(z.get("x25", 0.0))
    x29 = bool(z.get("x29", False))
    b: Dict[int, bool] = {}
    b[1] = (x21 > 0.5) and ((float(z["x01"]) > 0.5) or (float(z["x02"]) > 0.5))
    b[2] = (x18 > 0.5) and (float(z["x09"]) > 0.5) and (float(z["x02"]) > 0.5)
    b[3] = (x18 > 0.75) and (float(z["x09"]) < 0.5)
    b[4] = (float(z["x03"]) > 0.5) and (float(z["x03"]) - float(z["x01"]) > 0.25)
    b[5] = (float(z["x03"]) > 0.5) and (float(z["x05"]) < -0.5) and (float(z["x08"]) > 0.5)
    b[6] = (float(z["x01"]) < -0.5) and (float(z["x01"]) < float(z["x03"])) and (x24 > -0.5)
    b[7] = (float(z["x02"]) < -0.5) and (x24 < -0.5) and (float(z["x09"]) < -0.5)
    b[8] = (float(z["x02"]) < -0.5) and (x24 < -0.5) and (float(z["x08"]) > 0.5) and (float(z["x09"]) > 0.5)
    b[9] = (float(z["x05"]) > 1.0) and (x21 > 0.5) and (float(z["x08"]) < -0.5) and (x24 < -0.5)
    b[10] = (float(z["x07"]) > 1.0) and (float(z["x12"]) < -0.5) and (float(z["x12"]) < float(z["x11"]))
    b[11] = x24 > 0.5
    b[12] = (float(z["x12"]) > 0.5) and (x25 > 0.5)
    b[13] = (float(z["x12"]) < -0.5) and ((float(z["x11"]) > -0.25) or (float(z["x13"]) > -0.25))
    b[14] = ((x21 > 0.5) or (float(z["x03"]) > 0.5)) and (
        ((float(z["x12"]) >= -0.25) and (float(z["x14"]) > 0))
        or ((float(z["x12"]) > 0) and (float(z["x11"]) >= -0.25))
    )
    b[15] = (float(z["x11"]) < -0.5) and (float(z["x12"]) < -0.5) and (float(z["x14"]) < -0.5)
    b[16] = x29
    b[17] = (
        abs(float(z["x14"])) > 1.5
        and abs(x24) < 0.5
        and abs(float(z["x05"])) < 0.5
        and abs(float(z["x02"])) < 0.5
    )
    return b


def select_dominant(h: Dict[int, bool], n: Dict[int, int], g: Dict[int, int]) -> int:
    """主导主题：h09/h10 置顶；否则旁证优先，其次已持续根数，再次更小 id。"""
    if h.get(9):
        return 9
    if h.get(10):
        return 10
    best_key = None
    y1 = -1
    for i in range(1, 18):
        if not h.get(i):
            continue
        key = (int(n.get(i, 0)), int(g.get(i, 0)), -i)
        if best_key is None or key > best_key:
            best_key = key
            y1 = i
    return y1


def last_completed_equity_session(now: Optional[dt.datetime] = None) -> dt.date:
    """美股日线已完成交易日（America/New_York，16:00 收盘）。"""
    if now is None:
        now = dt.datetime.now(tz=ZoneInfo("America/New_York"))
    elif now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("America/New_York"))
    else:
        now = now.astimezone(ZoneInfo("America/New_York"))
    d = now.date()
    session_complete_today = (now.hour, now.minute) >= (16, 0)

    def _prev_bday(day: dt.date) -> dt.date:
        d0 = day
        while d0.weekday() >= 5:
            d0 -= dt.timedelta(days=1)
        return d0

    if d.weekday() >= 5:
        return _prev_bday(d)
    if session_complete_today:
        return d
    prev = d - dt.timedelta(days=1)
    return _prev_bday(prev)


def assess_freshness(
    as_of: dt.date,
    expected_as_of: Optional[dt.date] = None,
    stale_after_days: int = STALE_AFTER_BD,
) -> Dict[str, Any]:
    if expected_as_of is None:
        expected_as_of = as_of
    try:
        lag = int(np.busday_count(as_of, expected_as_of))
    except Exception:
        lag = (expected_as_of - as_of).days
    lag = max(0, lag)
    stale = lag > stale_after_days
    return {
        "as_of": as_of.isoformat(),
        "expected_as_of": expected_as_of.isoformat(),
        "lag_days": lag,
        "stale": stale,
        "stale_after_days": stale_after_days,
    }


def align_closes(
    closes: Dict[str, pd.Series],
    as_of: Optional[dt.date] = None,
    ffill_limit: int = FFILL_LIMIT,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """以 SPY 交易日为锚对齐；有限 ffill；不把周末 BTC 延长美股末日。"""
    missing = [k for k, *_ in SYMBOLS if k not in closes or closes[k] is None or len(closes[k]) == 0]
    present = {k: v.copy() for k, v in closes.items() if v is not None and len(v)}
    for k, s in list(present.items()):
        idx = pd.to_datetime(s.index).tz_localize(None)
        s = pd.Series(pd.to_numeric(s.values, errors="coerce"), index=idx).sort_index()
        s = s[~s.index.duplicated(keep="last")].dropna()
        present[k] = s

    if ANCHOR_KEY not in present:
        df = pd.DataFrame(present).sort_index()
        anchor_index = df.dropna(how="all").index
    else:
        anchor_index = present[ANCHOR_KEY].index

    if as_of is not None:
        as_of_ts = pd.Timestamp(as_of)
        anchor_index = anchor_index[anchor_index <= as_of_ts]

    df = pd.DataFrame(index=anchor_index)
    for k, s in present.items():
        df[k] = s.reindex(anchor_index)
    df = df.ffill(limit=ffill_limit)

    still_missing_cols = [k for k, *_ in SYMBOLS if k not in df.columns]
    nan_cols = [c for c in df.columns if df[c].isna().all()]
    missing_symbols = sorted(set(missing) | set(still_missing_cols) | set(nan_cols))
    critical_missing = []
    for k in CRITICAL_KEYS:
        if k in missing_symbols or k not in df.columns:
            critical_missing.append(k)
        elif k in df.columns and df[k].isna().sum() > max(5, int(len(df) * 0.05)):
            critical_missing.append(k)
    if ANCHOR_KEY in df.columns:
        df = df[df[ANCHOR_KEY].notna()]
    meta = {
        "missing_symbols": missing_symbols,
        "critical_missing": critical_missing,
        "degraded": len(critical_missing) > 0,
        "anchor": ANCHOR_KEY,
        "rows": int(len(df)),
    }
    return df, meta


def compute_state(
    closes: Dict[str, pd.Series],
    as_of: Optional[dt.date] = None,
    now: Optional[dt.datetime] = None,
    expected_as_of: Optional[dt.date] = None,
    prefer_latest: bool = True,
) -> Dict:
    # Default: latest available anchor (SPY) bar.
    # Intraday incomplete bars are kept; only flagged via quality.incomplete_bar.
    # prefer_latest=False restores cut-to-last-completed-session behavior.
    completed = last_completed_equity_session(now)
    df_probe, _ = align_closes(closes, as_of=None)
    latest_avail = None
    if len(df_probe):
        latest_avail = pd.Timestamp(df_probe.index[-1]).date()

    if as_of is None:
        if prefer_latest and latest_avail is not None:
            as_of = latest_avail
        else:
            as_of = completed
    elif (not prefer_latest) and as_of > completed:
        as_of = completed

    df, align_meta = align_closes(closes, as_of=as_of)
    missing_symbols = list(align_meta["missing_symbols"])
    critical_missing = list(align_meta["critical_missing"])
    degraded = bool(align_meta["degraded"])

    for key, _, _, _ in SYMBOLS:
        if key not in df.columns:
            df[key] = np.nan
            if key not in missing_symbols:
                missing_symbols.append(key)

    if len(df) < 210:
        raise RuntimeError(f"历史数据不足: {len(df)} 根 (<210)，无法算 200 日基线")

    z: Dict[str, pd.Series] = {}
    for key, _, kind, _ in SYMBOLS:
        series = df[key].astype(float)
        u = u1(series)
        if kind in ("rate", "pos"):
            z[key] = u
        else:
            z[key] = -u
        z[key] = z[key].fillna(0.0)

    x15 = z["x02"] - z["x04"]
    x16 = u2(x15)
    x17 = u2(0.7 * z["x09"] + 0.3 * z["x10"])
    x18 = x16
    x21 = z["x04"]
    x23 = ((z["x05"] > 0.5) & (z["x06"] < -0.25)) | ((z["x05"] < -0.5) & (z["x06"] > 0.25))
    x24 = (z["x11"] + z["x12"] + z["x13"]) / 3.0
    x25 = z["x12"] - z["x11"]
    x26 = z["x13"] - z["x11"]
    x27 = (z["x08"] + z["x07"]) / 2.0
    x28 = ((z["x08"] < -0.5) & (x21 > 0.5)) | ((z["x08"] > 0.5) & (x21 < -0.5))
    x29 = (z["x08"] > 0.5) & (x21 > 0.5)

    b: Dict[int, pd.Series] = {}
    b[1] = (x21 > 0.5) & ((z["x01"] > 0.5) | (z["x02"] > 0.5))
    b[2] = (x18 > 0.5) & (z["x09"] > 0.5) & (z["x02"] > 0.5)
    b[3] = (x18 > 0.75) & (z["x09"] < 0.5)
    b[4] = (z["x03"] > 0.5) & (z["x03"] - z["x01"] > 0.25)
    b[5] = (z["x03"] > 0.5) & (z["x05"] < -0.5) & (z["x08"] > 0.5)
    b[6] = (z["x01"] < -0.5) & (z["x01"] < z["x03"]) & (x24 > -0.5)
    b[7] = (z["x02"] < -0.5) & (x24 < -0.5) & (z["x09"] < -0.5)
    b[8] = (z["x02"] < -0.5) & (x24 < -0.5) & (z["x08"] > 0.5) & (z["x09"] > 0.5)
    b[9] = (z["x05"] > 1.0) & (x21 > 0.5) & (z["x08"] < -0.5) & (x24 < -0.5)
    b[10] = (z["x07"] > 1.0) & (z["x12"] < -0.5) & (z["x12"] < z["x11"])
    b[11] = (x24 > 0.5)
    b[12] = (z["x12"] > 0.5) & (x25 > 0.5)
    b[13] = (z["x12"] < -0.5) & ((z["x11"] > -0.25) | (z["x13"] > -0.25))
    b[14] = ((x21 > 0.5) | (z["x03"] > 0.5)) & (
        ((z["x12"] >= -0.25) & (z["x14"] > 0)) | ((z["x12"] > 0) & (z["x11"] >= -0.25)))
    b[15] = (z["x11"] < -0.5) & (z["x12"] < -0.5) & (z["x14"] < -0.5)
    b[16] = x29
    b[17] = (z["x14"].abs() > 1.5) & (x24.abs() < 0.5) & (z["x05"].abs() < 0.5) & (z["x02"].abs() < 0.5)

    n: Dict[int, pd.Series] = {}
    n[1] = (z["x08"] < 0).astype(int) + (z["x05"] > 0.5).astype(int) + (x18 < 0.5).astype(int)
    n[2] = (z["x10"] > 0.5).astype(int) + (z["x08"] > -0.5).astype(int) + (z["x03"] > 0.5).astype(int)
    n[3] = (z["x10"] < 0.5).astype(int) + (z["x02"] > 0).astype(int) + (z["x01"] > 0.5).astype(int)
    n[4] = (z["x08"] > -0.25).astype(int) + (z["x05"].abs() < 0.5).astype(int) + (x18 < 0.5).astype(int)
    n[5] = (z["x06"] > 0.5).astype(int) + (x24 < 0).astype(int) + (z["x03"] > z["x01"]).astype(int)
    n[6] = (x24 > 0.5).astype(int) + (z["x08"] > 0).astype(int) + (z["x05"] < 0).astype(int)
    n[7] = (z["x10"] < -0.5).astype(int) + (z["x01"] < -0.5).astype(int) + (z["x14"] < 0).astype(int)
    n[8] = (z["x07"] > 0.5).astype(int) + (z["x03"] < z["x01"]).astype(int) + (z["x14"] < 0).astype(int)
    n[9] = (z["x14"] < -0.5).astype(int) + (z["x09"] < 0).astype(int) + (z["x06"] < -0.5).astype(int)
    n[10] = (z["x14"] < -0.5).astype(int) + (z["x08"] < 0.25).astype(int) + (x24 < -0.5).astype(int)
    n[11] = (z["x10"] > 0.5).astype(int) + (z["x14"] > 0.5).astype(int) + (z["x05"] < 0).astype(int)
    n[12] = (z["x11"] < 0.5).astype(int) + (z["x13"] < 0.25).astype(int) + (z["x14"] > 0).astype(int)
    n[13] = (z["x13"] > 0.25).astype(int) + (x26 > 0.5).astype(int) + (x24 > -0.25).astype(int)
    n[14] = (z["x13"] > -0.25).astype(int) + (z["x10"] > -0.25).astype(int) + (x24 > 0).astype(int)
    n[15] = (z["x13"] < -0.5).astype(int) + (z["x10"] < -0.5).astype(int) + (x27 > 0.5).astype(int)
    n[16] = (z["x05"] > 0).astype(int) + (z["x03"] > 0.5).astype(int) + (z["x09"] < 0.5).astype(int)
    n[17] = (z["x08"].abs() < 0.5).astype(int) + (z["x09"].abs() < 0.5).astype(int) + (x21.abs() < 0.5).astype(int)

    idx = -1
    as_of_date = df.index[idx].date()
    if isinstance(as_of_date, pd.Timestamp):
        as_of_date = as_of_date.date()

    xvals = {
        "x01": float(z["x01"].iloc[idx]), "x02": float(z["x02"].iloc[idx]),
        "x03": float(z["x03"].iloc[idx]), "x04": float(z["x04"].iloc[idx]),
        "x05": float(z["x05"].iloc[idx]), "x06": float(z["x06"].iloc[idx]),
        "x07": float(z["x07"].iloc[idx]), "x08": float(z["x08"].iloc[idx]),
        "x09": float(z["x09"].iloc[idx]), "x10": float(z["x10"].iloc[idx]),
        "x11": float(z["x11"].iloc[idx]), "x12": float(z["x12"].iloc[idx]),
        "x13": float(z["x13"].iloc[idx]), "x14": float(z["x14"].iloc[idx]),
        "x15": float(x15.iloc[idx]), "x16": float(x16.iloc[idx]),
        "x17": float(x17.iloc[idx]), "x18": float(x18.iloc[idx]),
        "x21": float(x21.iloc[idx]), "x23": bool(x23.iloc[idx]),
        "x24": float(x24.iloc[idx]), "x25": float(x25.iloc[idx]),
        "x26": float(x26.iloc[idx]), "x27": float(x27.iloc[idx]),
        "x28": bool(x28.iloc[idx]), "x29": bool(x29.iloc[idx]),
    }

    # 1-day simple returns on aligned closes (display only; not used in theme rules)
    # For inv/neg symbols, still report raw price 1D% (not sign-flipped).
    ret1d: Dict[str, float] = {}
    for key, _, _, _ in SYMBOLS:
        s = df[key].astype(float)
        if len(s) >= 2 and pd.notna(s.iloc[idx]) and pd.notna(s.iloc[idx - 1]) and float(s.iloc[idx - 1]) != 0.0:
            ret1d[key] = float(s.iloc[idx] / s.iloc[idx - 1] - 1.0)
        else:
            ret1d[key] = float("nan")
    # derived composites: average member 1D%
    def _avg_ret(keys):
        vals = [ret1d[k] for k in keys if k in ret1d and ret1d[k] == ret1d[k]]
        return float(sum(vals) / len(vals)) if vals else float("nan")
    ret1d["x24"] = _avg_ret(["x11", "x12", "x13"])
    ret1d["x27"] = _avg_ret(["x08", "x07"])  # gold + jpy price; jpy is USDJPY raw
    # spread-like derived: difference of members' 1D%
    def _diff_ret(a, b):
        if a in ret1d and b in ret1d and ret1d[a] == ret1d[a] and ret1d[b] == ret1d[b]:
            return float(ret1d[a] - ret1d[b])
        return float("nan")
    ret1d["x25"] = _diff_ret("x12", "x11")
    ret1d["x26"] = _diff_ret("x13", "x11")
    ret1d["x17"] = (
        float(0.7 * ret1d["x09"] + 0.3 * ret1d["x10"])
        if ret1d.get("x09") == ret1d.get("x09") and ret1d.get("x10") == ret1d.get("x10")
        else float("nan")
    )
    h = {i: bool(b[i].rolling(3).sum().iloc[idx] >= 2) for i in range(1, 18)}
    g = {i: persist_count_from_h(b[i]) for i in range(1, 18)}
    nvals = {i: int(n[i].iloc[idx]) for i in range(1, 18)}
    bvals = {i: bool(b[i].iloc[idx]) for i in range(1, 18)}

    y1 = select_dominant(h, nvals, g)

    if expected_as_of is None:
        # freshness vs chosen as_of itself (not wall-clock completed session)
        expected_as_of = as_of_date
    fresh = assess_freshness(as_of_date, expected_as_of=expected_as_of)
    stale = bool(fresh["stale"])
    incomplete_bar = bool(as_of_date > completed)
    if degraded and stale:
        dq = "stale_degraded"
    elif degraded:
        dq = "degraded"
    elif stale:
        dq = "stale"
    else:
        dq = "ok"

    quality = {
        **fresh,
        "degraded": degraded,
        "missing_symbols": missing_symbols,
        "critical_missing": critical_missing,
        "data_quality": dq,
        "completed_session": completed.isoformat(),
        "latest_available": None if latest_avail is None else latest_avail.isoformat(),
        "incomplete_bar": incomplete_bar,
        "prefer_latest": bool(prefer_latest),
        "anchor": ANCHOR_KEY,
    }

    return {
        "date": as_of_date.isoformat(),
        "x": xvals, "h": h, "g": g, "n": nvals, "b": bvals, "y1": y1,
        "ret1d": ret1d,
        "quality": quality,
        "risk_bias": theme_risk_bias(y1),
        "pressure_override": theme_pressure_override(y1),
        "theme_pressure_level": theme_pressure_level(y1),
        "z_series": z, "_derived": {"x23": x23, "x24": x24, "x25": x25, "x26": x26,
                                     "x27": x27, "x28": x28, "x29": x29, "x17": x17},
    }


def u3(z: float) -> str:
    a = abs(z)
    if a < 0.5:
        return "噪音"
    if a < 1.0:
        return "有意义"
    if a < 1.5:
        return "强"
    return "异常"


def u5(z: float) -> str:
    if z > 0.15:
        return "↑"
    if z < -0.15:
        return "↓"
    return "→"


def fmt_ret1d(r: float) -> str:
    """Format 1-day return as percent for strength table."""
    try:
        if r is None or r != r:  # NaN
            return "—"
        return f"{r * 100:+.2f}%"
    except Exception:
        return "—"


def build_report(state: Dict) -> str:
    x = state["x"]
    y1 = state["y1"]
    date = state["date"]
    q = state.get("quality") or {}

    if y1 < 0:
        theme_line = "无主导主题 · 未形成共振"
        dict_line = "各品种没有形成一致方向 —— 等待共振, 别硬编故事"
        reverse_line = "—"
        conf_line = "—"
        bias_line = "none"
        pressure_line = "否"
        tpl = 0
        tpl_label = "None（无压力）"
    else:
        theme_line = f"{THEME_NAMES[y1]}  〔{FAMILY_NAMES[THEME_FAMILY[y1]]}〕"
        dict_line = THEME_DICTION[y1]
        reverse_line = THEME_REVERSE[y1]
        conf_line = f"旁证 {state['n'][y1]}/3 · 已持续 {state['g'][y1]} 根K线"
        bias_line = state.get("risk_bias") or theme_risk_bias(y1)
        pressure_line = "是" if state.get("pressure_override") or theme_pressure_override(y1) else "否"
        tpl = int(state.get("theme_pressure_level", theme_pressure_level(y1)) or 0)
        tpl_label = {0: "None（无压力）", 1: "Mixed（混合）", 2: "RiskOff（风险规避）", 3: "PressureOverride（压力置顶）"}.get(tpl, str(tpl))

    pairs = sorted(zip(LEADER_KEYS, LEADER_LABELS, [x[k] for k in LEADER_KEYS]),
                   key=lambda t: abs(t[2]), reverse=True)[:3]
    # z=5日动量异常度（非当日涨跌）；同时带出当日 1D% 避免「↑」被误读为今天上涨。
    _r1_lead = state.get("ret1d") or {}
    leaders_txt = "   ".join(
        f"{i+1}) {lbl} z{v:+.1f}{u5(v)}（当日{fmt_ret1d(_r1_lead.get(k))}）"
        for i, (k, lbl, v) in enumerate(pairs))

    fam_lines = []
    for fam in range(5):
        cand = [i for i in range(1, 18) if THEME_FAMILY[i] == fam and state["h"][i]]
        if not cand:
            fam_lines.append(f"- **{FAMILY_NAMES[fam]}**: —")
        else:
            bi = max(cand, key=lambda i: (state["n"][i], state["g"][i], -i))
            fam_lines.append(
                f"- **{FAMILY_NAMES[fam]}**: {THEME_NAMES[bi]}  "
                f"旁证{state['n'][bi]}/3 · 已持续{state['g'][bi]}根")

    r1 = state.get("ret1d") or {}
    strength_rows = []
    for key in ["x01", "x02", "x03", "x04", "x05", "x06", "x07", "x08",
                "x09", "x10", "x11", "x12", "x13", "x14"]:
        lbl = SYMBOL_LABELS[key]
        v = x[key]
        strength_rows.append(
            f"| {lbl} | {u5(v)} {v:+.2f} | {u3(v)} | {fmt_ret1d(r1.get(key))} |"
        )
    for key, lbl in [
        ("x24", DERIVED_LABELS["x24"]),
        ("x27", DERIVED_LABELS["x27"]),
        ("x25", DERIVED_LABELS["x25"]),
        ("x26", DERIVED_LABELS["x26"]),
        ("x17", DERIVED_LABELS["x17"]),
    ]:
        strength_rows.append(
            f"| {lbl} | {u5(x[key])} {x[key]:+.2f} | {u3(x[key])} | {fmt_ret1d(r1.get(key))} |"
        )

    gold_behavior = ("异常: 无视真实利率上涨" if x["x29"] else
                     "正常: 与真实利率反向" if x["x28"] else "中性: 无额外信息")

    dq = q.get("data_quality", "ok")
    miss = q.get("missing_symbols") or []
    crit = q.get("critical_missing") or []
    quality_lines = [
        f"- **数据质量**: `{dq}`",
        f"- **as_of / 期望**: {q.get('as_of', date)} / {q.get('expected_as_of', '—')}"
        f" ｜ lag={q.get('lag_days', 0)} 交易日"
        f"{' ｜ ⚠ STALE' if q.get('stale') else ''}"
        f"{' ｜ ⚠ DEGRADED' if q.get('degraded') else ''}"
        f"{' ｜ ⚠ INCOMPLETE_BAR(盘中未收盘)' if q.get('incomplete_bar') else ''}",
        f"- **已完成会话 / 最新可用**: {q.get('completed_session', '—')} / {q.get('latest_available', q.get('as_of', date))}",
    ]
    if miss:
        quality_lines.append(f"- **缺失品种**: {', '.join(miss)}")
    if crit:
        quality_lines.append(f"- **关键缺失**: {', '.join(crit)}")

    lines = [
        f"# 市场主题状态机（日频复刻）| {date}",
        "",
        f"- **市场主题**: {theme_line}",
        f"- **白话解读**: {dict_line}",
        f"- **确信度**: {conf_line}",
        f"- **推翻信号**: {reverse_line}",
        f"- **风险偏向 risk_bias**: {bias_line}",
        f"- **压力置顶 pressure_override**: {pressure_line}",
        f"- **主题压力层级 theme_pressure_level**: {tpl}（{tpl_label}）",
        "",
        "## 数据质量",
        "",
        *quality_lines,
        "",
        "## 动量主角（5日动量z 前三 · 箭头=z符号，非当日涨跌）",
        "",
        f"> {leaders_txt}",
        "",
        "## 关键仪表",
        "",
        f"- 真实利率方向: {u5(x['x21'])} {x['x21']:+.2f} · {u3(x['x21'])}  [实测]",
        f"- 通胀预期方向: {u5(x['x18'])} {x['x18']:+.2f} · {u3(x['x18'])}  [盘口实测]",
        f"- 美元方向: {u5(x['x05'])} {x['x05']:+.2f} · {u3(x['x05'])}"
        f"{'  欧元已确认' if x['x23'] else '  欧元未确认'}",
        f"- 黄金行为: {gold_behavior} · 黄金强度 {x['x08']:+.2f}",
        f"- 商品复合: {u5(x['x17'])} {x['x17']:+.2f} · {u3(x['x17'])}",
        "",
        "## 五个题材分区",
        "",
        "\n".join(fam_lines),
        "",
        "## 品种强度表",
        "",
        "| 品种 | 方向·强度(z) | 级别 | 1D% |",
        "|---|---|---|---|",
        "\n".join(strength_rows),
        "",
        "---",
        "*数据源: yfinance 日线代理（短端=^IRX≈3M, 10Y=^TNX, 30Y=^TYX, TIP, DXY, GLD, CL=F, HG=F, "
        "SPY/QQQ/RSP, EURUSD=X, JPY=X取反为日元强度, BTC-USD）。*",
        "*复刻 TradingView《市场主题状态机 v3.1r》核心逻辑（14品种 z-score + 17主题 + 三道滤网），"
        "日线路径；日历以 SPY 为锚。强度 z=5日动量异常度；1D%=当日简单涨跌（原始价格，未按主题符号取反）。"
        "本文件为数据参考，非投资建议。*",
        "",
    ]
    return "\n".join(lines)


def build_json(state: Dict) -> Dict:
    x = state["x"]
    y1 = state["y1"]
    pairs = sorted(zip(LEADER_KEYS, LEADER_LABELS, [x[k] for k in LEADER_KEYS]),
                   key=lambda t: abs(t[2]), reverse=True)[:3]
    families = {}
    for fam in range(5):
        cand = [i for i in range(1, 18) if THEME_FAMILY[i] == fam and state["h"][i]]
        families[FAMILY_NAMES[fam]] = None
        if cand:
            bi = max(cand, key=lambda i: (state["n"][i], state["g"][i], -i))
            families[FAMILY_NAMES[fam]] = {
                "theme": THEME_NAMES[bi], "n3": state["n"][bi], "persist_days": state["g"][bi],
                "risk_bias": theme_risk_bias(bi),
            }
    strength = []
    label_map = dict(SYMBOL_LABELS)
    label_map.update(DERIVED_LABELS)
    r1 = state.get("ret1d") or {}
    for key in ["x01", "x02", "x03", "x04", "x05", "x06", "x07", "x08",
                "x09", "x10", "x11", "x12", "x13", "x14", "x24", "x27", "x25", "x26", "x17"]:
        lbl = label_map.get(key, key)
        rr = r1.get(key)
        try:
            rr_out = None if rr is None or rr != rr else round(float(rr), 6)
        except Exception:
            rr_out = None
        strength.append({
            "key": key, "label": lbl, "z": round(x[key], 3),
            "dir": u5(x[key]), "level": u3(x[key]),
            "ret1d": rr_out,
            "ret1d_pct": None if rr_out is None else round(rr_out * 100.0, 2),
        })
    rb = state.get("risk_bias") or theme_risk_bias(y1)
    po = bool(state.get("pressure_override") if "pressure_override" in state
              else theme_pressure_override(y1))
    tpl = int(state.get("theme_pressure_level", theme_pressure_level(y1)) or 0)
    return {
        "date": state["date"],
        "dominant_theme": (None if y1 < 0 else {
            "id": y1, "name": THEME_NAMES[y1], "family": FAMILY_NAMES[THEME_FAMILY[y1]],
            "plain": THEME_DICTION[y1], "reverse": THEME_REVERSE[y1],
            "confidence": {"n3": state["n"][y1], "persist_days": state["g"][y1]},
            "risk_bias": rb,
            "pressure_override": po,
            "theme_pressure_level": tpl,
        }),
        "risk_bias": rb,
        "pressure_override": po,
        "theme_pressure_level": tpl,
        "quality": state.get("quality") or {},
        "x": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in x.items()},
        # 注意：z 是「5日动量异常度」(u1: 5日收益 vs 200日分布)，**不是当日涨跌**；
        # dir 箭头同样只由 z 的符号决定。故额外带出 ret1d_pct，防止下游把「↑」误读为当日上涨。
        "today_leaders": [{"label": lbl, "z": round(v, 3), "dir": u5(v),
                           "basis": "mom5d_z",
                           "ret1d_pct": (
                               None if (r1.get(k) is None or r1.get(k) != r1.get(k))
                               else round(float(r1[k]) * 100.0, 2)
                           )}
                          for k, lbl, v in pairs],
        "families": families,
        "strength_table": strength,
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Daily market theme state machine (Python port)")
    parser.add_argument("--date", default=None,
                        help="as_of date YYYY-MM-DD (default: latest available SPY bar)")
    parser.add_argument("--expected-as-of", default=None,
                        help="freshness benchmark date (default: as_of itself)")
    parser.add_argument("--completed-only", action="store_true",
                        help="cut to last completed NY equity session (old behavior)")
    parser.add_argument("--force-refresh", action="store_true", help="ignore 1d close cache")
    parser.add_argument("--report-dir", default=str(OUTPUT_DIR), help="output directory")
    args = parser.parse_args(argv)

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    as_of = dt.date.fromisoformat(args.date) if args.date else None
    expected = dt.date.fromisoformat(args.expected_as_of) if args.expected_as_of else None

    closes = load_closes(force_refresh=args.force_refresh)
    if not closes:
        print("无任何品种数据，退出。")
        return 1
    try:
        state = compute_state(
            closes,
            as_of=as_of,
            expected_as_of=expected,
            prefer_latest=not args.completed_only,
        )
    except RuntimeError as exc:
        print(f"{exc}")
        return 1

    md = build_report(state)
    payload = build_json(state)
    date = state["date"]
    out_md = report_dir / f"theme_state_machine_{date}.md"
    out_json = report_dir / f"theme_state_machine_{date}.json"
    out_md.write_text(md, encoding="utf-8")
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(md)
    print(f"\n[written] {out_md}")
    print(f"[written] {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
