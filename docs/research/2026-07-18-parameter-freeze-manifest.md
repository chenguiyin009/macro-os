# Macro OS 参数冻结纪律清单（Parameter Freeze Manifest）

**生效日期**: 2026-07-18
**关联审查**: 《Macro OS 日频全管线回测审查与架构评估报告》（评估基准日 2026-07-18）
**关联指令**: 审查报告第四节 Directive 3 — 保持参数冻结纪律（Maintain Parameter Freeze）

---

## 1. 冻结原则（Occam's Razor / Anti-Overfit）

- 本次冻结针对 **2024-10 至 2026-07** 历史路径校准的核心宏观参数与阈值。
- **严禁**基于本次日频回测结果（或任何 IS 样本）对以下任一参数做"微调"以"改善"回测曲线。
- 历史窗口是单一样本路径，过度拟合该路径会摧毁样本外（OOS）有效性。
- 只有当 **Directive 2 前向纸盘测试累积 N > 100 个真实交易日** 且出现**系统性、可解释的失效**时，
  方可由架构变更流程（非回测调参）重新审视，且须记录于 DECISION_JOURNAL。

---

## 2. 冻结参数寄存器

| 参数 | 当前值 | 位置 | 类型 | 冻结理由 |
|------|--------|------|------|----------|
| `regime.risk_on.tips_absolute_max` | 2.5 (%) | config/thresholds.yaml:12 | 硬天花板 | ZIRP 陷阱拆除 v2.0，防恶性通胀误判 |
| `regime.risk_on.tips_roc_60d_threshold` | -0.15 | config/thresholds.yaml:15 | 边际信号 | 60d 利率变化率门限 |
| `regime.risk_on.tips_percentile_2y` | 0.25 | config/thresholds.yaml:16 | 边际信号 | 2y 滚动分位门限 |
| `regime.risk_on.min_signals_required` | 1 | config/thresholds.yaml:19 | 确认数 | 留样后或收紧至 2（见注释） |
| `regime.risk_on.dxy_max` | 100.0 | config/thresholds.yaml:26 | 辅助参考 | ICE 量纲绝对水平 |
| `regime.tight_liquidity.tips_yield_min` | 0.8 | config/thresholds.yaml:28 | 水平门限 | TIGHT 入门口 |
| `regime.tight_liquidity.dxy_min` | 103.0 | config/thresholds.yaml:29 | 水平门限 | TIGHT 入口（ICE 量纲） |
| `regime.liquidity_squeeze.vix_min` | 25.0 | config/thresholds.yaml:31 | 挤压门限 | SQUEEZE 标签 |
| `regime.liquidity_squeeze.hy_credit_spread_min` | 400 (bp) | config/thresholds.yaml:32 | 挤压门限 | SQUEEZE 标签 |
| `constitution.red_lines.vix_escape_hatch` | 40.0 | config/thresholds.yaml:62 | 物理红线 | ADR-001 绝对夺权 |
| `constitution.red_lines.core_pce_max` | 3.5 (%) | config/thresholds.yaml:63 | 物理红线 | ADR-001 绝对夺权 |
| `constitution.red_lines.hy_credit_spread_bp` | 600.0 (bp) | config/thresholds.yaml:68 | 物理红线 | ADR-001 绝对夺权 |
| `safety.reduced_risk_budget` | 0.3 | config/thresholds.yaml:96 | 防御预算 | SAFETY_GATE 上限 |
| `safety.degraded_risk_budget` | 0.0 | config/thresholds.yaml:97 | 防御预算 | HARD_VETO 下限 |
| `VIX_CENTER` | 20.0 | core/probabilistic/softmax.py:17 | 概率锚点 | 概率层中心（审查 Directive 3 点名） |
| `DXY_CENTER` | 102.0 | core/probabilistic/softmax.py:17 | 概率锚点 | 概率层中心（ICE 量纲） |
| `OVX_CENTER` | 30.0 | core/probabilistic/softmax.py:17 | 概率锚点 | 概率层中心 |
| Decision Kernel 上限（TRANSITION） | 0.50 | core/decision_kernel.py | 预算上限 | 阶梯式权限映射（实际实现值） |
| Decision Kernel 上限（TIGHT_LIQUIDITY） | 0.30 | core/decision_kernel.py | 预算上限 | 阶梯式权限映射（实际实现值） |
| Decision Kernel 上限（LIQUIDITY_SQUEEZE / CASH_LIQUIDATION） | 0.00 | core/decision_kernel.py | 预算上限 | HARD_VETO 绝对清零 |
| C 档科技减震器 `TECH_DRAWDOWN_CAPS` | [(-0.13,0.35),(-0.10,0.50),(-0.07,0.65)] | core/decision_kernel.py (`compute_tech_dampener`) | SOFT 层预算封顶 | 跨 3 宏观环境(2022熊+2024-26科技)+8bps 长周期回测验证：SOXX/QQQ 双指标跑赢基准、扛住成本；绝对服从 HARD_VETO（Step 1 夺权时不封顶）。**内核仍纯函数、未改** |
| C 档迟滞带 `smoothing_lag_days`（出档慢确认期） | 2 | adapters/equity_stress_sensor.py (默认) / adapters/equity_stress.py `compute_soxx_drawdown_smoothed(lag=)` / scripts/daily_tech_dampener.py `--lag` | L1 适配器平滑 | 长周期多环境 A/B 扫描校准（lag∈{1,2,3,5}）：lag=2 在保留换手收益(1.7)的同时最不损反弹（SOXX 缺口 8.21% vs lag=5 的 15.21%）；**回撤保护与其他 lag 完全相同(-13.43%/-7.7%，因入档机制一致)**。迟滞带全在 L1 适配器，内核不感知 SOXX/迟滞 |
| `DXY_MONTHLY` 重定基锚点 | 2024-10~2026-05 月度 ICE 收盘 | scripts/backtest_regime.py:86 | 数据锚点 | DXY 重定基基准（冻结以保证可复现） |

