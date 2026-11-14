# 四腿宏观 · 近一年回测摘要（本地纸上，未改生产）

**报告日**：2026-11-09｜**数据截至**：2026-11-06｜**未改生产 C / four-leg min / pine**

## 结论

- **近一年（约 2025-11-06→2026-11-06）**：内核+科技减震器代理（非完整四腿）QQQ 净收益约 **17.34%**（年化 16.83%｜最大回撤 -4.36%｜Sharpe 1.63），同期 QQQ 买入持有 **5.76%**（回撤 -14.82%）。

- **完整四腿实盘快照纸上（2026-09-01→2026-11-05，仅 44 日）**：combined×次日QQQ 净约 **-2.44%**，同期买入持有 -6.04%（超额 3.6pp）。均值预算 0.2966。

## 回测设定

1. **近一年代理**：`decide()` 风险预算 + C 档科技减震器（SMH 20日峰值回撤）；仓位=预算×标的日收益，闲置现金 0%，换手 10bps。

2. **完整四腿近窗**：读取 `output/daily_macro_*.json` 的 `combined_budget=min(分母,减震器,主题,NQ)`；T 日收盘预算 × T+1 收益。

3. **标的**：QQQ / SMH / SPX；**非** Gy 只做空 NQ 的实盘损益。

## 核心数字

### A. 近一年 · 内核+减震器（扩展窗口本地复跑）

- 窗口：2025-11-06 ~ 2026-11-06（259 交易日）

- 减震器激活：23 日（8.9%）；预算均值 基线 0.5695 → 减震 0.5432

| 标的 | 策略净总收益 | 年化 | 最大回撤 | Sharpe | 买入持有总收益 | 买入持有回撤 |
|---|---:|---:|---:|---:|---:|---:|
| QQQ | 17.34% | 16.83% | -4.36% | 1.63 | 5.76% | -14.82% |
| SMH | 53.08% | 51.33% | -9.78% | 2.36 | 29.46% | -33.07% |
| SPX | 11.57% | 11.24% | -2.52% | 1.8 | 8.54% | -9.1% |

### B. 完整四腿 · 日更快照纸上（生产 combined）

- 窗口：2026-09-01 ~ 2026-11-05（44 日）｜绑定分布：{'分母': 21, 'NQ动因': 16, '减震器': 7}

| 标的 | 四腿纸上总收益 | 年化 | 最大回撤 | Sharpe | 买入持有 | 超额pp |
|---|---:|---:|---:|---:|---:|---:|
| QQQ | -2.44% | -13.17% | -5.53% | -1.51 | -6.04% | 3.6 |
| SMH | -5.81% | -29.0% | -11.65% | -1.74 | -11.79% | 5.98 |
| SPX | -1.2% | -6.68% | -2.47% | -1.24 | -3.42% | 2.22 |

### C. 仓库既有产物（对照，未重跑改写）

- `docs/research/backtest_last_year_dampener.md`：2025-07-18~2026-07-17；减震后 SOXX 净约 +98%、QQQ 净约 +25%（相对无减震器基线改善；现金=0%）。

- `docs/research/scheme_a_re_risk_backtest.md`：2023-08-01~2026-08-13 DEFENSE_ONLY≈min(kernel,tech,denom) QQQ 总收益约 +23.8% vs 买入持有 +94.6%（防守上限天然跑输牛市）。

## 与当前晨报口径

近窗完整四腿均值预算约 0.30，最近数日 binding=减震器、combined=0.35，与 2026-11-06 晨报「combined=0.35、绑科技减震器、增长恐慌/结构恶化」一致；近一年代理不能替代四腿 min。

## 局限与声明

- Full production four-leg combined_budget history on disk only covers 2026-09-01..2026-11-06 (48 files).

- Near-year panel uses kernel+tech dampener only (theme/NQ ceilings not reconstructed historically).

- Feature frame DXY FRED ends 2026-10-30; later days may be thinner / ffilled.

- SOXX local _eq_sox.csv broken on 2026-11-05/06; SMH used as tech proxy.

- Cash residual = 0% overstates timing drag vs buy&hold; compare relative numbers.

- Gy live style is short-NQ only; this paper long exposure = combined * asset. Not Gy short PnL.


教学/数据参考，不构成投资建议。四工具交叉验证，非交易信号。
