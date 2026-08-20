# 2022 熊市窗 — QQQ / SPY 方案对比

- 数据热身: 2021-01-01 起；主评估 **2022 全年** 与 **峰值→谷底(至 2022-10-12)**
- HY OAS is monthly-anchor interpolated proxy (FRED BAMLH0A0HYM2 vintage gap for 2021-22). DXY prefers ICE DX-Y.NYB cache _2022_dxy.csv. Kernel identical to overlay_2022 decide() loop.
- 口径与 3 年窗一致: 预算 T-1，换档 5bps；熊窗主排序 **MDD改善 → excess**；反弹/牛市对照看 Sharpe/收益
- 方案 ID 同 `rates_cap_3yr`：A 满仓 / B kernel / C +tech / D +denom / E +rates / F tech+rates / G full min / H–J rates 变体 / K tech-only / L rates-only

## 2022_p2t_Jan_to_Oct12 (2022-01-03 → 2022-10-12, n=200)

- rates 触发日: **96** (48.0%)；B区(触发且分母非紧缩): **45**
- 分母状态 Top: `{'分裂/未确认': 102, '久期压力': 38, '美元压力': 33, '信用传导': 21, '分母端宽松': 6}`
- kernel regime Top: `{'LIQUIDITY_SQUEEZE': 154, 'TRANSITION': 38, 'RISK_ON': 8}`

### QQQ

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| G_full_min | -10.4% | +23.5pp | 10.9% | +23.7pp | -2.38 | -13.0% | 0.09 |
| D_kernel_denom | -11.1% | +22.9pp | 11.6% | +23.1pp | -2.45 | -13.8% | 0.09 |
| C_kernel_tech | -12.1% | +21.9pp | 12.6% | +22.1pp | -2.30 | -15.0% | 0.12 |
| F_kernel_tech_rates | -12.1% | +21.9pp | 12.6% | +22.1pp | -2.30 | -15.0% | 0.12 |
| B_kernel_only | -13.0% | +20.9pp | 13.5% | +21.2pp | -2.24 | -16.1% | 0.13 |
| E_kernel_rates | -13.0% | +20.9pp | 13.5% | +21.2pp | -2.24 | -16.1% | 0.13 |
| I_kernel_rates_looser | -13.0% | +20.9pp | 13.5% | +21.2pp | -2.24 | -16.1% | 0.13 |
| J_kernel_rates_level_only | -13.0% | +20.9pp | 13.5% | +21.2pp | -2.24 | -16.1% | 0.13 |
| H_kernel_rates_tighter | -13.4% | +20.6pp | 13.8% | +20.8pp | -2.31 | -16.5% | 0.12 |
| L_rates_only | -25.0% | +9.0pp | 26.0% | +8.6pp | -1.19 | -30.4% | 0.80 |
| K_tech_only | -33.7% | +0.3pp | 34.3% | +0.3pp | -1.75 | -40.4% | 0.67 |
| A_buy_hold | -34.0% | +0.0pp | 34.6% | +0.0pp | -1.28 | -40.7% | 1.00 |

**本窗排序第一**: `G_full_min` (excess +23.5pp, MDD改善 +23.7pp, Sharpe -2.38)

