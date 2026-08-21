# PPO Backtest — BASE vs flag vs soft (QQQ/SPY)

- Friction: 5bps toggle; budget lag-1
- **Verdict:** signal_ok=False soft_bear_ok=True soft_full_ok=True → **recommend `flag_only`**
- Notes:
  - recent_full_2023_2026: det_fwd20 0.0210 > non 0.0185
  - recent_drawdown_days: det_fwd5 -0.0024 > non -0.0139
  - recent_drawdown_days: det_fwd20 -0.0235 > non -0.0296
  - 2022_p2t: det_fwd5 -0.0042 > non -0.0121
  - 2022_p2t: det_fwd20 -0.0101 > non -0.0407
  - 2022_full: det_fwd5 -0.0002 > non -0.0110
  - 2022_full: det_fwd20 -0.0057 > non -0.0332

## recent_full_2023_2026 (2023-08-01 → 2026-08-13, n=791)

- path_counts: `{'MIXED': 515, 'REPAIR': 249, 'DETERIOR': 27}`
- signal DETERIOR days=27 fwd5 det/non=0.0018508132635724627/0.004749756061728741 fwd20 det/non=0.021014995327796434/0.01851188925771335

### QQQ

| scheme | total | excess | MDD | MDD imp | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|---:|
| A_buy_hold | 94.6% | +0.0pp | 22.8% | +0.0pp | 1.18 | 1.00 |
| CTRL_E | 31.5% | -63.1pp | 11.7% | +11.1pp | 0.76 | 0.66 |
| BASE | 18.6% | -76.0pp | 9.0% | +13.8pp | 0.74 | 0.41 |
| PPO_flag | 18.6% | -76.0pp | 9.0% | +13.8pp | 0.74 | 0.41 |
| PPO_soft | 18.6% | -76.0pp | 9.0% | +13.8pp | 0.74 | 0.41 |
| CTRL_G | 18.6% | -76.0pp | 9.0% | +13.8pp | 0.74 | 0.41 |

### SPY

| scheme | total | excess | MDD | MDD imp | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|---:|
| A_buy_hold | 76.9% | +0.0pp | 18.8% | +0.0pp | 1.32 | 1.00 |
| CTRL_E | 27.9% | -49.0pp | 9.0% | +9.8pp | 0.97 | 0.66 |
| BASE | 14.8% | -62.1pp | 7.1% | +11.6pp | 0.84 | 0.41 |
| PPO_flag | 14.8% | -62.1pp | 7.1% | +11.6pp | 0.84 | 0.41 |
| PPO_soft | 14.8% | -62.1pp | 7.1% | +11.6pp | 0.84 | 0.41 |
| CTRL_G | 14.8% | -62.1pp | 7.1% | +11.6pp | 0.84 | 0.41 |

## recent_drawdown_days (2023-10-25 → 2026-07-30, n=80)

- path_counts: `{'MIXED': 50, 'DETERIOR': 28, 'REPAIR': 2}`
- signal DETERIOR days=28 fwd5 det/non=-0.00236981398237014/-0.013893872309058074 fwd20 det/non=-0.023523651856095607/-0.029612246217859368

### QQQ

| scheme | total | excess | MDD | MDD imp | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|---:|
| BASE | -10.0% | +6.8pp | 10.5% | +13.2pp | -3.00 | 0.27 |
| PPO_flag | -10.0% | +6.8pp | 10.5% | +13.2pp | -3.00 | 0.27 |
| PPO_soft | -10.0% | +6.8pp | 10.5% | +13.2pp | -3.00 | 0.27 |
| CTRL_G | -10.0% | +6.8pp | 10.5% | +13.2pp | -3.00 | 0.27 |
| CTRL_E | -13.9% | +2.8pp | 13.7% | +10.0pp | -2.60 | 0.41 |
| A_buy_hold | -16.7% | +0.0pp | 23.7% | +0.0pp | -1.16 | 1.00 |

### SPY

| scheme | total | excess | MDD | MDD imp | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|---:|
| BASE | -7.6% | +4.4pp | 7.8% | +12.2pp | -2.99 | 0.27 |
| PPO_flag | -7.6% | +4.4pp | 7.8% | +12.2pp | -2.99 | 0.27 |
| PPO_soft | -7.6% | +4.4pp | 7.8% | +12.2pp | -2.99 | 0.27 |
| CTRL_G | -7.6% | +4.4pp | 7.8% | +12.2pp | -2.99 | 0.27 |
| CTRL_E | -10.4% | +1.5pp | 10.3% | +9.7pp | -2.69 | 0.41 |
| A_buy_hold | -11.9% | +0.0pp | 20.0% | +0.0pp | -1.04 | 1.00 |

