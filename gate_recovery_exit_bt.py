# -*- coding: utf-8 -*-
"""
gate_recovery_exit_bt.py — 分数闸 eq_acute 旧规则 vs 恢复退出 对比回测（研究）
用法: SECTOR_PROXY=off /workspace/macro-os/.venv/bin/python gate_recovery_exit_bt.py
- denom / nq 腿：gate_legs.build_leg_frame（官方 overlay + pipeline；窗外 VIX 代理）
- equity 腿：按「生产口径」重建（SOXX worst-window raw / 2日 smooth / 减震器 -7%）
- 书：C 结构 70 核心 + 30×sat_mult Top3 + BIL；科技 mom6m 软拥挤，标普 pine_half
- 日频：T 收盘信号 → T+1 收益；单边成本 0.1%
"""
from __future__ import annotations
import json, os, sys, warnings
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd

os.environ["SECTOR_PROXY"] = "off"
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import gate_legs as gl
import gate_recovery as gr
import shadow_c_enhancement_bt as sc
import shadow_tech_mom_compare_bt as tm

OUT = ROOT / "output" / "research"
OUT.mkdir(parents=True, exist_ok=True)
END = "2026-11-13"
WARM = "2021-01-01"
SIM_START = "2022-06-01"
WINDOWS = {
    "近3年": ("2023-11-13", END),
    "近1年": ("2025-11-13", END),
    "2026-10~11": ("2026-10-01", END),
}
DAMPENER_DD = -0.07   # macro-os TECH_DRAWDOWN_CAPS 最浅档
PARAMS = gl.load_params()


def equity_legs(soxx: pd.Series, qqq: pd.Series, variant: str, rec_params=None):
    d = gr.dd20_series(soxx)
    sm2 = d.rolling(2, min_periods=1).min()
    if variant == "bt_locked":          # gate_legs 标定口径（参考）
        raw = gl.dd20(soxx)
        smooth = pd.concat([gl.dd20(soxx), gl.dd20(qqq)], axis=1).min(axis=1)
        acute = (raw.fillna(smooth) <= PARAMS["eq_acute_raw"]) | (smooth <= PARAMS["eq_acute_smooth"])
        soft = (smooth >= PARAMS["eq_soft_lo"]) & (smooth < PARAMS["eq_soft_hi"]) & ~acute
        return acute, soft, smooth
    raw = d if "curraw" in variant else gr.worst_window_dd_series(soxx)
    acute = (raw <= PARAMS["eq_acute_raw"]) | (sm2 <= PARAMS["eq_acute_smooth"])
    if "nodamp" not in variant:
        acute = acute | (sm2 <= DAMPENER_DD)
    soft = (sm2 >= PARAMS["eq_soft_lo"]) & (sm2 < PARAMS["eq_soft_hi"]) & ~acute
    if variant in ("prod_old", "curraw", "curraw_nodamp"):
        return acute, soft, sm2
    if variant.startswith("recov") or rec_params is not None:
        rec = gr.recovery_exit_series(soxx, rec_params)
        a2, s2 = gr.apply_recovery(acute, soft, sm2, rec, rec_params, PARAMS["eq_soft_lo"], PARAMS["eq_soft_hi"])
        return a2, s2, sm2
    raise ValueError(variant)


def gate_frame(legs, acute, soft):
    sc_ = (legs["denom"].astype(float) * PARAMS["denom_w"] + legs["nq"].astype(float) * PARAMS["nq_w"]
           + acute.astype(float) * PARAMS["eq_acute_w"] + soft.astype(float) * PARAMS["soft_w"])
    sm = gl.sat_mult_series(sc_, PARAMS, continuous=True)
    st = gl.state_from_score(sc_, PARAMS, shift=False)
    return pd.DataFrame({"score": sc_, "sat_mult": sm, "state": st, "acute": acute, "soft": soft})