### SPY

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| G_full_min | -6.8% | +17.3pp | 7.1% | +17.4pp | -2.36 | -8.5% | 0.09 |
| D_kernel_denom | -7.4% | +16.6pp | 7.7% | +16.8pp | -2.43 | -9.3% | 0.09 |
| C_kernel_tech | -8.2% | +15.9pp | 8.4% | +16.1pp | -2.34 | -10.2% | 0.12 |
| F_kernel_tech_rates | -8.2% | +15.9pp | 8.4% | +16.1pp | -2.34 | -10.2% | 0.12 |
| B_kernel_only | -8.8% | +15.3pp | 9.0% | +15.5pp | -2.26 | -10.9% | 0.13 |
| E_kernel_rates | -8.8% | +15.3pp | 9.0% | +15.5pp | -2.26 | -10.9% | 0.13 |
| I_kernel_rates_looser | -8.8% | +15.3pp | 9.0% | +15.5pp | -2.26 | -10.9% | 0.13 |
| J_kernel_rates_level_only | -8.8% | +15.3pp | 9.0% | +15.5pp | -2.26 | -10.9% | 0.13 |
| H_kernel_rates_tighter | -9.0% | +15.0pp | 9.3% | +15.2pp | -2.34 | -11.2% | 0.12 |
| L_rates_only | -16.0% | +8.1pp | 17.4% | +7.1pp | -1.06 | -19.7% | 0.80 |
| A_buy_hold | -24.1% | +0.0pp | 24.5% | +0.0pp | -1.23 | -29.3% | 1.00 |
| K_tech_only | -24.6% | -0.5pp | 25.0% | -0.5pp | -1.74 | -29.9% | 0.67 |

**本窗排序第一**: `G_full_min` (excess +17.3pp, MDD改善 +17.4pp, Sharpe -2.36)

## 2022_full (2022-01-03 → 2022-12-30, n=256)

- rates 触发日: **121** (47.3%)；B区(触发且分母非紧缩): **63**
- 分母状态 Top: `{'分裂/未确认': 142, '久期压力': 40, '美元压力': 35, '信用传导': 31, '分母端宽松': 8}`
- kernel regime Top: `{'LIQUIDITY_SQUEEZE': 210, 'TRANSITION': 38, 'RISK_ON': 8}`

### QQQ

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| G_full_min | -10.4% | +22.6pp | 10.9% | +24.4pp | -2.13 | -10.3% | 0.07 |
| D_kernel_denom | -11.1% | +21.9pp | 11.6% | +23.7pp | -2.20 | -11.0% | 0.07 |
| C_kernel_tech | -12.1% | +20.9pp | 12.6% | +22.7pp | -2.06 | -11.9% | 0.09 |
| F_kernel_tech_rates | -12.1% | +20.9pp | 12.6% | +22.7pp | -2.06 | -11.9% | 0.09 |
| B_kernel_only | -13.0% | +20.0pp | 13.5% | +21.8pp | -2.02 | -12.8% | 0.10 |
| E_kernel_rates | -13.0% | +20.0pp | 13.5% | +21.8pp | -2.02 | -12.8% | 0.10 |
| I_kernel_rates_looser | -13.0% | +20.0pp | 13.5% | +21.8pp | -2.02 | -12.8% | 0.10 |
| J_kernel_rates_level_only | -13.0% | +20.0pp | 13.5% | +21.8pp | -2.02 | -12.8% | 0.10 |
| H_kernel_rates_tighter | -13.4% | +19.7pp | 13.8% | +21.5pp | -2.07 | -13.2% | 0.10 |
| L_rates_only | -27.7% | +5.4pp | 30.0% | +5.2pp | -1.09 | -27.3% | 0.80 |
| A_buy_hold | -33.1% | +0.0pp | 35.2% | +0.0pp | -1.03 | -32.6% | 1.00 |
| K_tech_only | -36.8% | -3.7pp | 38.1% | -2.8pp | -1.50 | -36.3% | 0.71 |

**本窗排序第一**: `G_full_min` (excess +22.6pp, MDD改善 +24.4pp, Sharpe -2.13)

