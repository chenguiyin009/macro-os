# -*- coding: utf-8 -*-
"""用户 2026-Q4 宏观观点 × 市场数据对照 —— 规则 v2（2026-11-14）。

用户口径（2026-11-14）：「q4不能反推，要根据实际数据来判断」。
v1 是由 10-29 / 11-12 手写判读反推的机械规则，已整体废弃。v2 每条阈值都从
「这条观点若成立，市场数据应当呈现什么」推导，阈值用资产自身波动/政策单位定，
不对照、不拟合任何历史手写结论。

仍为观察：不进四腿合成 min、不改任何腿分数、不构成交易信号。

观点原文（记忆 2026-10-21 基础观点 + 2026-10-23 并入的本杰明乌萨奇信用/流动性透镜）：
  a 利率路径：Fed 2026 再加一次、2027 两次（低信心）、2028 降两次（高信心）；
    债券少量配置做避险与降息交易，偏 2–5 年约 5% 久期，长债暂避、等更深跌再进。
  b EURUSD、欧债、能源可在 Q4 或 2027 年初交易。
  c 现金贵、美元强、流动性回撤频繁；做指数优于个股（防年末至 2027Q1 流动性事件）。
  d 大宗不拖：大宗商品交易不再延后（Q4 内走出来）。
  e 信用看斜率：银行信用增速放缓（H.8 同比斜率向下）。
  f 三重流动性利空诊断：美债收益率下行 + 美元不再加速上冲 + 黄金领跌股指。

判定词：支持 / 未定 / 证伪；任何输入缺失 → 「数据缺」，不硬判。
窗口：趋势类观点（a b c d）用近 20 个交易日（≈1 个月、约一个 FOMC 间隔）；
      e 用 13 周（一个季度，与文章「Q1→Q2→Q3」的季度斜率同口径）；
      f 是冲击诊断，按文章用近 5 个交易日。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

RULES_VERSION = "q4_view_rules_v2"
W_TREND = 20      # 交易日
W_SHOCK = 5       # 交易日
W_CREDIT = 13     # 周
B_DEADLINE = "2027-03-31"  # b 观点窗口「Q4 或明年初」的末端，此前不判证伪

MISSING = "数据缺"

# ---- 阈值（带经济理由，见 THRESHOLD_NOTES）----
TH = {
    # a1：剩余 2026（12 月 FOMC）一次 + 2027 两次，未来 12 个月覆盖其中约 2–3 次，每次 25bp
    "a1_fed1y_support_bp": 50.0,   # ≥2 次加息被定价
    "a1_fed1y_falsify_bp": 25.0,   # <1 次加息：市场不认「再加息」
    "a1_2y_effr_support_bp": 25.0,  # 2Y 高于 EFFR ≥1 次加息幅度
    "a1_2y_effr_falsify_bp": 0.0,   # 2Y 低于政策利率 = 市场定价降息
    # a2：「约 5%」= ±25bp（一次政策步长）；偏离 50bp 即锁 5% 的前提消失
    "a2_support_pct": 4.75,
    "a2_falsify_pct": 4.50,
    # a3：长债暂避。TLT 久期约 16–17：+3% ≈ 长端 -18bp，远超 2–5Y 与长端一个月的 carry 差
    "a3_tlt_falsify_pct": 3.0,
    "a3_tlt_support_pct": -2.0,
    "a3_30y_support_bp": 15.0,
    # b：「可交易」= 出现≥1 个月度标准差的趋势。EURUSD 年化隐波约 7% → 月 σ≈2%；
    #    Bund 日波约 5bp → 月 σ≈20bp；WTI 年化约 35% → 月 σ≈10%
    "b_eur_abs_pct": 2.0,
    "b_bund_abs_bp": 20.0,
    "b_wti_abs_pct": 10.0,
    # c1：现金实际收益 = 3M 国库券 − 10Y 盈亏平衡通胀；中性实际利率约 0.5–1%，≥1.5pp 为限制性
    "c1_real_cash_support_pp": 1.5,
    "c1_real_cash_falsify_pp": 0.5,
    # c2：DXY 年化约 7% → 月 σ≈2%；±1%（半个 σ）作方向确认
    "c2_dxy_pct": 1.0,
    # c3：SPY 年化 15% → 日 σ≈1%；-1.5% 日约 1.5σ，20 日内常态约 1 次，≥2 次即「频繁」
    "c3_drop_day_pct": -1.5,
    "c3_drop_days_support": 2,
    "c3_maxdd_support_pct": -5.0,
    "c3_maxdd_falsify_pct": -3.0,
    # c4：市值加权 vs 等权 20D 差 ±1pp，过滤噪音
    "c4_spy_rsp_pp": 1.0,
    # d：DBC 年化约 15–18% → 月 σ≈4–5%；≥+4% 为大宗行情走出来，≤-4% 为继续拖
    "d_dbc_pct": 4.0,
    # e：H.8 同比 13 周变化 ±0.25pp（周度修订噪音量级；文章季度斜率约 -1pp/季）
    "e_h8_slope_pp": 0.25,
    "e_hy_oas_stress_bp": 50.0,
    # f：10Y 5D ≤ -5bp（≈1 个日 σ）；DXY 5D ≤ +0.5%（未加速）；金 5D ≤ -1% 且弱于 SPY
    "f_10y_bp": -5.0,
    "f_dxy_pct": 0.5,
    "f_gold_pct": -1.0,
}

THRESHOLD_NOTES = {
    "a1": "加息路径：Fed 1y 加息定价 ≥50bp（≥2 次）且 2Y−EFFR ≥+25bp → 支持；1y ≤25bp 或 2Y−EFFR ≤0 → 证伪；其余未定。理由：观点要求未来 12 个月仍有约 2–3 次 25bp 加息，市场若只定价不到 1 次或 2Y 已低于政策利率，即与「再加息」相反。",
    "a2": "2–5Y 约 5%：2Y/5Y 均值 ≥4.75% → 支持（5% 档仍可锁）；<4.50% → 证伪；其间未定。理由：「约 5%」取 ±1 个政策步长 25bp。",
    "a3": "长债暂避：TLT 20D ≤-2% 或 30Y 20D ≥+15bp → 支持（长端仍在跌，回避正确）；TLT 20D ≥+3% → 证伪（回避已明显付出代价）；其余未定。理由：TLT 久期约 16–17，+3% ≈ 长端 -18bp，远超一个月 carry 差。",
    "b": "可交易性：20D 绝对变动 EURUSD ≥2%、Bund10Y ≥20bp、WTI ≥10%（各≈1 个月度 σ）；≥2 条腿达标 → 支持，否则未定；窗口末端 2027-03-31 前不判证伪。",
    "c": "四项：c1 现金实际收益（3M−10Y 盈亏平衡）≥1.5pp 支持 / ≤0.5pp 证伪；c2 DXY 20D ≥+1% 支持 / ≤-1% 证伪；c3 SPY 20 日内 ≤-1.5% 日 ≥2 次或 20D 最大回撤 ≤-5% 支持，0 次且回撤 >-3% 证伪；c4 SPY−RSP 20D ≥+1pp 支持 / ≤-1pp 证伪。合计（支持数−证伪数）≥2 → 支持，≤-2 → 证伪，其余未定。",
    "d": "DBC 20D ≥+4% → 支持；≤-4% → 证伪（继续拖）；其余未定。WTI/铜/金 20D 只列宽度。理由：DBC 月 σ≈4–5%。",
    "e": "H.8 TOTBKCR 同比 vs 13 周前：下降 ≥0.25pp → 支持（斜率放缓）；上升 ≥0.25pp → 证伪；其余未定。HY OAS 20D、CP 周变化只作旁证不改判。",
    "f": "按文章诊断三条件（5D）：10Y ≤-5bp、DXY ≤+0.5%、金 ≤-1% 且弱于 SPY；3/3 → 支持（冲击形态出现），2/3 → 未定，≤1 → 证伪（形态未出现）。",
}


def _ok(*xs) -> bool:
    return all(x is not None for x in xs)


def judge_a1(fed1y_bp: Optional[float], us2y: Optional[float], effr: Optional[float]) -> Dict[str, Any]:
    if not _ok(fed1y_bp, us2y, effr):
        return {"verdict": MISSING}
    spread = (us2y - effr) * 100.0
    if fed1y_bp <= TH["a1_fed1y_falsify_bp"] or spread <= TH["a1_2y_effr_falsify_bp"]:
        v = "证伪"
    elif fed1y_bp >= TH["a1_fed1y_support_bp"] and spread >= TH["a1_2y_effr_support_bp"]:
        v = "支持"
    else:
        v = "未定"
    return {"verdict": v, "us2y_effr_bp": round(spread, 1)}


def judge_a2(us2y: Optional[float], us5y: Optional[float]) -> Dict[str, Any]:
    if not _ok(us2y, us5y):
        return {"verdict": MISSING}
    m = (us2y + us5y) / 2.0
    v = "支持" if m >= TH["a2_support_pct"] else ("证伪" if m < TH["a2_falsify_pct"] else "未定")
    return {"verdict": v, "mean_2y5y": round(m, 3)}


def judge_a3(tlt_20d_pct: Optional[float], us30y_20d_bp: Optional[float]) -> Dict[str, Any]:
    if not _ok(tlt_20d_pct, us30y_20d_bp):
        return {"verdict": MISSING}
    if tlt_20d_pct >= TH["a3_tlt_falsify_pct"]:
        v = "证伪"
    elif tlt_20d_pct <= TH["a3_tlt_support_pct"] or us30y_20d_bp >= TH["a3_30y_support_bp"]:
        v = "支持"
    else:
        v = "未定"
    return {"verdict": v}


def judge_b(eur_20d_pct, bund_20d_bp, wti_20d_pct, as_of: str) -> Dict[str, Any]:
    if not _ok(eur_20d_pct, bund_20d_bp, wti_20d_pct):
        return {"verdict": MISSING}
    legs = {"EURUSD": abs(eur_20d_pct) >= TH["b_eur_abs_pct"],
            "Bund": abs(bund_20d_bp) >= TH["b_bund_abs_bp"],
            "WTI": abs(wti_20d_pct) >= TH["b_wti_abs_pct"]}
    n = sum(legs.values())
    if n >= 2:
        v = "支持"
    elif as_of > B_DEADLINE and n == 0:
        v = "证伪"
    else:
        v = "未定"
    return {"verdict": v, "legs_hit": [k for k, x in legs.items() if x], "n": n}


def max_drawdown_pct(closes: Sequence[float]) -> Optional[float]:
    if not closes:
        return None
    peak, dd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        dd = min(dd, (c / peak - 1.0) * 100.0)
    return dd


def drop_days(closes: Sequence[float], th_pct: float) -> Optional[int]:
    if len(closes) < 2:
        return None
    return sum(1 for a, b in zip(closes[:-1], closes[1:]) if (b / a - 1.0) * 100.0 <= th_pct)


def judge_c(us3m, t10yie, dxy_20d_pct, spy_drop_days, spy_maxdd_pct, spy_20d_pct, rsp_20d_pct) -> Dict[str, Any]:
    subs: Dict[str, str] = {}
    if _ok(us3m, t10yie):
        r = us3m - t10yie
        subs["c1"] = "支持" if r >= TH["c1_real_cash_support_pp"] else ("证伪" if r <= TH["c1_real_cash_falsify_pp"] else "未定")
    else:
        subs["c1"] = MISSING
    if _ok(dxy_20d_pct):
        subs["c2"] = "支持" if dxy_20d_pct >= TH["c2_dxy_pct"] else ("证伪" if dxy_20d_pct <= -TH["c2_dxy_pct"] else "未定")
    else:
        subs["c2"] = MISSING
    if _ok(spy_drop_days, spy_maxdd_pct):
        if spy_drop_days >= TH["c3_drop_days_support"] or spy_maxdd_pct <= TH["c3_maxdd_support_pct"]:
            subs["c3"] = "支持"
        elif spy_drop_days == 0 and spy_maxdd_pct > TH["c3_maxdd_falsify_pct"]:
            subs["c3"] = "证伪"
        else:
            subs["c3"] = "未定"
    else:
        subs["c3"] = MISSING
    if _ok(spy_20d_pct, rsp_20d_pct):
        d = spy_20d_pct - rsp_20d_pct
        subs["c4"] = "支持" if d >= TH["c4_spy_rsp_pp"] else ("证伪" if d <= -TH["c4_spy_rsp_pp"] else "未定")
    else:
        subs["c4"] = MISSING
    if any(x == MISSING for x in subs.values()):
        return {"verdict": MISSING, "subs": subs}
    score = sum(x == "支持" for x in subs.values()) - sum(x == "证伪" for x in subs.values())
    v = "支持" if score >= 2 else ("证伪" if score <= -2 else "未定")
    return {"verdict": v, "subs": subs, "score": score,
            "real_cash_pp": round(us3m - t10yie, 2), "spy_minus_rsp_pp": round(spy_20d_pct - rsp_20d_pct, 2)}


def judge_d(dbc_20d_pct) -> Dict[str, Any]:
    if not _ok(dbc_20d_pct):
        return {"verdict": MISSING}
    v = "支持" if dbc_20d_pct >= TH["d_dbc_pct"] else ("证伪" if dbc_20d_pct <= -TH["d_dbc_pct"] else "未定")
    return {"verdict": v}


def judge_e(h8_yoy_now, h8_yoy_13w) -> Dict[str, Any]:
    if not _ok(h8_yoy_now, h8_yoy_13w):
        return {"verdict": MISSING}
    d = h8_yoy_now - h8_yoy_13w
    v = "支持" if d <= -TH["e_h8_slope_pp"] else ("证伪" if d >= TH["e_h8_slope_pp"] else "未定")
    return {"verdict": v, "slope_pp": round(d, 2)}


def judge_f(us10y_5d_bp, dxy_5d_pct, gold_5d_pct, spy_5d_pct) -> Dict[str, Any]:
    if not _ok(us10y_5d_bp, dxy_5d_pct, gold_5d_pct, spy_5d_pct):
        return {"verdict": MISSING}
    conds = {"10Y下行": us10y_5d_bp <= TH["f_10y_bp"],
             "美元未加速": dxy_5d_pct <= TH["f_dxy_pct"],
             "金领跌": gold_5d_pct <= TH["f_gold_pct"] and gold_5d_pct < spy_5d_pct}
    n = sum(conds.values())
    v = "支持" if n == 3 else ("未定" if n == 2 else "证伪")
    return {"verdict": v, "n": n, "conds": conds}
