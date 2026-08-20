# rates_cap 3年方案对比 — QQQ vs SPY

- 窗口: **2023-08-01 → 2026-08-13** (791 交易日)
- B区（rates 触发且分母非紧缩）: **21** 日 (2.7%)
- 与「久期压力」重叠诊断: `{"n_days": 933, "rates_engaged_days": 43, "duration_days": 104, "overlap_duration": 14, "rates_engaged_not_duration": 29, "rates_engaged_not_any_tight": 25, "duration_not_rates": 90, "incremental_share": 0.5814}`
- 摩擦: 换档 5bps / 次；预算滞后 1 日

## 方案说明

| ID | 含义 |
|---|---|
| A_buy_hold | 始终满仓 |
| B_kernel_only | 仅 kernel risk_budget |
| C_kernel_tech | kernel × 冻结 SOXX C-tier 减震 |
| D_kernel_denom | kernel × 分母状态 ceiling |
| E_kernel_rates | kernel × **推荐 rates_cap** |
| F_kernel_tech_rates | kernel × tech × rates（正交叠加） |
| G_full_min | kernel × tech × denom × rates |
| H/I/J | rates 更紧 / 更松 / 仅水位 |
| K/L | 纯 tech / 纯 rates 参考 |

## QQQ 全样本

| 方案 | 总收益 | 年化 | 波动 | Sharpe | 最大回撤 | Calmar | 均预算 | 约束日占比 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A_buy_hold | 94.6% | 23.7% | 20.1% | 1.18 | 22.8% | 1.04 | 1.00 | 0.0% |
| L_rates_only | 88.1% | 22.3% | 19.8% | 1.13 | 22.8% | 0.98 | 0.98 | 4.9% |
| K_tech_only | 60.2% | 16.2% | 15.8% | 1.03 | 18.4% | 0.88 | 0.88 | 25.0% |
| B_kernel_only | 33.6% | 9.7% | 12.2% | 0.79 | 11.7% | 0.83 | 0.67 | 100.0% |
| H_kernel_rates_tighter | 32.3% | 9.3% | 11.9% | 0.79 | 11.7% | 0.80 | 0.66 | 100.0% |
| J_kernel_rates_level_only | 32.6% | 9.4% | 12.1% | 0.78 | 11.7% | 0.81 | 0.66 | 100.0% |
| G_full_min | 21.2% | 6.3% | 8.2% | 0.78 | 9.5% | 0.67 | 0.45 | 100.0% |
| E_kernel_rates | 31.5% | 9.1% | 12.1% | 0.76 | 11.7% | 0.78 | 0.66 | 100.0% |
| I_kernel_rates_looser | 31.7% | 9.2% | 12.2% | 0.75 | 11.7% | 0.78 | 0.67 | 100.0% |
| D_kernel_denom | 21.5% | 6.4% | 8.6% | 0.74 | 9.3% | 0.69 | 0.46 | 100.0% |
| C_kernel_tech | 24.2% | 7.1% | 11.2% | 0.64 | 12.3% | 0.58 | 0.63 | 100.0% |
| F_kernel_tech_rates | 23.9% | 7.1% | 11.1% | 0.63 | 12.3% | 0.58 | 0.63 | 100.0% |

**Sharpe 最优**: `A_buy_hold` (Sharpe=1.176, 总收益=94.6%, MDD=22.8%)

## SPY 全样本

| 方案 | 总收益 | 年化 | 波动 | Sharpe | 最大回撤 | Calmar | 均预算 | 约束日占比 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A_buy_hold | 76.9% | 20.0% | 15.1% | 1.32 | 18.8% | 1.06 | 1.00 | 0.0% |
| L_rates_only | 70.8% | 18.6% | 14.9% | 1.25 | 18.8% | 0.99 | 0.98 | 4.9% |
| K_tech_only | 51.1% | 14.1% | 11.3% | 1.24 | 13.6% | 1.04 | 0.88 | 25.0% |
| B_kernel_only | 29.5% | 8.6% | 8.5% | 1.02 | 9.0% | 0.96 | 0.67 | 100.0% |
| H_kernel_rates_tighter | 28.3% | 8.3% | 8.3% | 1.00 | 9.0% | 0.92 | 0.66 | 100.0% |
| J_kernel_rates_level_only | 28.7% | 8.4% | 8.4% | 1.00 | 9.0% | 0.93 | 0.66 | 100.0% |
| I_kernel_rates_looser | 28.3% | 8.3% | 8.4% | 0.98 | 9.0% | 0.92 | 0.67 | 100.0% |
| E_kernel_rates | 27.9% | 8.2% | 8.4% | 0.97 | 9.0% | 0.91 | 0.66 | 100.0% |
| G_full_min | 16.6% | 5.0% | 5.7% | 0.88 | 7.5% | 0.67 | 0.45 | 100.0% |
| D_kernel_denom | 16.9% | 5.1% | 6.1% | 0.84 | 7.3% | 0.70 | 0.46 | 100.0% |
| C_kernel_tech | 21.0% | 6.3% | 7.8% | 0.81 | 9.3% | 0.67 | 0.63 | 100.0% |
| F_kernel_tech_rates | 20.9% | 6.2% | 7.8% | 0.80 | 9.3% | 0.67 | 0.63 | 100.0% |

**Sharpe 最优**: `A_buy_hold` (Sharpe=1.325, 总收益=76.9%, MDD=18.8%)

## 分年收益（关键方案）

### QQQ
| 方案 | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|
| A_buy_hold | 7.4% | 25.6% | 20.8% | 19.4% |
| B_kernel_only | 6.0% | 15.8% | 6.5% | 2.3% |
| C_kernel_tech | 4.7% | 11.0% | 5.2% | 1.6% |
| E_kernel_rates | 6.0% | 15.8% | 6.5% | 0.7% |
| F_kernel_tech_rates | 4.7% | 11.0% | 5.2% | 1.3% |
| G_full_min | 3.9% | 9.9% | 3.0% | 3.1% |
| H_kernel_rates_tighter | 6.0% | 15.8% | 6.5% | 1.3% |

### SPY
| 方案 | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|
| A_buy_hold | 4.9% | 24.9% | 17.7% | 14.7% |
| B_kernel_only | 4.0% | 15.5% | 5.4% | 2.3% |
| C_kernel_tech | 3.0% | 11.2% | 4.6% | 1.0% |
| E_kernel_rates | 4.0% | 15.5% | 5.4% | 1.1% |
| F_kernel_tech_rates | 3.0% | 11.2% | 4.6% | 0.9% |
| G_full_min | 2.9% | 9.2% | 2.4% | 1.3% |
| H_kernel_rates_tighter | 4.0% | 15.5% | 5.4% | 1.4% |

## 解读要点

1. 若 **E/F 相对 C** 在 QQQ 上 Sharpe 或 MDD 改善，且 B 区天数 > 0，说明 rates 前瞻腿有增量。
2. 若 E≈C 且 B 区≈0，说明与分母/减震高度重叠，rates 腿应降级为分母内部阈值，而不是独立 min。
3. G_full_min 通常最保守；若收益显著落后而 MDD 改善有限，生产应避免四腿过紧。
4. SPY 久期敏感度弱于 QQQ，rates 腿对 SPY 的相对价值通常更低——以 QQQ 为主决策资产。

产物: `rates_cap_3yr_qqq_spy.csv`, `rates_cap_3yr_qqq_spy.json`