### SPY

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| G_full_min | -6.8% | +11.4pp | 7.1% | +17.4pp | -2.10 | -6.7% | 0.07 |
| D_kernel_denom | -7.4% | +10.7pp | 7.7% | +16.8pp | -2.17 | -7.3% | 0.07 |
| C_kernel_tech | -8.2% | +10.0pp | 8.4% | +16.1pp | -2.09 | -8.1% | 0.09 |
| F_kernel_tech_rates | -8.2% | +10.0pp | 8.4% | +16.1pp | -2.09 | -8.1% | 0.09 |
| B_kernel_only | -8.8% | +9.4pp | 9.0% | +15.5pp | -2.02 | -8.6% | 0.10 |
| E_kernel_rates | -8.8% | +9.4pp | 9.0% | +15.5pp | -2.02 | -8.6% | 0.10 |
| I_kernel_rates_looser | -8.8% | +9.4pp | 9.0% | +15.5pp | -2.02 | -8.6% | 0.10 |
| J_kernel_rates_level_only | -8.8% | +9.4pp | 9.0% | +15.5pp | -2.02 | -8.6% | 0.10 |
| H_kernel_rates_tighter | -9.0% | +9.2pp | 9.3% | +15.2pp | -2.09 | -8.9% | 0.10 |
| L_rates_only | -14.6% | +3.5pp | 17.4% | +7.1pp | -0.79 | -14.4% | 0.80 |
| A_buy_hold | -18.2% | +0.0pp | 24.5% | +0.0pp | -0.75 | -17.9% | 1.00 |
| K_tech_only | -23.9% | -5.7pp | 25.9% | -1.4pp | -1.30 | -23.5% | 0.71 |

**本窗排序第一**: `G_full_min` (excess +11.4pp, MDD改善 +17.4pp, Sharpe -2.10)

## 2022_H1 (2022-01-03 → 2022-06-30, n=126)

- rates 触发日: **52** (41.3%)；B区(触发且分母非紧缩): **28**
- 分母状态 Top: `{'分裂/未确认': 65, '久期压力': 28, '美元压力': 18, '信用传导': 14, '分母端宽松': 1}`
- kernel regime Top: `{'LIQUIDITY_SQUEEZE': 80, 'TRANSITION': 38, 'RISK_ON': 8}`

### QQQ

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| G_full_min | -10.4% | +19.1pp | 10.9% | +21.6pp | -2.89 | -19.8% | 0.14 |
| D_kernel_denom | -11.1% | +18.4pp | 11.6% | +20.9pp | -2.98 | -21.0% | 0.15 |
| C_kernel_tech | -12.1% | +17.4pp | 12.6% | +19.9pp | -2.78 | -22.8% | 0.19 |
| F_kernel_tech_rates | -12.1% | +17.4pp | 12.6% | +19.9pp | -2.78 | -22.8% | 0.19 |
| B_kernel_only | -13.0% | +16.5pp | 13.5% | +19.0pp | -2.70 | -24.4% | 0.20 |
| E_kernel_rates | -13.0% | +16.5pp | 13.5% | +19.0pp | -2.70 | -24.4% | 0.20 |
| I_kernel_rates_looser | -13.0% | +16.5pp | 13.5% | +19.0pp | -2.70 | -24.4% | 0.20 |
| J_kernel_rates_level_only | -13.0% | +16.5pp | 13.5% | +19.0pp | -2.70 | -24.4% | 0.20 |
| H_kernel_rates_tighter | -13.4% | +16.2pp | 13.8% | +18.7pp | -2.78 | -24.9% | 0.20 |
| L_rates_only | -24.1% | +5.4pp | 26.0% | +6.4pp | -1.52 | -42.4% | 0.83 |
| K_tech_only | -30.7% | -1.1pp | 32.1% | +0.3pp | -2.19 | -51.9% | 0.66 |
| A_buy_hold | -29.5% | +0.0pp | 32.4% | +0.0pp | -1.47 | -50.4% | 1.00 |

**本窗排序第一**: `G_full_min` (excess +19.1pp, MDD改善 +21.6pp, Sharpe -2.89)