## 2022_p2t (2022-01-03 → 2022-10-12, n=200)

- path_counts: `{'DETERIOR': 91, 'MIXED': 56, 'REPAIR': 53}`
- signal DETERIOR days=91 fwd5 det/non=-0.004235291970312347/-0.012065514494321496 fwd20 det/non=-0.010055540438789166/-0.040704908549235704

### QQQ

| scheme | total | excess | MDD | MDD imp | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|---:|
| BASE | -10.4% | +23.5pp | 10.9% | +23.7pp | -2.38 | 0.09 |
| PPO_flag | -10.4% | +23.5pp | 10.9% | +23.7pp | -2.38 | 0.09 |
| PPO_soft | -10.4% | +23.5pp | 10.9% | +23.7pp | -2.38 | 0.09 |
| CTRL_G | -10.4% | +23.5pp | 10.9% | +23.7pp | -2.38 | 0.09 |
| CTRL_E | -13.0% | +20.9pp | 13.5% | +21.2pp | -2.24 | 0.13 |
| A_buy_hold | -34.0% | +0.0pp | 34.6% | +0.0pp | -1.28 | 1.00 |

### SPY

| scheme | total | excess | MDD | MDD imp | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|---:|
| BASE | -6.8% | +17.3pp | 7.1% | +17.4pp | -2.36 | 0.09 |
| PPO_flag | -6.8% | +17.3pp | 7.1% | +17.4pp | -2.36 | 0.09 |
| PPO_soft | -6.8% | +17.3pp | 7.1% | +17.4pp | -2.36 | 0.09 |
| CTRL_G | -6.8% | +17.3pp | 7.1% | +17.4pp | -2.36 | 0.09 |
| CTRL_E | -8.8% | +15.3pp | 9.0% | +15.5pp | -2.26 | 0.13 |
| A_buy_hold | -24.1% | +0.0pp | 24.5% | +0.0pp | -1.23 | 1.00 |

## 2022_full (2022-01-03 → 2022-12-30, n=256)

- path_counts: `{'DETERIOR': 103, 'MIXED': 95, 'REPAIR': 58}`
- signal DETERIOR days=103 fwd5 det/non=-0.00021799625726238067/-0.010992464316420609 fwd20 det/non=-0.005704702360247518/-0.033227834311818624

### QQQ

| scheme | total | excess | MDD | MDD imp | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|---:|
| BASE | -10.4% | +22.6pp | 10.9% | +24.4pp | -2.13 | 0.07 |
| PPO_flag | -10.4% | +22.6pp | 10.9% | +24.4pp | -2.13 | 0.07 |
| PPO_soft | -10.4% | +22.6pp | 10.9% | +24.4pp | -2.13 | 0.07 |
| CTRL_G | -10.4% | +22.6pp | 10.9% | +24.4pp | -2.13 | 0.07 |
| CTRL_E | -13.0% | +20.0pp | 13.5% | +21.8pp | -2.02 | 0.10 |
| A_buy_hold | -33.1% | +0.0pp | 35.2% | +0.0pp | -1.03 | 1.00 |

### SPY

| scheme | total | excess | MDD | MDD imp | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|---:|
| BASE | -6.8% | +11.4pp | 7.1% | +17.4pp | -2.10 | 0.07 |
| PPO_flag | -6.8% | +11.4pp | 7.1% | +17.4pp | -2.10 | 0.07 |
| PPO_soft | -6.8% | +11.4pp | 7.1% | +17.4pp | -2.10 | 0.07 |
| CTRL_G | -6.8% | +11.4pp | 7.1% | +17.4pp | -2.10 | 0.07 |
| CTRL_E | -8.8% | +9.4pp | 9.0% | +15.5pp | -2.02 | 0.10 |
| A_buy_hold | -18.2% | +0.0pp | 24.5% | +0.0pp | -0.75 | 1.00 |

## Interpretation

1. PPO_flag budgets equal BASE by construction — value is gate/advice + signal stats.
2. PPO_soft only recommended if verdict.recommend_mode == soft_cap.
3. CTRL_E/G are negative controls; do not adopt if they only win on MDD with large full-sample drag.

