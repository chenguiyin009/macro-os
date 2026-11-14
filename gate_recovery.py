# -*- coding: utf-8 -*-
"""
gate_recovery.py — 分数闸 eq_acute「恢复退出」规则（2026-11-14）
================================================================
问题：生产 eq_acute 由三条任一点亮
  (a) raw_dd = SOXX 近21根收盘窗口内「最坏峰谷回撤」≤ -10%（窗口内最坏点，深跌后会
      原样挂满约 20 个交易日，与当前价无关）；
  (b) smooth_dd = SOXX 20日峰值回撤的 2 日滚动最小值 ≤ gate_params.eq_acute_smooth；
  (c) macro-os 减震器 dampener_active（smooth_dd ≤ -7% 且预算被压）。
三条都只看「回撤深度」，不看「是否已经在恢复」，V 反后会晚退出。

恢复退出（参数 2 个，事先指定，不做网格挑优）：
  若 eq_acute 为 ON，且
     自 30 根内谷底反弹 ≥ min_rebound（默认 10%） 且
     近 5 日 20日峰值回撤斜率 > 0（回撤在收窄，= trajectory.drawdown_slope_5d_pp>0）
  → eq_acute 降为 eq_soft（权重 0.5）。价格再走弱（斜率≤0 或反弹回吐到 <10%）即恢复 acute。
口径与 macro-os adapters/equity_stress.compute_soxx_trajectory 完全一致
（trough=近30根最低收盘；slope=dd20 近5点线性回归斜率）。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd

RECOVERY_DEFAULTS = {
    "recovery_exit_enabled": True,
    "recovery_min_rebound": 0.10,   # 自谷底反弹 ≥10%
    "recovery_slope_days": 5,       # dd20 斜率窗口（与 trajectory 的 5d 对齐）
    "recovery_trough_win": 30,      # 谷底回看窗口（与 trajectory 对齐）
    "recovery_mode": "to_soft",     # to_soft: acute→soft(0.5)；off: acute→按 soft 带重判
}


def recovery_exit_from_trajectory(traj: Optional[dict], params: Optional[dict] = None) -> Tuple[bool, str]:
    """生产点值：读 daily_macro.tech_dampener.trajectory。缺字段 → 不触发（保守，维持旧行为）。"""
    p = {**RECOVERY_DEFAULTS, **(params or {})}
    if not p.get("recovery_exit_enabled", True):
        return False, "恢复退出未启用"
    if not isinstance(traj, dict):
        return False, "无 trajectory → 不做恢复退出"
    try:
        reb = float(traj.get("recovery_from_trough_pct")) / 100.0
        slope_key = f"drawdown_slope_{int(p['recovery_slope_days'])}d_pp"
        slope = float(traj.get(slope_key))
    except (TypeError, ValueError):
        return False, "trajectory 字段缺失 → 不做恢复退出"
    if not (np.isfinite(reb) and np.isfinite(slope)):
        return False, "trajectory 非有限值 → 不做恢复退出"
    ok = (reb >= float(p["recovery_min_rebound"])) and (slope > 0.0)
    why = (
        f"自谷底({traj.get('trough_date', '?')})反弹{reb:+.1%}"
        f"{'≥' if reb >= p['recovery_min_rebound'] else '<'}{p['recovery_min_rebound']:.0%}，"
        f"{int(p['recovery_slope_days'])}日回撤斜率{slope:+.2f}pp/日{'>0' if slope > 0 else '≤0'}"
    )
    return ok, why


# ---------------------------------------------------------------- 序列（回测用，无前视）
def dd20_series(close: pd.Series, days: int = 20) -> pd.Series:
    rm = close.rolling(days, min_periods=1).max()
    return close / rm - 1.0


def worst_window_dd_series(close: pd.Series, days: int = 20) -> pd.Series:
    """等价 macro-os peak_to_trough_drawdown(closes[:t+1], days)：近 days+1 根内最坏峰谷回撤。"""
    v = close.values.astype(float)
    out = np.full(len(v), np.nan)
    for t in range(len(v)):
        w = v[max(0, t - days): t + 1]
        if len(w) < 2:
            out[t] = 0.0
            continue
        rm = np.maximum.accumulate(w)
        out[t] = min(0.0, float(np.min(w / rm - 1.0)))
    return pd.Series(out, index=close.index)


def trajectory_series(close: pd.Series, days: int = 20, trough_win: int = 30, slope_days: int = 5) -> pd.DataFrame:
    """逐日复现 compute_soxx_trajectory 的 recovery / slope（pp/日）。"""
    v = close.values.astype(float)
    dd = dd20_series(close, days).values
    n = len(v)
    reb = np.full(n, np.nan)
    slope = np.full(n, np.nan)
    trough_i = np.full(n, -1)
    x = np.arange(slope_days, dtype=float)
    for t in range(n):
        lo = max(0, t - trough_win + 1)
        w = v[lo: t + 1]
        ti = lo + int(np.argmin(w))
        trough_i[t] = ti
        reb[t] = v[t] / v[ti] - 1.0 if v[ti] > 0 else 0.0
        if t + 1 >= slope_days:
            slope[t] = float(np.polyfit(x, dd[t - slope_days + 1: t + 1], 1)[0]) * 100.0
    return pd.DataFrame({"rebound": reb, "slope_pp": slope, "trough_i": trough_i, "dd20": dd}, index=close.index)


def recovery_exit_series(close: pd.Series, params: Optional[dict] = None) -> pd.Series:
    p = {**RECOVERY_DEFAULTS, **(params or {})}
    tr = trajectory_series(close, trough_win=int(p["recovery_trough_win"]), slope_days=int(p["recovery_slope_days"]))
    return (tr["rebound"] >= float(p["recovery_min_rebound"])) & (tr["slope_pp"] > 0.0)


def apply_recovery(acute: pd.Series, soft: pd.Series, smooth: pd.Series, rec: pd.Series,
                   params: Optional[dict] = None, soft_lo: float = -0.10, soft_hi: float = -0.05):
    """返回 (acute_new, soft_new)。"""
    p = {**RECOVERY_DEFAULTS, **(params or {})}
    hit = acute & rec.reindex(acute.index).fillna(False)
    a2 = acute & ~hit
    if p["recovery_mode"] == "to_soft":
        s2 = (soft | hit) & ~a2
    else:
        band = (smooth >= soft_lo) & (smooth < soft_hi)
        s2 = (soft | (hit & band)) & ~a2
    return a2, s2