### SPY

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| G_full_min | -6.8% | +13.2pp | 7.1% | +16.0pp | -2.91 | -13.1% | 0.14 |
| D_kernel_denom | -7.4% | +12.5pp | 7.7% | +15.3pp | -3.00 | -14.3% | 0.15 |
| C_kernel_tech | -8.2% | +11.8pp | 8.4% | +14.6pp | -2.87 | -15.7% | 0.19 |
| F_kernel_tech_rates | -8.2% | +11.8pp | 8.4% | +14.6pp | -2.87 | -15.7% | 0.19 |
| B_kernel_only | -8.8% | +11.2pp | 9.0% | +14.0pp | -2.77 | -16.8% | 0.20 |
| E_kernel_rates | -8.8% | +11.2pp | 9.0% | +14.0pp | -2.77 | -16.8% | 0.20 |
| I_kernel_rates_looser | -8.8% | +11.2pp | 9.0% | +14.0pp | -2.77 | -16.8% | 0.20 |
| J_kernel_rates_level_only | -8.8% | +11.2pp | 9.0% | +14.0pp | -2.77 | -16.8% | 0.20 |
| H_kernel_rates_tighter | -9.0% | +11.0pp | 9.3% | +13.7pp | -2.86 | -17.2% | 0.20 |
| L_rates_only | -15.5% | +4.5pp | 17.4% | +5.6pp | -1.45 | -28.5% | 0.83 |
| A_buy_hold | -20.0% | +0.0pp | 23.0% | +0.0pp | -1.44 | -36.0% | 1.00 |
| K_tech_only | -22.1% | -2.1pp | 23.5% | -0.4pp | -2.26 | -39.3% | 0.66 |

**本窗排序第一**: `G_full_min` (excess +13.2pp, MDD改善 +16.0pp, Sharpe -2.91)

## 2022_H2_to_trough (2022-07-01 → 2022-10-12, n=74)

- rates 触发日: **44** (59.5%)；B区(触发且分母非紧缩): **17**
- 分母状态 Top: `{'分裂/未确认': 37, '美元压力': 15, '久期压力': 10, '信用传导': 7, '分母端宽松': 5}`
- kernel regime Top: `{'LIQUIDITY_SQUEEZE': 74}`

### QQQ

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| B_kernel_only | 0.0% | +6.3pp | 0.0% | +21.1pp | nan | 0.0% | 0.00 |
| C_kernel_tech | 0.0% | +6.3pp | 0.0% | +21.1pp | nan | 0.0% | 0.00 |
| D_kernel_denom | 0.0% | +6.3pp | 0.0% | +21.1pp | nan | 0.0% | 0.00 |
| E_kernel_rates | 0.0% | +6.3pp | 0.0% | +21.1pp | nan | 0.0% | 0.00 |
| F_kernel_tech_rates | 0.0% | +6.3pp | 0.0% | +21.1pp | nan | 0.0% | 0.00 |
| G_full_min | 0.0% | +6.3pp | 0.0% | +21.1pp | nan | 0.0% | 0.00 |
| H_kernel_rates_tighter | 0.0% | +6.3pp | 0.0% | +21.1pp | nan | 0.0% | 0.00 |
| I_kernel_rates_looser | 0.0% | +6.3pp | 0.0% | +21.1pp | nan | 0.0% | 0.00 |
| J_kernel_rates_level_only | 0.0% | +6.3pp | 0.0% | +21.1pp | nan | 0.0% | 0.00 |
| L_rates_only | -1.1% | +5.2pp | 16.1% | +5.0pp | -0.18 | -3.8% | 0.74 |
| K_tech_only | -4.3% | +2.0pp | 16.6% | +4.5pp | -0.64 | -13.9% | 0.69 |
| A_buy_hold | -6.3% | +0.0pp | 21.1% | +0.0pp | -0.72 | -19.8% | 1.00 |

**本窗排序第一**: `B_kernel_only` (excess +6.3pp, MDD改善 +21.1pp, Sharpe nan)

