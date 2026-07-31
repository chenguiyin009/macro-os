"""Macro OS · 个人交易员工作台 (Local Workbench MVP, v2 已打通).

本地纯 Streamlit 应用，把已冻结的 L1/L2/L3 引擎产出可视化，并**直接消费真实管线产物**：

  - Tab 1 宏观雷达   : Softmax 概率沙盘 + 真实 combined_risk_budget (分母/减震/主题三顶)
  - Tab 2 主题 & 科技 : theme_pressure_level(AND门) + 科技减震器 + 科技轮动 Selected Rotation Score
  - Tab 3 调仓对账单 : target 由 combined_risk_budget 等比缩放推导 (不再占位)
  - Tab 4 A股情绪周期 : Sentiment Shadow (独立子项目)

数据来源（只读本地，不联网）：
  - 合并枢纽   : <repo>/../output/daily_macro_<date>.json   (内嵌 denominator/tech/theme + combined_risk_budget)
  - 科技轮动   : <repo>/../rotation_result_<date>.md 与 rotation_results/
  - A股情绪    : <repo>/output/sentiment_shadow_<date>.json
颜色遵循「涨红跌绿」的 A 股惯例。
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from datetime import datetime

import pandas as pd
import streamlit as st

# ---- 路径引导：让 core / config 包可导入 ----
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.probabilistic.softmax import (  # noqa: E402
    compute_crisis_stress_index,
    compute_regime_probabilities,
)
from core.portfolio.reconciliation import compute_actionable_diff  # noqa: E402
from core.allocation_utils import normalize_allocation  # noqa: E402
from core.schemas import FeatureSchema, RegimeName  # noqa: E402

DATA = os.path.join(REPO_ROOT, "data")
OUTPUT = os.path.join(REPO_ROOT, "output")            # macro-os/output (A股情绪)
PIPE_OUT = os.path.join(REPO_ROOT, "..", "output")    # 真实管线 output (daily_macro 等)
ROOT = os.path.dirname(REPO_ROOT)                     # tradingview/

# ---- 颜色（A 股惯例：红=风险防御，绿=机会）----
REGIME_COLOR = {
    "AI_EXPANSION": "#d4380d",
    "NARROW_LEADERSHIP": "#d48806",
    "FAST_LIQUIDITY_SHOCK": "#cf1322",
    "CASH_LIQUIDATION": "#a8071a",
}
STAGE_COLOR = {
    "S0": "#cf1322", "S1": "#d4380d", "S2": "#d48806",
    "S3": "#1677ff", "S4": "#389e0d",
}
STAGE_LABEL = {
    "S0": "见顶 (Top)", "S1": "下跌 (Decline)", "S2": "恐慌 (Panic)",
    "S3": "企稳 (Stabilize)", "S4": "反弹 (Rebound)",
}

# 战略基准风险敞口（风险资产内部相对比例；占比 0.70 → 对应 risk_budget=0.70）
BASE_RISKY = {"QQQ": 0.30, "SOXX": 0.15, "IWM": 0.10, "GLD": 0.10, "TLT": 0.05}
RISKY_SUM = sum(BASE_RISKY.values())


# ---------------------------------------------------------------------------
# 数据加载（本地）
# ---------------------------------------------------------------------------
def _date_in_name(path: str) -> str:
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", os.path.basename(path))
    return m.group(1) if m else ""


def _newest(report_dir: str, pattern: str) -> str | None:
    hits = glob.glob(os.path.join(report_dir, pattern))
    if not hits:
        return None
    return sorted(hits, key=lambda p: _date_in_name(p), reverse=True)[0]


def load_latest_daily_macro():
    """读取合并枢纽 daily_macro_<date>.json（按文件名日期取最新）。"""
    p = _newest(PIPE_OUT, "daily_macro_*.json")
    if not p:
        return None, None
    try:
        return json.load(open(p, encoding="utf-8")), _date_in_name(p)
    except Exception:
        return None, None


def load_latest_rotation():
    """读取最新科技轮动快照 md，解析 Selected Rotation Score / 状态 / 主题平均分。"""
    cands = glob.glob(os.path.join(ROOT, "rotation_result_*.md"))
    cands += glob.glob(os.path.join(ROOT, "rotation_results", "rotation_result_*.md"))
    cands = [c for c in cands if "archive" not in c]
    if not cands:
        return None
    p = sorted(cands, key=lambda x: _date_in_name(x), reverse=True)[0]
    try:
        txt = open(p, encoding="utf-8").read()
    except Exception:
        return None
    out = {"file": os.path.basename(p), "date": _date_in_name(p)}
    m = re.search(r"Selected Rotation Score.*?：\s*\*\*(?P<score>[\d.]+)\*\*\s*（状态：\*\*(?P<state>[^*]+)\*\*）", txt)
    if m:
        out["score"] = float(m.group("score"))
        out["state"] = m.group("state").strip()
    m = re.search(r"主题平均分\s*\*\*(?P<avg>[\d.]+)\*\*", txt)
    if m:
        out["avg"] = float(m.group("avg"))
    return out


def load_latest_sentiment():
    files = sorted(glob.glob(os.path.join(OUTPUT, "sentiment_shadow_*.json")))
    if not files:
        return None
    return json.load(open(files[-1], encoding="utf-8")), os.path.basename(files[-1])


def latest_csv_value(rel, default):
    p = os.path.join(DATA, rel)
    if not os.path.exists(p):
        return default, None
    try:
        df = pd.read_csv(p)
        col = df.columns[1]
        s = df[col].dropna()
        date_col = df.columns[0]
        last_date = pd.to_datetime(df[date_col]).max().date()
        return float(s.iloc[-1]), last_date
    except Exception:
        return default, None


def derive_target_from_budget(budget: float) -> dict:
    """由真实 combined_risk_budget 等比推导各资产 target 权重。

    规则：风险资产内部相对比例沿用 BASE_RISKY；总和缩放至 budget；
    CASH = 1 - budget。再用 normalize_allocation 兜底归一。
    """
    scale = budget / RISKY_SUM
    raw = {k: round(v * scale, 4) for k, v in BASE_RISKY.items()}
    raw["CASH"] = round(1.0 - budget, 4)
    return normalize_allocation(raw, allowed=set(raw.keys()), cash_asset="CASH")


def staleness_badge(quality: str | None):
    if quality in ("stale", "mixed", "degraded"):
        st.warning(f"数据质量：{quality}（非同日共振，谨慎参考）")
    elif quality == "ok":
        st.success("数据质量：ok")


# ---------------------------------------------------------------------------
# Tab 1: 宏观雷达 (Softmax 沙盘 + 真实 combined_risk_budget)
# ---------------------------------------------------------------------------
def tab_radar():
    st.header("🛰️ 宏观雷达 · Softmax + 真实风险预算")
    st.caption("上半为 4 因子 what-if 沙盘；下半为真实管线合并产出 combined_risk_budget")

    vix_d, vix_date = latest_csv_value("_vix_daily.csv", 16.73)
    dxy_d, dxy_date = latest_csv_value("_dxy_daily.csv", 120.5)
    hy_pct, hy_date = latest_csv_value("_hy_daily.csv", 2.71)

    c1, c2, c3, c4 = st.columns(4)
    vix = c1.slider("VIX (恐慌)", 8.0, 80.0, float(round(vix_d, 1)), 0.5, help=f"数据截至 {vix_date}")
    dxy = c2.slider("DXY (美元)", 90.0, 130.0, float(round(dxy_d, 1)), 0.5, help=f"数据截至 {dxy_date}")
    ovx = c3.slider("OVX (油波)", 10.0, 80.0, 30.0, 0.5, help="无本地缓存，默认 30")
    hy_bps = c4.slider("HY OAS (bps)", 200.0, 800.0, float(round(hy_pct * 100, 0)), 5.0, help=f"数据截至 {hy_date}（百分比×100）")

    features = FeatureSchema(vix=vix, dxy=dxy, ovx=ovx, hy_credit_spread=hy_bps)
    csi = compute_crisis_stress_index(vix, dxy, ovx, hy_bps)
    probs = compute_regime_probabilities(features)
    pv = {k.value: v for k, v in probs.items()}

    dom = max(probs, key=probs.get)
    dom_color = REGIME_COLOR.get(dom.value, "#333")
    m1, m2 = st.columns([1, 2])
    with m1:
        st.markdown("### 沙盘主导体制")
        st.markdown(
            f"<div style='padding:10px 14px;border-radius:8px;background:{dom_color};"
            f"color:#fff;font-size:20px;font-weight:700;text-align:center'>{dom.value}</div>",
            unsafe_allow_html=True)
        st.metric("危机压力指数 CSI", f"{csi:+.2f}")
        st.caption(f"危机总概率 ≈ {pv['CASH_LIQUIDATION'] + pv['FAST_LIQUIDITY_SHOCK']:.1%}")
    with m2:
        df = pd.DataFrame({"体制": [k.value for k in probs], "概率": [probs[k] for k in probs]}).sort_values("概率")
        st.bar_chart(df.set_index("体制")["概率"], use_container_width=True, color="#1677ff")
        for k, v in sorted(probs.items(), key=lambda x: -x[1]):
            col = REGIME_COLOR.get(k.value, "#333")
            st.markdown(f"<span style='color:{col};font-weight:600'>{k.value}</span> <span style='font-variant-numeric:tabular-nums'>{v:.1%}</span>", unsafe_allow_html=True)

    st.divider()
    st.subheader("📡 真实管线产出 (daily_macro_*.json)")
    dm, dm_date = load_latest_daily_macro()
    if dm is None:
        st.error("未找到 output/daily_macro_*.json，请先运行 run_daily_macro.py")
        return
    crb = dm.get("combined_risk_budget", {})
    syn = dm.get("synthesis", {})
    asof = dm.get("as_of", {})
    staleness_badge(syn.get("data_quality"))
    b = float(crb.get("combined_budget", 0.0))
    col_a, col_b = st.columns([1, 2])
    with col_a:
        st.metric("合并风险预算 combined_budget", f"{b:.0%}")
        st.progress(min(max(b, 0.0), 1.0))
        st.caption(f"绑定天花板：{', '.join(crb.get('binding', [])) or '—'}")
    with col_b:
        ceil = crb.get("denominator_ceiling"), crb.get("tech_ceiling"), crb.get("theme_ceiling")
        st.markdown(f"- 分母天花板 **{ceil[0]}** ｜ 减震天花板 **{ceil[1]}** ｜ 主题天花板 **{ceil[2]}**")
        st.markdown(f"- 分母 as_of **{asof.get('denominator')}** ｜ 科技 as_of **{asof.get('tech')}** ｜ 主题 as_of **{asof.get('theme')}**")
        if syn.get("equity_stress"):
            st.markdown(f"- 权益压力：**{syn.get('equity_stress')}** ｜ 减震器：{'激活' if syn.get('dampener_active') else '休眠'}")
        if syn.get("narrative"):
            st.caption(f"叙事：{syn.get('narrative')}")


# ---------------------------------------------------------------------------
# Tab 2: 主题 & 科技
# ---------------------------------------------------------------------------
def tab_theme_tech():
    st.header("🧭 主题 & 科技 · AND门 + 减震器 + 轮动")
    dm, dm_date = load_latest_daily_macro()
    rot = load_latest_rotation()
    if dm is None:
        st.error("未找到 output/daily_macro_*.json，请先运行 run_daily_macro.py")
        return

    ts = dm.get("theme_state_machine", {})
    td = dm.get("tech_dampener", {})
    dec = td.get("decision", {})

    # ---- 主题状态机 + AND 门 ----
    st.subheader("📖 主题状态机 v3.1r → AND 门")
    lvl = int(ts.get("theme_pressure_level", 0) or 0)
    lvl_color = {0: "#389e0d", 1: "#d48806", 2: "#d4380d", 3: "#a8071a"}.get(lvl, "#333")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f"<div style='padding:8px 12px;border-radius:8px;background:{lvl_color};color:#fff;font-size:18px;font-weight:700;text-align:center'>pressure_level = {lvl}</div>", unsafe_allow_html=True)
        st.caption("0=无压 1=观察 2=压力 3=强压")
    with c2:
        st.metric("主导主题", ts.get("dominant_theme") or "无主导")
        st.caption(f"risk_bias: {ts.get('risk_bias')}")
    with c3:
        gate = "可能触发" if lvl >= 2 else "休眠"
        st.metric("AND 门 (SOXX/QQQ+宏观)", gate)
        st.caption(f"pressure_override: {ts.get('pressure_override')}")
    q = (ts.get("quality") or {})
    if q.get("stale"):
        st.warning(f"主题数据 stale（as_of {q.get('as_of')}，期望 {q.get('expected_as_of')}，lag={q.get('lag_days')}）→ 禁止当作同日共振")

    # ---- 科技减震器 ----
    st.subheader("🛡️ 科技减震器 (decision_kernel)")
    c1, c2, c3, c4 = st.columns(4)
    dd = float(td.get("tech_drawdown", 0.0) or 0.0)
    dd_raw = float(td.get("tech_drawdown_raw", 0.0) or 0.0)
    with c1:
        st.metric("SOXX 20D 回撤(平滑)", f"{dd:+.1%}")
    with c2:
        st.metric("SOXX 20D 回撤(原始)", f"{dd_raw:+.1%}")
    with c3:
        st.metric("内核权威", dec.get("authority", "—"))
    with c4:
        st.metric("减震后预算", f"{float(dec.get('risk_budget', 0) or 0):.0%}")
    st.caption(f"reason_code: {dec.get('reason_code')} ｜ dampener_active: {dec.get('dampener_active')}")
    dn = (dec.get("dampener_note") or {})
    if dn:
        st.caption(f"减震明细：pre_cap={dn.get('pre_cap_budget')} → cap={dn.get('cap')} → post_cap={dn.get('post_cap_budget')}")

    # ---- 科技板块轮动 ----
    st.subheader("🔄 科技板块轮动 v2.4 (Pine)")
    if rot is None:
        st.warning("未找到 rotation_result_*.md")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            score = rot.get("score")
            sc = "#389e0d" if (score or 99) >= 50 else ("#d48806" if (score or 0) >= 30 else "#a8071a")
            st.markdown(f"<div style='padding:8px 12px;border-radius:8px;background:{sc};color:#fff;font-size:18px;font-weight:700;text-align:center'>Rotation Score = {score}</div>", unsafe_allow_html=True)
        with c2:
            st.metric("状态", rot.get("state", "—"))
        with c3:
            st.metric("主题平均分", f"{rot.get('avg', '—')}")
        st.caption(f"来源：{rot.get('file')} (as_of {rot.get('date')})")


# ---------------------------------------------------------------------------
# Tab 3: 调仓对账单 (Reconciliation)
# ---------------------------------------------------------------------------
def tab_reconciliation():
    st.header("⚖️ 调仓对账单 · Reconciliation")
    st.caption("L4.5 宪兵检查：target(由真实 combined_risk_budget 推导) vs actual，漂移>3% 才生成可执行调仓差")

    dm, _ = load_latest_daily_macro()
    if dm is None:
        st.error("未找到 output/daily_macro_*.json，使用占位 target")
        budget = 0.5
    else:
        budget = float(dm.get("combined_risk_budget", {}).get("combined_budget", 0.5) or 0.5)
    tgt = derive_target_from_budget(budget)
    assets = list(tgt.keys())
    act = {a: tgt[a] for a in assets}

    st.info(f"target 推导：combined_risk_budget = **{budget:.0%}** → 风险资产等比缩放至 {budget:.0%}，CASH = {1-budget:.0%}（规则见 derive_target_from_budget）")
    asof = (dm or {}).get("as_of", {})
    if asof:
        st.caption(f"分母 as_of {asof.get('denominator')} ｜ 科技 as_of {asof.get('tech')} ｜ 主题 as_of {asof.get('theme')}")

    with st.expander("✏️ 编辑权重（或上传 CSV 覆盖）", expanded=False):
        cols = st.columns(len(assets))
        for i, a in enumerate(assets):
            tgt[a] = cols[i].number_input(f"Target {a}", 0.0, 1.0, float(round(tgt[a], 2)), 0.01, key=f"t_{a}")
            act[a] = cols[i].number_input(f"Actual {a}", 0.0, 1.0, float(round(act[a], 2)), 0.01, key=f"a_{a}")
        up = st.file_uploader("上传 actual 权重 CSV（两列: asset,weight）", type=["csv"])
        if up:
            try:
                u = pd.read_csv(up)
                for _, row in u.iterrows():
                    if row["asset"] in act:
                        act[row["asset"]] = float(row["weight"])
                st.success("已用上传 CSV 覆盖 Actual")
            except Exception as e:
                st.error(f"CSV 解析失败: {e}")

    if st.button("🔍 计算调仓差", type="primary"):
        diff = compute_actionable_diff(tgt, act)
        if not diff:
            st.success("组合已平衡，无超过 3% 阈值的可执行调仓差。")
        else:
            rows = []
            for a in assets:
                if a in diff:
                    rows.append({"资产": a, "Target": f"{tgt[a]:.1%}", "Actual": f"{act[a]:.1%}",
                                 "Δ": f"{diff[a]:+.1%}", "动作": "加仓" if diff[a] > 0 else "减仓"})
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.info("📤 「发往 Futu」为 PRD 二期能力；当前按钮生成本地 DiffReport 文件。")
            if st.button("📄 生成 DiffReport（本地）"):
                ts = datetime.now().strftime("%Y-%m-%d_%H%M")
                lines = [f"# Macro OS 调仓对账单 · {ts}", "",
                         "| 资产 | Target | Actual | Δ | 动作 |"]
                for a in assets:
                    if a in diff:
                        lines.append(f"| {a} | {tgt[a]:.1%} | {act[a]:.1%} | {diff[a]:+.1%} | {'加仓' if diff[a] > 0 else '减仓'} |")
                out_md = os.path.join(OUTPUT, f"reconcile_report_{ts}.md")
                out_json = os.path.join(OUTPUT, f"reconcile_report_{ts}.json")
                with open(out_md, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
                with open(out_json, "w", encoding="utf-8") as f:
                    json.dump({"as_of": ts, "budget_source": budget, "target": tgt, "actual": act, "actionable_diff": diff}, f, ensure_ascii=False, indent=2)
                st.success(f"已生成 {out_md} 与 {out_json}")


# ---------------------------------------------------------------------------
# Tab 4: A股情绪周期 (Sentiment Shadow)
# ---------------------------------------------------------------------------
def tab_sentiment():
    st.header("🇨🇳 A股情绪周期 · Shadow Engine")
    data = load_latest_sentiment()
    if data is None:
        st.warning("未找到 output/sentiment_shadow_*.json，请先运行 run_sentiment_shadow.py")
        return
    s, fname = data
    st.caption(f"数据源: {fname} · as_of {s.get('as_of')} · 质量 {s.get('quality')} · 置信度 {s.get('confidence'):.2f} · 源 {s.get('source')}")

    cyc = s.get("cycle", {})
    stage = cyc.get("stage", "—")
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        color = STAGE_COLOR.get(stage, "#333")
        st.markdown(f"<div style='padding:10px 14px;border-radius:8px;background:{color};color:#fff;font-size:18px;font-weight:700;text-align:center'>{stage} · {STAGE_LABEL.get(stage, stage)}</div>", unsafe_allow_html=True)
        st.caption(f"次选: {cyc.get('stage_runner_up')} · 置信 {cyc.get('stage_confidence'):.2f}")
    with col_b:
        temp = s.get("sentiment_temperature", 0)
        st.metric("情绪温度", f"{temp:.1f}", help="0=冰点 100=沸点")
        st.progress(min(max(temp / 100.0, 0.0), 1.0))
    with col_c:
        gjd = s.get("gjd_proxy_score")
        st.metric("国家队代理分", f"{gjd:.2f}" if gjd is not None else "N/A")
        st.caption(f"干预模式: {s.get('intervention_mode')}")

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**关键信号**")
        for k, v in [("市场状态", s.get("market_regime_label")), ("跌停数", s.get("limit_down_count")),
                     ("限压指数", s.get("limit_stress")), ("去杠杆压力", s.get("deleveraging_stress_index")),
                     ("流动性传导", s.get("liquidity_transmission_score")), ("日内路径", s.get("intraday_path")),
                     ("反弹质量", s.get("bounce_quality"))]:
            if v is not None:
                st.markdown(f"- {k}: **{v}**")
    with c2:
        st.markdown("**叙事要点**")
        for b in s.get("narrative_bullets", []) or []:
            st.markdown(f"- {b}")
        st.markdown("**观察清单**")
        for w in s.get("watch_checklist", []) or []:
            st.markdown(f"- {w}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Macro OS 工作台", page_icon="🛰️", layout="wide", initial_sidebar_state="collapsed")
    st.title("🛰️ Macro OS · 个人交易员工作台 (已打通真实管线)")
    st.caption("本地纯运行 · 读取 daily_macro_*.json 合并枢纽 + rotation + sentiment_shadow · 不联网")

    tabs = st.tabs(["🛰️ 宏观雷达", "🧭 主题 & 科技", "⚖️ 调仓对账单", "🇨🇳 A股情绪周期"])
    with tabs[0]:
        tab_radar()
    with tabs[1]:
        tab_theme_tech()
    with tabs[2]:
        tab_reconciliation()
    with tabs[3]:
        tab_sentiment()


if __name__ == "__main__":
    main()