def run_book(close, idx, gate, side, panel):
    targets = {}
    for d in idx:
        smv = float(gate.at[d, "sat_mult"])
        if side == "tech":
            targets[d] = tm.book_c_tech(tm.select_top3(d, close, "mom6m"), smv)
        else:
            targets[d] = sc.book_weights("C", "SPY", sc.select_top3_pine(d, panel), smv, "OFF")
    cl = close.reindex(idx).ffill()
    if side == "tech":
        nav, turns, _ = tm.simulate_tech(cl, targets, 0.001)
    else:
        nav, turns, _ = sc.simulate(cl, targets, 0.001)
    return nav


def mets(nav, a, b):
    n = nav.loc[a:b]
    n = n / n.iloc[0]
    r = n.pct_change().fillna(0)
    yrs = max((n.index[-1] - n.index[0]).days / 365.25, 1e-9)
    vol = r.std() * np.sqrt(252)
    return {"total": float(n.iloc[-1] - 1), "cagr": float(n.iloc[-1] ** (1 / yrs) - 1) if yrs > 0.3 else None,
            "sharpe": float(r.mean() * 252 / vol) if vol > 0 else 0.0, "mdd": float((n / n.cummax() - 1).min())}


def episode_lag(soxx, old_acute, new_acute, a, b):
    """旧规则 acute 连续段：谷底(段内最低收盘)→段结束 的交易日数；新规则同段内最后 ON 日。"""
    oa = old_acute.loc[a:b]; na = new_acute.loc[a:b]; px = soxx.loc[a:b]
    eps, cur = [], None
    for i, (d, v) in enumerate(oa.items()):
        if v and cur is None: cur = i
        if (not v) and cur is not None: eps.append((cur, i - 1)); cur = None
    if cur is not None: eps.append((cur, len(oa) - 1))
    rows = []
    for s, e in eps:
        if e - s < 2: continue
        seg = px.iloc[s:e + 1]
        ti = s + int(np.argmin(seg.values))
        nseg = na.iloc[s:e + 1].values
        last_on = s + int(np.where(nseg)[0].max()) if nseg.any() else s - 1
        flips = int(np.sum(np.diff(nseg.astype(int)) == 1))
        rows.append({"start": str(oa.index[s].date()), "end": str(oa.index[e].date()),
                     "trough": str(oa.index[ti].date()), "days": e - s + 1,
                     "lag_old": e - ti + 1, "lag_new": max(0, last_on - ti + 1),
                     "new_on_days": int(nseg.sum()), "new_reentries": flips})
    return rows