### SPY

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| B_kernel_only | 0.0% | +5.1pp | 0.0% | +16.7pp | nan | 0.0% | 0.00 |
| C_kernel_tech | 0.0% | +5.1pp | 0.0% | +16.7pp | nan | 0.0% | 0.00 |
| D_kernel_denom | 0.0% | +5.1pp | 0.0% | +16.7pp | nan | 0.0% | 0.00 |
| E_kernel_rates | 0.0% | +5.1pp | 0.0% | +16.7pp | nan | 0.0% | 0.00 |
| F_kernel_tech_rates | 0.0% | +5.1pp | 0.0% | +16.7pp | nan | 0.0% | 0.00 |
| G_full_min | 0.0% | +5.1pp | 0.0% | +16.7pp | nan | 0.0% | 0.00 |
| H_kernel_rates_tighter | 0.0% | +5.1pp | 0.0% | +16.7pp | nan | 0.0% | 0.00 |
| I_kernel_rates_looser | 0.0% | +5.1pp | 0.0% | +16.7pp | nan | 0.0% | 0.00 |
| J_kernel_rates_level_only | 0.0% | +5.1pp | 0.0% | +16.7pp | nan | 0.0% | 0.00 |
| L_rates_only | -0.6% | +4.5pp | 12.6% | +4.0pp | -0.12 | -2.0% | 0.74 |
| K_tech_only | -3.1% | +2.0pp | 13.2% | +3.5pp | -0.61 | -10.3% | 0.69 |
| A_buy_hold | -5.1% | +0.0pp | 16.7% | +0.0pp | -0.74 | -16.3% | 1.00 |

**本窗排序第一**: `B_kernel_only` (excess +5.1pp, MDD改善 +16.7pp, Sharpe nan)

## 2022_relief_JunAug (2022-06-17 → 2022-08-16, n=43)

- rates 触发日: **17** (39.5%)；B区(触发且分母非紧缩): **12**
- 分母状态 Top: `{'分裂/未确认': 34, '美元压力': 4, '分母端宽松': 4, '久期压力': 1}`
- kernel regime Top: `{'LIQUIDITY_SQUEEZE': 43}`

### QQQ

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| L_rates_only | 19.6% | -2.8pp | 4.2% | +0.6pp | 8.46 | 185.9% | 0.84 |
| A_buy_hold | 22.4% | +0.0pp | 4.9% | +0.0pp | 9.02 | 227.5% | 1.00 |
| B_kernel_only | 0.0% | -22.4pp | 0.0% | +4.9pp | nan | 0.0% | 0.00 |
| C_kernel_tech | 0.0% | -22.4pp | 0.0% | +4.9pp | nan | 0.0% | 0.00 |
| D_kernel_denom | 0.0% | -22.4pp | 0.0% | +4.9pp | nan | 0.0% | 0.00 |
| E_kernel_rates | 0.0% | -22.4pp | 0.0% | +4.9pp | nan | 0.0% | 0.00 |
| F_kernel_tech_rates | 0.0% | -22.4pp | 0.0% | +4.9pp | nan | 0.0% | 0.00 |
| G_full_min | 0.0% | -22.4pp | 0.0% | +4.9pp | nan | 0.0% | 0.00 |
| H_kernel_rates_tighter | 0.0% | -22.4pp | 0.0% | +4.9pp | nan | 0.0% | 0.00 |
| I_kernel_rates_looser | 0.0% | -22.4pp | 0.0% | +4.9pp | nan | 0.0% | 0.00 |
| J_kernel_rates_level_only | 0.0% | -22.4pp | 0.0% | +4.9pp | nan | 0.0% | 0.00 |
| K_tech_only | 15.9% | -6.6pp | 4.2% | +0.6pp | 6.44 | 137.0% | 0.78 |

**本窗排序第一**: `L_rates_only` (excess -2.8pp, MDD改善 +0.6pp, Sharpe 8.46)

