# PPO Backtest — BASE vs flag vs soft (QQQ/SPY)

Tightened DETERIOR: require LH/LL + ret20<=-5%; deteriorate_confirm_days=3.

- Friction: 5bps toggle; budget lag-1
- **Verdict:** signal_ok=False soft_bear_ok=True soft_full_ok=True → **recommend `flag_only`**
- Notes:
  - recent_full_2023_2026: det_fwd5 0.0547 > non 0.0043
  - recent_full_2023_2026: det_fwd20 0.0887 > non 0.0180
  - recent_drawdown_days: det_fwd5 0.0341 > non -0.0141
  - recent_drawdown_days: det_fwd20 -0.0232 > non -0.0272
  - 2022_p2t: det_fwd5 -0.0054 > non -0.0093
  - 2022_full: det_fwd5 -0.0064 > non -0.0068

## recent_full_2023_2026 (2023-08-01 → 2026-08-13, n=791)

- path_counts: `{'MIXED': 562, 'REPAIR': 223, 'DETERIOR': 6}`
- signal DETERIOR days=6 fwd5 det/non=0.05469847399183362/0.0042651871346379095 fwd20 det/non=0.08869656272788491/0.018043222082459818

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

- path_counts: `{'MIXED': 71, 'DETERIOR': 7, 'REPAIR': 2}`
- signal DETERIOR days=7 fwd5 det/non=0.03412456950151437/-0.014091746713863146 fwd20 det/non=-0.02315874828803347/-0.027247979111810237

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

- path_counts: `{'MIXED': 126, 'REPAIR': 40, 'DETERIOR': 34}`
- signal DETERIOR days=34 fwd5 det/non=-0.005441232122131946/-0.009258120551219448 fwd20 det/non=-0.04555181652691622/-0.026733615466273033

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

- path_counts: `{'MIXED': 173, 'REPAIR': 45, 'DETERIOR': 38}`
- signal DETERIOR days=38 fwd5 det/non=-0.006431341602635646/-0.0068412214706656885 fwd20 det/non=-0.03680781750009049/-0.019272456293723515

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