def main():
    sec = list(sc.SECTOR_ETF.values())
    tick = sorted(set(sec + tm.THEME_TICKERS + ["SPY", "QQQ", "SOXX", "BIL"]))
    close, vol = sc.download_close_vol(tick, WARM, END)
    close = close.loc[:END]; vol = vol.reindex(close.index)
    legs = gl.build_leg_frame("2018-01-01", "2026-11-14", PARAMS).reindex(close.index).ffill()
    soxx, qqq = close["SOXX"], close["QQQ"]
    panel = sc.pine_panel(close, vol, close["SPY"])
    idx = close.index[close.index >= SIM_START]

    variants = {
        "bt_locked(标定口径参考)": ("bt_locked", None),
        "prod_old(生产旧规则)": ("prod_old", None),
        "recov10_soft(新·主方案)": ("recov", {"recovery_min_rebound": 0.10, "recovery_mode": "to_soft"}),
        # 敏感性（不用于挑参）
        "recov08_soft": ("recov", {"recovery_min_rebound": 0.08, "recovery_mode": "to_soft"}),
        "recov12_soft": ("recov", {"recovery_min_rebound": 0.12, "recovery_mode": "to_soft"}),
        "recov15_soft": ("recov", {"recovery_min_rebound": 0.15, "recovery_mode": "to_soft"}),
        "recov10_off": ("recov", {"recovery_min_rebound": 0.10, "recovery_mode": "off"}),
        # 事后补测（看到上面结果后追加，已在报告中披露）：raw 改当前回撤 / 去掉减震器直连
        "curraw": ("curraw", None),
        "curraw_nodamp": ("curraw_nodamp", None),
        "curraw+recov10": ("curraw_r", {"recovery_min_rebound": 0.10, "recovery_mode": "to_soft"}),
        "curraw+recov08": ("curraw_r", {"recovery_min_rebound": 0.08, "recovery_mode": "to_soft"}),
        "curraw+recov12": ("curraw_r", {"recovery_min_rebound": 0.12, "recovery_mode": "to_soft"}),
        "curraw+recov15": ("curraw_r", {"recovery_min_rebound": 0.15, "recovery_mode": "to_soft"}),
        "curraw_nodamp+recov10": ("curraw_nodamp_r", {"recovery_min_rebound": 0.10, "recovery_mode": "to_soft"}),
    }
    res, gates = {}, {}
    for name, (v, rp) in variants.items():
        a, s, _ = equity_legs(soxx, qqq, v, rp)
        g = gate_frame(legs, a, s)
        gates[name] = g
        out = {}
        for side in ("tech", "spy"):
            nav = run_book(close, idx, g, side, panel)
            out[side] = {w: mets(nav, *ab) for w, ab in WINDOWS.items()}
        out["gate"] = {}
        for w, (a0, b0) in WINDOWS.items():
            gg = g.loc[a0:b0]
            out["gate"][w] = {"acute_days": int(gg["acute"].sum()), "warn_days": int((gg["state"] == 1).sum()),
                              "bear_days": int((gg["state"] == 2).sum()), "n": int(len(gg)),
                              "sat_mult_mean": float(gg["sat_mult"].mean())}
        res[name] = out
        print("done", name, flush=True)

    old_a = gates["prod_old(生产旧规则)"]["acute"]
    lags = {}
    for name in variants:
        if name != "prod_old(生产旧规则)" and not name.startswith("bt_"):
            lags[name] = {w: episode_lag(soxx, old_a, gates[name]["acute"], *ab) for w, ab in WINDOWS.items()}
    # 2026-10~11 逐日
    a0 = "2026-10-01"
    tr = gr.trajectory_series(soxx)
    daily = pd.DataFrame({
        "soxx": soxx, "dd20": tr["dd20"], "worst21": gr.worst_window_dd_series(soxx),
        "rebound": tr["rebound"], "slope5_pp": tr["slope_pp"],
        "denom": legs["denom"], "nq": legs["nq"],
        "old_score": gates["prod_old(生产旧规则)"]["score"], "old_sat": gates["prod_old(生产旧规则)"]["sat_mult"],
        "new_score": gates["recov10_soft(新·主方案)"]["score"], "new_sat": gates["recov10_soft(新·主方案)"]["sat_mult"],
        "B_score": gates["curraw+recov10"]["score"], "B_sat": gates["curraw+recov10"]["sat_mult"],
        "cn_score": gates["curraw_nodamp+recov10"]["score"], "cn_sat": gates["curraw_nodamp+recov10"]["sat_mult"],
    }).loc[a0:END]
    payload = {"generated": datetime.now().isoformat(timespec="seconds"), "end": END, "params": PARAMS,
               "results": res, "lags": lags,
               "daily_2026_10_11": json.loads(daily.reset_index().to_json(orient="records", date_format="iso"))}
    (OUT / "gate_recovery_exit_bt_2026-11-14.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    daily.round(4).to_csv(OUT / "gate_recovery_exit_daily_2026-10-11.csv")
    print(json.dumps({k: {s: v[s] for s in ("tech", "spy", "gate")} for k, v in res.items()}, ensure_ascii=False, indent=1))
    for k, v in lags.items():
        print(k)
        for w, rows in v.items():
            if rows:
                print(" ", w, "n=", len(rows), "lag_old=", np.mean([r["lag_old"] for r in rows]).round(2),
                      "lag_new=", np.mean([r["lag_new"] for r in rows]).round(2),
                      "reentries=", sum(r["new_reentries"] for r in rows))
    print(daily.round(4).to_string())


if __name__ == "__main__":
    main()