### SPY

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| L_rates_only | 15.9% | -1.8pp | 2.1% | +1.1pp | 8.62 | 137.4% | 0.84 |
| A_buy_hold | 17.7% | +0.0pp | 3.3% | +0.0pp | 8.54 | 159.9% | 1.00 |
| B_kernel_only | 0.0% | -17.7pp | 0.0% | +3.3pp | nan | 0.0% | 0.00 |
| C_kernel_tech | 0.0% | -17.7pp | 0.0% | +3.3pp | nan | 0.0% | 0.00 |
| D_kernel_denom | 0.0% | -17.7pp | 0.0% | +3.3pp | nan | 0.0% | 0.00 |
| E_kernel_rates | 0.0% | -17.7pp | 0.0% | +3.3pp | nan | 0.0% | 0.00 |
| F_kernel_tech_rates | 0.0% | -17.7pp | 0.0% | +3.3pp | nan | 0.0% | 0.00 |
| G_full_min | 0.0% | -17.7pp | 0.0% | +3.3pp | nan | 0.0% | 0.00 |
| H_kernel_rates_tighter | 0.0% | -17.7pp | 0.0% | +3.3pp | nan | 0.0% | 0.00 |
| I_kernel_rates_looser | 0.0% | -17.7pp | 0.0% | +3.3pp | nan | 0.0% | 0.00 |
| J_kernel_rates_level_only | 0.0% | -17.7pp | 0.0% | +3.3pp | nan | 0.0% | 0.00 |
| K_tech_only | 12.9% | -4.8pp | 3.0% | +0.3pp | 6.80 | 103.6% | 0.78 |

**本窗排序第一**: `L_rates_only` (excess -1.8pp, MDD改善 +1.1pp, Sharpe 8.62)

## 2022_relief_OctDec (2022-10-12 → 2022-12-30, n=57)

- rates 触发日: **26** (45.6%)；B区(触发且分母非紧缩): **18**
- 分母状态 Top: `{'分裂/未确认': 40, '信用传导': 10, '美元压力': 3, '久期压力': 2, '分母端宽松': 2}`
- kernel regime Top: `{'LIQUIDITY_SQUEEZE': 57}`

### QQQ

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| L_rates_only | -3.7% | -5.0pp | 11.5% | +0.0pp | -0.65 | -15.2% | 0.81 |
| A_buy_hold | 1.3% | +0.0pp | 11.5% | +0.0pp | 0.19 | 6.1% | 1.00 |
| B_kernel_only | 0.0% | -1.3pp | 0.0% | +11.5pp | nan | 0.0% | 0.00 |
| C_kernel_tech | 0.0% | -1.3pp | 0.0% | +11.5pp | nan | 0.0% | 0.00 |
| D_kernel_denom | 0.0% | -1.3pp | 0.0% | +11.5pp | nan | 0.0% | 0.00 |
| E_kernel_rates | 0.0% | -1.3pp | 0.0% | +11.5pp | nan | 0.0% | 0.00 |
| F_kernel_tech_rates | 0.0% | -1.3pp | 0.0% | +11.5pp | nan | 0.0% | 0.00 |
| G_full_min | 0.0% | -1.3pp | 0.0% | +11.5pp | nan | 0.0% | 0.00 |
| H_kernel_rates_tighter | 0.0% | -1.3pp | 0.0% | +11.5pp | nan | 0.0% | 0.00 |
| I_kernel_rates_looser | 0.0% | -1.3pp | 0.0% | +11.5pp | nan | 0.0% | 0.00 |
| J_kernel_rates_level_only | 0.0% | -1.3pp | 0.0% | +11.5pp | nan | 0.0% | 0.00 |
| K_tech_only | -4.7% | -6.0pp | 10.9% | +0.6pp | -0.69 | -19.2% | 0.84 |

**本窗排序第一**: `L_rates_only` (excess -5.0pp, MDD改善 +0.0pp, Sharpe -0.65)

