# -*- coding: utf-8 -*-
"""
rotation_rank.py — 轻量消融落地的 Top3 排序引擎
================================================
回测 light_ablation_bt + C_enhancement_backtest（2026-09-15）结论：

  1) pine_half_crowd = pine_score - 12.5 * over_ext
     → 标普 Top3 **生产默认**（旗标可切）
  2) mom12 = 12M（≈252 交易日）价格动量 − 可选拥挤软罚
     → 标普研究/可选引擎（SECTOR_TOP3_ENGINE=mom12）；影子回测相对 pine 有超额
  2b) blend = 0.5·z(pine_half) + 0.5·z(mom12) 截面融合
     → 标普研究旗标（SECTOR_TOP3_ENGINE=blend）；P1 回测超额略优于纯 mom12
  3) 科技生产主引擎 = mom6m_S3 裸 6M 价格动量（不做 hard 过热 Top1 帽）
  4) 可选极轻：s3_soft_crowd = mom6m - 0.05 * over_ext（默认开）
  5) 降回撤仍靠分数闸 + Book C，不在此层加重罚

拥挤惩罚（Gemini #2 落地）：
  优先用 rel60 连续罚：pen_pp = min(5, max(0, (rel60-15)/1.5))
  科技 mom：rank = mom6m - pen_pp/100  （上限 5pp）
  标普 pine：adj = score - min(PINE_HALF_CROWD, pen_pp * (12.5/5)) 或 rel60 连续
  回退：无 rel60 时用状态「拥挤」/state_code==3 布尔罚
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional

import numpy as np
import pandas as pd

# 与 daily_allocator 对齐
THEME_ETF = {
    "半导体": "SMH", "软件": "IGV", "云计算": "SKYY",
    "网络安全": "CIBR", "机器人": "BOTZ", "AI综合": "AIQ",
}
SECTOR_ETF = {
    "科技": "XLK", "金融": "XLF", "能源": "XLE", "医疗保健": "XLV",
    "工业": "XLI", "材料": "XLB", "必需消费": "XLP", "公用事业": "XLU",
    "房地产": "XLRE", "可选消费": "XLY", "通信服务": "XLC",
}

PINE_HALF_CROWD = 12.5
S3_SOFT_CROWD_PP = 0.05  # 布尔回退时的 5pp
MOM_LOOKBACK = 126
MOM12_LOOKBACK = 252  # 标普 mom12 引擎（约 12M 交易日）
CROWD_REL60_START = 15.0   # rel60 开始罚
CROWD_REL60_SCALE = 1.5    # 每 1.5pp rel60 → +1 罚分(pine) / 映射到 mom
CROWD_PEN_PP_CAP = 5.0     # 最大 5pp
CROWD_USE_REL60 = True     # 默认连续 rel60


def is_over_ext(row: dict) -> bool:
    st = str(row.get("status") or row.get("state") or "")
    if "拥挤" in st:
        return True
    try:
        if int(row.get("state_code") or 0) == 3:
            return True
    except Exception:
        pass
    return bool(row.get("over_ext"))



def crowd_penalty_pp(rel60: float | None, over_bool: bool = False) -> float:
    """连续拥挤罚（百分点，0~CROWD_PEN_PP_CAP）。

    Gemini: pen = min(5, max(0, (rel60-15)/1.5))
    无 rel60 时：布尔拥挤 → 满 5pp（等价旧 soft/half 量级可再缩放）
    """
    if rel60 is not None:
        try:
            r = float(rel60)
            if r == r:  # not nan
                return float(min(CROWD_PEN_PP_CAP, max(0.0, (r - CROWD_REL60_START) / CROWD_REL60_SCALE)))
        except Exception:
            pass
    return float(CROWD_PEN_PP_CAP) if over_bool else 0.0


def crowd_penalty_mom(rel60: float | None, over_bool: bool = False) -> float:
    """mom 小数空间惩罚：0~S3_SOFT_CROWD_PP（默认 0.05）。"""
    pp = crowd_penalty_pp(rel60, over_bool)
    # map 0..5 pp → 0..0.05
    return float(S3_SOFT_CROWD_PP) * (pp / CROWD_PEN_PP_CAP) if CROWD_PEN_PP_CAP > 0 else 0.0


def crowd_penalty_pine(rel60: float | None, over_bool: bool = False, half: float = PINE_HALF_CROWD) -> float:
    """pine 分空间惩罚：0~half（默认 12.5），与 rel60 连续或布尔满罚。"""
    pp = crowd_penalty_pp(rel60, over_bool)
    return float(half) * (pp / CROWD_PEN_PP_CAP) if CROWD_PEN_PP_CAP > 0 else 0.0


def pine_half_crowd_score(score: float, over: bool, penalty: float = PINE_HALF_CROWD,
                          rel60: float | None = None) -> float:
    if CROWD_USE_REL60 and rel60 is not None:
        return float(score) - crowd_penalty_pine(rel60, over, half=penalty)
    return float(score) - (penalty if over else 0.0)


def annotate_pine_half(rows: list[dict], score_key: str = "score") -> list[dict]:
    """为每行写入 adj_score / over_ext / rank_score（pine_half）。

    rel60 字段：rel60 / rel60_pct / rs60d（若 md 解析提供）。
    """
    out = []
    for r in rows:
        rr = dict(r)
        over = is_over_ext(rr)
        sc = float(rr.get(score_key) or 0.0)
        rel60 = rr.get("rel60")
        if rel60 is None:
            rel60 = rr.get("rel60_pct")
        if rel60 is None:
            rel60 = rr.get("rs60d")
        try:
            rel60 = float(rel60) if rel60 is not None else None
        except Exception:
            rel60 = None
        rr["over_ext"] = over
        rr["rel60"] = rel60
        rr["pine_raw"] = sc
        pen = crowd_penalty_pine(rel60, over) if CROWD_USE_REL60 else (PINE_HALF_CROWD if over else 0.0)
        rr["crowd_pen"] = pen
        rr["adj_score"] = float(sc) - pen
        rr["rank_score"] = rr["adj_score"]
        rr["rank_engine"] = "pine_half_rel60" if (CROWD_USE_REL60 and rel60 is not None) else "pine_half_crowd"
        out.append(rr)
    return out


def download_mom_and_rel60(tickers: list[str], lookback: int = MOM_LOOKBACK, bench: str = "QQQ"):
    """返回 (mom6m_map, rel60_map)。rel60 = 100*(ratio/ratio[60]-1)，ratio=tic/QQQ。"""
    try:
        import yfinance as yf
    except Exception:
        return {}, {}
    end = datetime.utcnow().date() + timedelta(days=1)
    start = end - timedelta(days=int(max(lookback, 60) * 2.2) + 80)
    syms = list(dict.fromkeys([bench] + list(tickers)))
    try:
        raw = yf.download(
            syms, start=str(start), end=str(end), auto_adjust=True,
            progress=False, threads=False, group_by="column",
        )
    except Exception:
        return {}, {}
    if raw is None or len(raw) == 0:
        return {}, {}
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0):
            px = raw["Close"].copy()
        else:
            try:
                px = raw.xs("Close", axis=1, level=1).copy()
            except Exception:
                return {}, {}
    else:
        px = raw[["Close"]].copy() if "Close" in raw.columns else raw
        if px.shape[1] == 1 and len(syms) == 1:
            px.columns = [syms[0]]
    if bench not in px.columns:
        return {}, {}
    b = px[bench].dropna().ffill()
    mom_map, rel_map = {}, {}
    for t in tickers:
        if t not in px.columns:
            continue
        s = px[t].dropna().ffill()
        if len(s) <= lookback:
            continue
        mom_map[t] = float(s.iloc[-1] / s.iloc[-1 - lookback] - 1.0)
        # align ratio
        common = s.index.intersection(b.index)
        if len(common) < 80:
            continue
        ratio = s.reindex(common) / b.reindex(common)
        if len(ratio.dropna()) < 61:
            continue
        r = ratio.dropna()
        rel_map[t] = float(r.iloc[-1] / r.iloc[-61] - 1.0) * 100.0
    return mom_map, rel_map


def _download_mom6m(tickers: list[str], lookback: int = MOM_LOOKBACK) -> dict[str, float]:

    """最新 6M 价格动量 close/close[126]-1。失败返回 {}。"""
    try:
        import yfinance as yf
    except Exception:
        return {}
    end = datetime.utcnow().date() + timedelta(days=1)
    start = end - timedelta(days=int(lookback * 2.2) + 40)
    try:
        raw = yf.download(
            tickers,
            start=str(start),
            end=str(end),
            auto_adjust=True,
            progress=False,
            threads=False,
            group_by="column",
        )
    except Exception:
        return {}
    if raw is None or len(raw) == 0:
        return {}
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0):
            px = raw["Close"].copy()
        else:
            try:
                px = raw.xs("Close", axis=1, level=1).copy()
            except Exception:
                return {}
    else:
        px = raw[["Close"]].copy() if "Close" in raw.columns else raw
        if px.shape[1] == 1 and len(tickers) == 1:
            px.columns = [tickers[0]]
    out = {}
    for t in tickers:
        if t not in px.columns:
            continue
        s = px[t].dropna()
        if len(s) <= lookback:
            continue
        out[t] = float(s.iloc[-1] / s.iloc[-1 - lookback] - 1.0)
    return out


@dataclass
class Top3Result:
    picks: list[dict]
    banned: list[dict]
    engine: str
    note: str
    mom_map: dict


def select_top3_sector_pine_half(
    sectors: list[dict],
    pool_names: Iterable[str],
    score_ban: float = 45.0,
    status_off: Optional[set] = None,
    k: int = 3,
) -> Top3Result:
    """标普默认：pine_half_crowd 排序。"""
    status_off = status_off or {"资金撤出"}
    pool = [s for s in sectors if s.get("name") in set(pool_names)]
    annotated = annotate_pine_half(pool)
    banned = [
        s for s in annotated
        if (s.get("status") in status_off or "撤出" in str(s.get("status") or "") or float(s.get("pine_raw") or 0) < score_ban)
    ]
    cand = [s for s in annotated if s not in banned]
    if len(cand) < k:
        for s in sorted(annotated, key=lambda x: -float(x.get("adj_score") or 0)):
            if s not in cand:
                cand.append(s)
            if len(cand) >= k:
                break
    cand.sort(key=lambda s: (-float(s.get("adj_score") or 0), -float(s.get("rs5d") or 0), s.get("name") or ""))
    picks = cand[:k]
    for i, p in enumerate(picks):
        p["rank"] = i + 1
    note = (
        "标普Top3=pine_half"
        + ("_rel60" if CROWD_USE_REL60 else "_crowd")
        + "；选中: "
        + ", ".join(
            f"{p['name']} raw={p.get('pine_raw')} adj={float(p.get('adj_score') or 0):.1f}"
            + (f" rel60={float(p.get('rel60')):+.1f}" if p.get("rel60") is not None else "")
            + (f" pen={float(p.get('crowd_pen') or 0):.1f}" if p.get("crowd_pen") else "")
            for p in picks
        )
    )
    eng = picks[0].get("rank_engine") if picks else "pine_half_crowd"
    return Top3Result(picks=picks, banned=banned, engine=eng or "pine_half_crowd", note=note, mom_map={})



def select_top3_sector_mom12(
    sectors: list[dict],
    pool_names: Iterable[str],
    score_ban: float = 45.0,
    status_off: Optional[set] = None,
    soft_crowd: bool = True,
    soft_pp: float = S3_SOFT_CROWD_PP,
    k: int = 3,
    lookback: int = MOM12_LOOKBACK,
    mom_map: Optional[dict[str, float]] = None,
    ban_bottom_pct: float = 20.0,
) -> Top3Result:
    """标普可选引擎：12M 价格动量 Top3（默认生产仍用 pine_half）。

    - rank = mom12 - soft_crowd_pen（与科技 S3 同量级 5pp）
    - 禁入：资金撤出 / score<score_ban / mom 分位最弱 ban_bottom_pct%
    - mom 下载失败时回退 pine_half，避免空 Top3
    """
    status_off = status_off or {"资金撤出"}
    pool = [s for s in sectors if s.get("name") in set(pool_names)]
    annotated = []
    for s in pool:
        rr = dict(s)
        rr["over_ext"] = is_over_ext(rr)
        rr["ticker"] = SECTOR_ETF.get(rr["name"]) or rr.get("ticker")
        annotated.append(rr)

    tickers = [r["ticker"] for r in annotated if r.get("ticker")]
    rel60_map: dict = {}
    if mom_map is not None:
        mm = dict(mom_map)
    else:
        mm, rel60_map = download_mom_and_rel60(tickers, lookback=lookback, bench="SPY")

    if not mm:
        # 安全回退：保持可出报告
        return select_top3_sector_pine_half(
            sectors, pool_names, score_ban=score_ban, status_off=status_off, k=k,
        )

    moms_valid = [float(mm[t]) for t in tickers if t in mm and np.isfinite(mm[t])]
    thr = float(np.nanpercentile(moms_valid, ban_bottom_pct)) if len(moms_valid) >= 3 else -1e18

    for rr in annotated:
        tic = rr.get("ticker")
        mom = mm.get(tic) if tic else None
        rr["mom12"] = mom
        rr["mom6m"] = mom  # 兼容 _topn_sat_weights 读 mom6m
        rel60 = rr.get("rel60")
        if rel60 is None:
            rel60 = rr.get("rel60_pct")
        if rel60 is None and tic and isinstance(rel60_map, dict):
            rel60 = rel60_map.get(tic)
        try:
            rel60 = float(rel60) if rel60 is not None else None
            if rel60 is not None and rel60 != rel60:
                rel60 = None
        except Exception:
            rel60 = None
        rr["rel60"] = rel60
        sc = float(rr.get("score") or 0.0)
        rr["pine_raw"] = sc
        rr["adj_score"] = pine_half_crowd_score(sc, rr["over_ext"], rel60=rel60)

        if mom is None or not np.isfinite(mom):
            rr["mom_rank"] = -1e9
            rr["crowd_pen"] = 0.0
        else:
            if soft_crowd:
                if CROWD_USE_REL60:
                    pen = crowd_penalty_mom(rel60, rr["over_ext"])
                    pen = min(float(soft_pp), pen)
                else:
                    pen = float(soft_pp) if rr["over_ext"] else 0.0
            else:
                pen = 0.0
            rr["crowd_pen"] = pen
            rr["mom_rank"] = float(mom) - pen
        rr["rank_score"] = rr["mom_rank"]
        eng = "mom12"
        if soft_crowd:
            eng = "mom12_rel60" if (CROWD_USE_REL60 and rel60 is not None) else "mom12_soft"
        rr["rank_engine"] = eng

    banned = []
    cand = []
    for rr in annotated:
        st = str(rr.get("status") or "")
        withdraw = (rr.get("status") in status_off) or ("撤出" in st)
        low = float(rr.get("pine_raw") or 0) < float(score_ban)
        mom = rr.get("mom12")
        weak_mom = (mom is not None and np.isfinite(mom) and float(mom) <= thr)
        if withdraw or low or weak_mom or float(rr.get("mom_rank") or -1e9) <= -1e8:
            banned.append(rr)
        else:
            cand.append(rr)

    if len(cand) < k:
        # 放宽：仍避开撤出，但允许低分/弱动量补齐
        for rr in sorted(annotated, key=lambda x: -float(x.get("mom_rank") or -1e9)):
            st = str(rr.get("status") or "")
            if (rr.get("status") in status_off) or ("撤出" in st):
                continue
            if rr not in cand:
                cand.append(rr)
            if len(cand) >= k:
                break

    cand.sort(key=lambda s: (-float(s.get("mom_rank") or -1e9), -float(s.get("rs5d") or 0), s.get("name") or ""))
    picks = cand[:k]
    for i, p in enumerate(picks):
        p["rank"] = i + 1
    eng = picks[0].get("rank_engine") if picks else "mom12"
    parts = []
    for p in picks:
        m = p.get("mom12")
        ms = f"{m*100:+.1f}%" if m is not None and np.isfinite(m) else "N/A"
        pen = float(p.get("crowd_pen") or 0.0)
        parts.append(
            f"{p['name']}({p.get('ticker')}) mom12={ms}"
            + (f" pen={pen*100:.1f}pp" if soft_crowd and pen > 1e-6 else "")
            + (f" rel60={float(p['rel60']):+.1f}" if p.get("rel60") is not None else "")
        )
    note = f"标普Top3={eng}（12M动量；非默认 pine）；" + "；".join(parts)
    return Top3Result(picks=picks, banned=banned, engine=eng or "mom12", note=note, mom_map=mm)


def select_top3_sector_blend(
    sectors: list[dict],
    pool_names: Iterable[str],
    score_ban: float = 45.0,
    status_off: Optional[set] = None,
    soft_crowd: bool = True,
    soft_pp: float = S3_SOFT_CROWD_PP,
    k: int = 3,
    lookback: int = MOM12_LOOKBACK,
    mom_map: Optional[dict[str, float]] = None,
    ban_bottom_pct: float = 20.0,
) -> Top3Result:
    """标普研究引擎：0.5·z(pine_half) + 0.5·z(mom12) 截面融合 Top3。

    - 禁入同 mom12：撤出 / score<ban / mom 最弱 ban_bottom_pct%
    - mom 下载失败时回退 pine_half
    - 生产默认仍 pine_half；本函数仅旗标 SECTOR_TOP3_ENGINE=blend
    """
    status_off = status_off or {"资金撤出"}
    pool = [s for s in sectors if s.get("name") in set(pool_names)]
    annotated = []
    for s in pool:
        rr = dict(s)
        rr["over_ext"] = is_over_ext(rr)
        rr["ticker"] = SECTOR_ETF.get(rr["name"]) or rr.get("ticker")
        annotated.append(rr)

    tickers = [r["ticker"] for r in annotated if r.get("ticker")]
    rel60_map: dict = {}
    if mom_map is not None:
        mm = dict(mom_map)
    else:
        mm, rel60_map = download_mom_and_rel60(tickers, lookback=lookback, bench="SPY")

    if not mm:
        return select_top3_sector_pine_half(
            sectors, pool_names, score_ban=score_ban, status_off=status_off, k=k,
        )

    moms_valid = [float(mm[t]) for t in tickers if t in mm and np.isfinite(mm[t])]
    thr = float(np.nanpercentile(moms_valid, ban_bottom_pct)) if len(moms_valid) >= 3 else -1e18

    eligible = []
    for rr in annotated:
        tic = rr.get("ticker")
        mom = mm.get(tic) if tic else None
        rr["mom12"] = mom
        rr["mom6m"] = mom
        rel60 = rr.get("rel60")
        if rel60 is None:
            rel60 = rr.get("rel60_pct")
        if rel60 is None and tic and isinstance(rel60_map, dict):
            rel60 = rel60_map.get(tic)
        try:
            rel60 = float(rel60) if rel60 is not None else None
            if rel60 is not None and rel60 != rel60:
                rel60 = None
        except Exception:
            rel60 = None
        rr["rel60"] = rel60
        sc = float(rr.get("score") or 0.0)
        rr["pine_raw"] = sc
        rr["adj_score"] = pine_half_crowd_score(sc, rr["over_ext"], rel60=rel60)

        if mom is None or not np.isfinite(mom):
            rr["mom_rank"] = -1e9
            rr["crowd_pen"] = 0.0
        else:
            if soft_crowd:
                if CROWD_USE_REL60:
                    pen = crowd_penalty_mom(rel60, rr["over_ext"])
                    pen = min(float(soft_pp), pen)
                else:
                    pen = float(soft_pp) if rr["over_ext"] else 0.0
            else:
                pen = 0.0
            rr["crowd_pen"] = pen
            rr["mom_rank"] = float(mom) - pen

        st = str(rr.get("status") or "")
        withdraw = (rr.get("status") in status_off) or ("撤出" in st)
        low = float(rr.get("pine_raw") or 0) < float(score_ban)
        weak_mom = (mom is not None and np.isfinite(mom) and float(mom) <= thr)
        if withdraw or low or weak_mom or float(rr.get("mom_rank") or -1e9) <= -1e8:
            rr["_blend_ban"] = True
        else:
            rr["_blend_ban"] = False
            eligible.append(rr)

    banned = [rr for rr in annotated if rr.get("_blend_ban")]
    cand = list(eligible)

    if len(cand) >= 2:
        pine_vals = np.array([float(r["adj_score"]) for r in cand], dtype=float)
        mom_vals = np.array([float(r["mom_rank"]) for r in cand], dtype=float)
        def _z(a: np.ndarray) -> np.ndarray:
            sd = float(a.std(ddof=0))
            if sd < 1e-12:
                return np.zeros_like(a)
            return (a - float(a.mean())) / sd
        zp, zm = _z(pine_vals), _z(mom_vals)
        for i, rr in enumerate(cand):
            rr["blend_z_pine"] = float(zp[i])
            rr["blend_z_mom"] = float(zm[i])
            rr["blend_score"] = 0.5 * float(zp[i]) + 0.5 * float(zm[i])
            rr["rank_score"] = rr["blend_score"]
            rr["rank_engine"] = "blend"
    else:
        for rr in cand:
            rr["blend_score"] = float(rr.get("adj_score") or 0)
            rr["rank_score"] = rr["blend_score"]
            rr["rank_engine"] = "blend"

    if len(cand) < k:
        for rr in sorted(annotated, key=lambda x: -float(x.get("adj_score") or 0)):
            st = str(rr.get("status") or "")
            if (rr.get("status") in status_off) or ("撤出" in st):
                continue
            if rr not in cand:
                if "blend_score" not in rr:
                    rr["blend_score"] = float(rr.get("adj_score") or 0)
                    rr["rank_score"] = rr["blend_score"]
                    rr["rank_engine"] = "blend"
                cand.append(rr)
            if len(cand) >= k:
                break

    cand.sort(key=lambda s: (-float(s.get("blend_score") or -1e9), -float(s.get("rs5d") or 0), s.get("name") or ""))
    picks = cand[:k]
    for i, p in enumerate(picks):
        p["rank"] = i + 1
    parts = []
    for p in picks:
        m = p.get("mom12")
        ms = f"{m*100:+.1f}%" if m is not None and np.isfinite(m) else "N/A"
        parts.append(
            f"{p['name']}({p.get('ticker')}) blend={float(p.get('blend_score') or 0):+.2f}"
            f" pine_adj={float(p.get('adj_score') or 0):.1f} mom12={ms}"
        )
    note = "标普Top3=blend（0.5 z(pine)+0.5 z(mom12)；非默认）；" + "；".join(parts)
    return Top3Result(picks=picks, banned=banned, engine="blend", note=note, mom_map=mm)


def select_top3_tech_mom6m(
    themes: list[dict],
    soft_crowd: bool = True,
    soft_pp: float = S3_SOFT_CROWD_PP,
    k: int = 3,
    mom_map: Optional[dict[str, float]] = None,
) -> Top3Result:
    """
    科技生产主引擎：6 主题 ETF 的 6M 价格动量。
    soft_crowd=True 时 mom_rank = mom - 0.05*over（可在2–3，不做 hard Top1 帽）。
    不按 score_ban 剔除（与裸 S3 一致）；状态仅用于拥挤标记。
    """
    tradeable = [t for t in themes if t.get("name") in THEME_ETF]
    annotated = []
    for t in tradeable:
        rr = dict(t)
        rr["over_ext"] = is_over_ext(rr)
        rr["ticker"] = THEME_ETF[rr["name"]]
        annotated.append(rr)

    tickers = [THEME_ETF[t["name"]] for t in annotated]
    rel60_map = {}
    if mom_map is not None:
        mm = mom_map
    else:
        mm, rel60_map = download_mom_and_rel60(tickers)
    for rr in annotated:
        tic = rr["ticker"]
        mom = mm.get(tic)
        rr["mom6m"] = mom
        # rel60 from row or from downloaded ratio map
        rel60 = rr.get("rel60")
        if rel60 is None:
            rel60 = rr.get("rel60_pct")
        if rel60 is None and isinstance(rel60_map, dict):
            rel60 = rel60_map.get(tic)
        try:
            rel60 = float(rel60) if rel60 is not None else None
            if rel60 is not None and rel60 != rel60:
                rel60 = None
        except Exception:
            rel60 = None
        rr["rel60"] = rel60
        if mom is None or not np.isfinite(mom):
            rr["mom_rank"] = -1e9
            rr["crowd_pen"] = 0.0
        else:
            if soft_crowd:
                if CROWD_USE_REL60:
                    pen = crowd_penalty_mom(rel60, rr["over_ext"])
                    # scale to soft_pp cap
                    pen = min(float(soft_pp), pen)
                else:
                    pen = float(soft_pp) if rr["over_ext"] else 0.0
            else:
                pen = 0.0
            rr["crowd_pen"] = pen
            rr["mom_rank"] = float(mom) - pen
        eng = "mom6m_S3"
        if soft_crowd:
            eng = "mom6m_S3_rel60" if (CROWD_USE_REL60 and rel60 is not None) else "mom6m_S3_soft"
        rr["rank_engine"] = eng
        rr["rank_score"] = rr["mom_rank"]
        sc = float(rr.get("score") or 0)
        rr["pine_raw"] = sc
        rr["adj_score"] = pine_half_crowd_score(sc, rr["over_ext"], rel60=rel60)

    ranked = sorted(
        annotated,
        key=lambda x: (-float(x.get("mom_rank") or -1e9), -float(x.get("rs5d") or 0), x.get("name") or ""),
    )
    picks = ranked[:k]
    for i, p in enumerate(picks):
        p["rank"] = i + 1
    banned = []  # S3 不因低分 ban；撤出主题仍可按动量入选（与裸 S3 一致）
    eng = picks[0].get("rank_engine") if picks else ("mom6m_S3_soft" if soft_crowd else "mom6m_S3")
    # majority engine label
    if picks:
        eng = picks[0].get("rank_engine") or eng
    parts = []
    for p in picks:
        m = p.get("mom6m")
        ms = f"{m*100:+.1f}%" if m is not None and np.isfinite(m) else "N/A"
        pen = p.get("crowd_pen") or 0.0
        parts.append(
            f"{p['name']}({p['ticker']}) mom6m={ms}"
            + (f" pen={float(pen)*100:.1f}pp" if soft_crowd and float(pen) > 1e-6 else "")
            + (f"→{p['mom_rank']*100:+.1f}%" if soft_crowd and float(pen) > 1e-6 else "")
            + (f" rel60={float(p['rel60']):+.1f}" if p.get("rel60") is not None else "")
            + (" 拥挤" if p.get("over_ext") else "")
        )
    note = f"科技Top3={eng}（主引擎；非 pine 分）；" + "；".join(parts)
    if not mm:
        note += "｜警告: mom6m 下载失败，排序可能无效"
    return Top3Result(picks=picks, banned=banned, engine=eng, note=note, mom_map=mm)


def format_top3_line_tech(picks: list[dict]) -> str:
    bits = []
    for p in picks:
        tic = p.get("ticker") or THEME_ETF.get(p["name"], "?")
        m = p.get("mom6m")
        ms = f" mom6m {m*100:+.1f}%" if m is not None and np.isfinite(m) else ""
        bits.append(f"{tic}（{p['name']}{ms}）")
    return " · ".join(bits)


def format_top3_line_sector(picks: list[dict]) -> str:
    bits = []
    for p in picks:
        tic = p.get("ticker") or SECTOR_ETF.get(p["name"], "?")
        m12 = p.get("mom12")
        if m12 is not None and np.isfinite(m12):
            bits.append(f"{tic}（{p['name']} mom12 {m12*100:+.1f}%）")
            continue
        adj = p.get("adj_score")
        raw = p.get("pine_raw", p.get("score"))
        if adj is not None and raw is not None and abs(float(adj) - float(raw)) > 0.05:
            bits.append(f"{tic}（{p['name']} adj{float(adj):.0f}/raw{float(raw):.0f}）")
        else:
            bits.append(f"{tic}（{p['name']} {float(raw or 0):.0f}）")
    return " · ".join(bits)


if __name__ == "__main__":
    # smoke
    demo = [
        {"name": "软件", "score": 89, "status": "拥挤主升", "rs5d": 4.0},
        {"name": "云计算", "score": 56, "status": "中性观察", "rs5d": 1.0},
        {"name": "网络安全", "score": 72, "status": "早期轮动", "rs5d": 4.0},
        {"name": "AI综合", "score": 62, "status": "早期轮动", "rs5d": 0.4},
        {"name": "半导体", "score": 16, "status": "资金撤出", "rs5d": -1.0},
        {"name": "机器人", "score": 8, "status": "派发撤出", "rs5d": -1.5},
    ]
    r = select_top3_tech_mom6m(demo, soft_crowd=True)
    print(r.engine, r.note)
    for p in r.picks:
        print(p["name"], p.get("mom6m"), p.get("mom_rank"), p.get("over_ext"))