---

## 2.1 追加：C 档科技减震器（2026-07-18 封版）

本次在既有冻结宏观参数之外，**新晋（promote）** 一组 C 档科技减震器参数入冻结寄存器（上表两行）。
性质说明，以对齐第 1 节纪律：

- 这组参数**不是对既有冻结参数的"微调"**，而是把一个已完成「多环境验证 → 阈值校准 → 迟滞带校准」三期流程的独立实验分支正式晋升为主线（依据会话内 v5.1 纪律与用户拍板）。
- `TECH_DRAWDOWN_CAPS` 三档阈值源自此前 `equity_overlay` 校准网格（C_-13_-10_-7 档：2022 触发率 46%→22.2%、468 天 SOXX 保护 -17.73%→-9.02%，保护度几乎无损）；`smoothing_lag_days=2` 源自本次长周期多环境 A/B 扫描（lag∈{1,2,3,5}，跨 923 交易日、扣 8bps），属**结构性稳健性结论**（回撤保护在所有 lag 完全一致，仅反弹捕获随 lag 递减），非对单一 IS 路径过拟合。
- 内核 `decision_kernel.py` 仍为纯函数、一行未改；减震器封顶逻辑与迟滞带平滑分别位于 kernel 的 `compute_tech_dampener` 与 L1 适配器 `EquityStressSensor`，冻结以保证主线与每日自动化（automation-1784354715650 的 `daily_tech_dampener.py`）口径一致。
- 解冻/复核仍走第 4 节 Go/No-Go 流程（前向纸盘 N≥100 + 系统性失效 + DECISION_JOURNAL）。

---

## 3. 澄清：实际预算上限 vs 审查报告表述

审查报告第四节提到 "TRANSITION 处于 0.60 上限之内 / TIGHT_LIQUIDITY 处于 0.40 上限之内"。
**实测实现值为 TRANSITION 上限 0.50、TIGHT_LIQUIDITY 上限 0.30**（见上表 kernel 上限行）。
实际实现比审查报告所述更保守——这仅强化"无超配"结论，不构成风险。冻结以**上表实现值**为准。

---

## 4. 解冻条件（Go/No-Go）

满足**全部**条件方可启动参数复核（仍走架构变更流程，非回测调参）：

1. 前向纸盘测试（Directive 2）已累积 **N ≥ 100** 个真实交易日样本；
2. OOS 预算路径与 IS 出现**系统性偏离**（非单日噪声），且能归因到具体宏观机制；
3. 变更提案写入 DECISION_JOURNAL 并经架构审查确认；
4. 任何阈值改动须同步更新本清单与 `config/thresholds.yaml` 注释。

---

> ⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。
