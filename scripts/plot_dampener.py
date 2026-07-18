"""Generate a self-contained SVG line chart comparing the three nav curves
(baseline / damped_cap_0.50 / damped_cap_0.60) with dampened days highlighted.

Reads docs/research/denominator_fusion_dampener.csv (produced by
backtest_denominator_state.py) and writes an HTML file. No external deps.
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "docs" / "research" / "denominator_fusion_dampener_chart.html"
CSV = PROJECT_ROOT / "docs" / "research" / "denominator_fusion_dampener.csv"

m = pd.read_csv(CSV, parse_dates=["date"])
cols = ["nav_risk_budget", "nav_damped_50", "nav_damped_60"]
for c in cols:
    assert c in m.columns, f"missing {c}"

# trigger days = RISK_ON where dampener lowered the budget
triggered = m["damped_50"] < m["risk_budget"]

W, H = 920, 380
PAD_L, PAD_R, PAD_T, PAD_B = 10, 10, 28, 22
plot_w = W - PAD_L - PAD_R
plot_h = H - PAD_T - PAD_B

navs = m[cols]
lo = float(navs.min().min()) * 0.985
hi = float(navs.max().max()) * 1.015


def sx(i: int) -> float:
    return PAD_L + (i / (len(m) - 1)) * plot_w


def sy(v: float) -> float:
    return PAD_T + (1 - (v - lo) / (hi - lo)) * plot_h


COLORS = {"nav_risk_budget": "#1f77b4", "nav_damped_50": "#ff7f0e", "nav_damped_60": "#9467bd"}
LABELS = {"nav_risk_budget": "基准 (不阻尼)", "nav_damped_50": "阻尼 cap=0.50", "nav_damped_60": "阻尼 cap=0.60"}

# grid + axes
svg = []
svg.append(f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="system-ui,Segoe UI,Arial">')
# trigger day vertical bands
for i in range(len(m)):
    if triggered.iloc[i]:
        svg.append(f'<line x1="{sx(i):.1f}" y1="{PAD_T}" x2="{sx(i):.1f}" y2="{PAD_T+plot_h}" stroke="#ffcccc" stroke-width="1"/>')
# horizontal gridlines (5)
for g in range(5):
    val = lo + (hi - lo) * g / 4
    y = sy(val)
    svg.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{PAD_L+plot_w}" y2="{y:.1f}" stroke="#e5e5e5" stroke-width="1"/>')
    svg.append(f'<text x="{PAD_L+2}" y="{y-3:.1f}" font-size="9" fill="#888">{val:.3f}</text>')
# polylines
for c in cols:
    pts = " ".join(f"{sx(i):.1f},{sy(m[c].iloc[i]):.1f}" for i in range(len(m)))
    svg.append(f'<polyline fill="none" stroke="{COLORS[c]}" stroke-width="1.6" points="{pts}"/>')
# axis title
svg.append(f'<text x="{PAD_L}" y="14" font-size="11" fill="#333">净值曲线对比 (nav = cumprod(1 + budget × QQQ日收益)) — 红色竖带 = 阻尼触发日 (RISK_ON + Pine 压力态)</text>')
svg.append("</svg>")

legend = "".join(
    f'<span style="color:{COLORS[c]};font-weight:600">■ {LABELS[c]}</span> &nbsp; ' for c in cols
)

html = f"""<!doctype html><html><head><meta charset="utf-8"><title>阻尼软叠加 — 净值对比</title>
<style>body{{font-family:system-ui,Segoe UI,Arial;margin:24px;color:#222}}
.card{{border:1px solid #ddd;border-radius:8px;padding:16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
h2{{margin-top:0}}.legend{{margin:8px 0;font-size:13px}}</style></head>
<body>
<h2>Macro OS × Pine v1.6 阻尼软叠加实验 — 净值曲线对比</h2>
<div class="legend">{legend}</div>
<div class="card">{''.join(svg)}</div>
<div class="card">
<h3>关键指标</h3>
<ul>
<li>窗口: 2024-10-01 .. 2026-07-17 (468 交易日)</li>
<li>Kernel RISK_ON 天数: 252 ｜ 阻尼触发: <b>82</b> 天 (美元压力 41 / 久期压力 38 / 信用传导 3)</li>
<li>最大回撤: 三方案完全相同 = <b>5.42%</b> (阻尼零改善)</li>
<li>总收益: 基准 42.1% ｜ cap0.50 40.4% ｜ cap0.60 41.8% (阻尼净牺牲收益)</li>
<li>触发日 QQQ 平均日收益: <b>+0.15%</b> (正 → 当日阻尼只是减仓在涨的日子)</li>
<li>触发日后 20 日 QQQ 累计: +0.28% vs 非触发 +0.48% (极微弱领先, 不足以补偿减仓)</li>
</ul>
<p style="color:#b00"><b>结论: 在此窗口 + QQQ 代理下, 阻尼不值得进 kernel</b> — 零回撤改善、净牺牲收益、信号仅极微弱领先。需先在含熊市/震荡窗口重验。</p>
</div>
<p style="color:#999;font-size:11px">⚠️ AI 基于公开信息整理的研究, 仅供方法学讨论, 不构成投资建议。</p>
</body></html>"""

OUT.write_text(html, encoding="utf-8")
print(f"[ok] wrote {OUT.name} ({len(html)} bytes)")