### SPY

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| L_rates_only | 1.4% | -6.0pp | 7.2% | +0.0pp | 0.36 | 6.3% | 0.81 |
| A_buy_hold | 7.4% | +0.0pp | 7.2% | +0.0pp | 1.54 | 37.1% | 1.00 |
| B_kernel_only | 0.0% | -7.4pp | 0.0% | +7.2pp | nan | 0.0% | 0.00 |
| C_kernel_tech | 0.0% | -7.4pp | 0.0% | +7.2pp | nan | 0.0% | 0.00 |
| D_kernel_denom | 0.0% | -7.4pp | 0.0% | +7.2pp | nan | 0.0% | 0.00 |
| E_kernel_rates | 0.0% | -7.4pp | 0.0% | +7.2pp | nan | 0.0% | 0.00 |
| F_kernel_tech_rates | 0.0% | -7.4pp | 0.0% | +7.2pp | nan | 0.0% | 0.00 |
| G_full_min | 0.0% | -7.4pp | 0.0% | +7.2pp | nan | 0.0% | 0.00 |
| H_kernel_rates_tighter | 0.0% | -7.4pp | 0.0% | +7.2pp | nan | 0.0% | 0.00 |
| I_kernel_rates_looser | 0.0% | -7.4pp | 0.0% | +7.2pp | nan | 0.0% | 0.00 |
| J_kernel_rates_level_only | 0.0% | -7.4pp | 0.0% | +7.2pp | nan | 0.0% | 0.00 |
| K_tech_only | 0.8% | -6.6pp | 7.3% | -0.2pp | 0.17 | 3.5% | 0.84 |

**本窗排序第一**: `L_rates_only` (excess -6.0pp, MDD改善 +0.0pp, Sharpe 0.36)

## 2021_bull_control (2021-01-04 → 2021-12-31, n=252)

- rates 触发日: **0** (0.0%)；B区(触发且分母非紧缩): **0**
- 分母状态 Top: `{'分裂/未确认': 134, '预热中': 62, '信用传导': 22, '美元压力': 18, '分母端宽松': 11, '久期压力': 5}`
- kernel regime Top: `{'TRANSITION': 176, 'RISK_ON': 55, 'LIQUIDITY_SQUEEZE': 21}`

### QQQ

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| A_buy_hold | 28.6% | +0.0pp | 10.8% | +0.0pp | 1.58 | 28.7% | 1.00 |
| L_rates_only | 28.6% | +0.0pp | 10.8% | +0.0pp | 1.58 | 28.7% | 1.00 |
| K_tech_only | 16.9% | -11.7pp | 10.8% | +0.1pp | 1.03 | 17.0% | 0.93 |
| G_full_min | 2.7% | -25.9pp | 6.7% | +4.1pp | 0.38 | 2.7% | 0.41 |
| D_kernel_denom | 2.6% | -26.1pp | 6.7% | +4.1pp | 0.36 | 2.6% | 0.41 |
| B_kernel_only | 2.3% | -26.3pp | 8.0% | +2.9pp | 0.28 | 2.3% | 0.47 |
| E_kernel_rates | 2.3% | -26.3pp | 8.0% | +2.9pp | 0.28 | 2.3% | 0.47 |
| H_kernel_rates_tighter | 2.3% | -26.3pp | 8.0% | +2.9pp | 0.28 | 2.3% | 0.47 |
| I_kernel_rates_looser | 2.3% | -26.3pp | 8.0% | +2.9pp | 0.28 | 2.3% | 0.47 |
| J_kernel_rates_level_only | 2.3% | -26.3pp | 8.0% | +2.9pp | 0.28 | 2.3% | 0.47 |
| C_kernel_tech | 2.3% | -26.4pp | 8.1% | +2.8pp | 0.27 | 2.3% | 0.47 |
| F_kernel_tech_rates | 2.3% | -26.4pp | 8.1% | +2.8pp | 0.27 | 2.3% | 0.47 |

**本窗排序第一**: `A_buy_hold` (excess +0.0pp, MDD改善 +0.0pp, Sharpe 1.58)

### SPY

| 方案 | 总收益 | vs满仓 | MDD | MDD改善 | Sharpe | 年化 | 均预算 |
|---|---:|---:|---:|---:|---:|---:|---:|
| A_buy_hold | 30.5% | +0.0pp | 5.1% | +0.0pp | 2.37 | 30.6% | 1.00 |
| L_rates_only | 30.5% | +0.0pp | 5.1% | +0.0pp | 2.37 | 30.6% | 1.00 |
| K_tech_only | 20.5% | -10.0pp | 5.1% | +0.0pp | 1.73 | 20.6% | 0.93 |
| C_kernel_tech | 3.4% | -27.1pp | 4.6% | +0.5pp | 0.59 | 3.5% | 0.47 |
| F_kernel_tech_rates | 3.4% | -27.1pp | 4.6% | +0.5pp | 0.59 | 3.5% | 0.47 |
| B_kernel_only | 3.4% | -27.1pp | 4.5% | +0.6pp | 0.58 | 3.4% | 0.47 |
| E_kernel_rates | 3.4% | -27.1pp | 4.5% | +0.6pp | 0.58 | 3.4% | 0.47 |
| H_kernel_rates_tighter | 3.4% | -27.1pp | 4.5% | +0.6pp | 0.58 | 3.4% | 0.47 |
| I_kernel_rates_looser | 3.4% | -27.1pp | 4.5% | +0.6pp | 0.58 | 3.4% | 0.47 |
| J_kernel_rates_level_only | 3.4% | -27.1pp | 4.5% | +0.6pp | 0.58 | 3.4% | 0.47 |
| G_full_min | 2.8% | -27.7pp | 3.6% | +1.5pp | 0.57 | 2.8% | 0.41 |
| D_kernel_denom | 2.7% | -27.9pp | 3.6% | +1.5pp | 0.54 | 2.7% | 0.41 |

**本窗排序第一**: `A_buy_hold` (excess +0.0pp, MDD改善 +0.0pp, Sharpe 2.37)

## 结论（2022）

### QQQ

- **峰值→谷底**最优: `G_full_min` (收益 -10.4%, excess +23.5pp, MDD改善 +23.7pp)
- **2022全年**最优(同排序): `G_full_min` (收益 -10.4%, excess +22.6pp)
- E vs B (p2t): excess +20.9 vs +20.9pp；MDD改善 +21.2 vs +21.2pp → E未优于B / 持平
- E vs C (p2t): excess +20.9 vs +21.9pp；MDD 13.5% vs 12.6%
- G/D (p2t): G excess +23.5pp / D +22.9pp
- 纯 rates L vs 满仓 A (p2t): -25.0% vs -34.0% (excess +9.0pp)

### SPY

- **峰值→谷底**最优: `G_full_min` (收益 -6.8%, excess +17.3pp, MDD改善 +17.4pp)
- **2022全年**最优(同排序): `G_full_min` (收益 -6.8%, excess +11.4pp)
- E vs B (p2t): excess +15.3 vs +15.3pp；MDD改善 +15.5 vs +15.5pp → E未优于B / 持平
- E vs C (p2t): excess +15.3 vs +15.9pp；MDD 9.0% vs 8.4%
- G/D (p2t): G excess +17.3pp / D +16.6pp
- 纯 rates L vs 满仓 A (p2t): -16.0% vs -24.1% (excess +8.1pp)

### 读法

1. 2022 是利率+美元+信用同时施压的真熊，kernel/分母本身就会大幅降仓；看 rates 是否**额外**有用。
2. 若 E≈B 且 L 接近 A，说明 rates 腿在 2022 仍非主保护来源。
3. 反弹窗（Jun–Aug / Oct–Dec）若 G/D 明显拖累 Sharpe，说明过紧约束有空头踏空成本。
4. 与 2023-26 回撤并集对比：2022 才是 rates 第一性叙事（折现率熊）的主考场。

产物: `rates_cap_2022_bear_qqq_spy.csv`, `rates_cap_2022_bear_qqq_spy.json`
