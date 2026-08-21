# Scheme A Re-Risk Backtest

Note: target_budget <= defense_ceiling always; A cannot beat live DEFENSE_ONLY on long-only returns by construction. Value is measured primarily vs STICKY_CUT (cut-and-freeze).

- recommend_enable_step_up: **True**
- ok={'recent_gt_sticky': True, 'bear_mdd_vs_defense': True, 'a_not_far_below_defense_bull': True}

## recent_full (2023-08-01 → 2026-08-13, n=791, permit_frac=0.4109)

- delta vs DEFENSE: `{'QQQ': {'total_pp': -9.26, 'sharpe_diff': -0.234, 'mdd_pp': -2.3}, 'SPY': {'total_pp': -6.62, 'sharpe_diff': -0.202, 'mdd_pp': -1.78}}`
- delta vs STICKY: `{'QQQ': {'total_pp': 8.74, 'sharpe_diff': 0.38, 'mdd_pp': 4.58}, 'SPY': {'total_pp': 7.23, 'sharpe_diff': 0.355, 'mdd_pp': 3.72}}`
### QQQ
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | 94.6% | +0.0pp | 22.8% | 1.18 | 1.00 |
| DEFENSE_ONLY | 18.6% | -76.0pp | 9.0% | 0.74 | 0.41 |
| SCHEME_A | 9.3% | -85.3pp | 6.7% | 0.50 | 0.29 |
| STICKY_CUT | 0.6% | -94.0pp | 2.1% | 0.12 | 0.06 |

### SPY
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | 76.9% | +0.0pp | 18.8% | 1.32 | 1.00 |
| DEFENSE_ONLY | 14.8% | -62.1pp | 7.1% | 0.84 | 0.41 |
| SCHEME_A | 8.2% | -68.7pp | 5.4% | 0.64 | 0.29 |
| STICKY_CUT | 1.0% | -75.9pp | 1.7% | 0.28 | 0.06 |

## 2022_p2t (2022-01-03 → 2022-10-12, n=200, permit_frac=0.18)

- delta vs DEFENSE: `{'QQQ': {'total_pp': 5.53, 'sharpe_diff': -0.275, 'mdd_pp': -5.51}, 'SPY': {'total_pp': 3.89, 'sharpe_diff': -0.167, 'mdd_pp': -3.88}}`
- delta vs STICKY: `{'QQQ': {'total_pp': -1.85, 'sharpe_diff': -0.841, 'mdd_pp': 1.83}, 'SPY': {'total_pp': -1.21, 'sharpe_diff': -0.851, 'mdd_pp': 1.2}}`
### QQQ
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | -34.0% | +0.0pp | 34.6% | -1.28 | 1.00 |
| STICKY_CUT | -3.1% | +30.9pp | 3.5% | -1.81 | 0.02 |
| DEFENSE_ONLY | -10.4% | +23.5pp | 10.9% | -2.38 | 0.09 |
| SCHEME_A | -4.9% | +29.1pp | 5.4% | -2.65 | 0.03 |

### SPY
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | -24.1% | +0.0pp | 24.5% | -1.23 | 1.00 |
| STICKY_CUT | -1.7% | +22.4pp | 2.0% | -1.68 | 0.02 |
| DEFENSE_ONLY | -6.8% | +17.3pp | 7.1% | -2.36 | 0.09 |
| SCHEME_A | -2.9% | +21.2pp | 3.2% | -2.53 | 0.03 |

## 2022_full (2022-01-03 → 2022-12-30, n=256, permit_frac=0.2383)

- delta vs DEFENSE: `{'QQQ': {'total_pp': 5.53, 'sharpe_diff': -0.226, 'mdd_pp': -5.51}, 'SPY': {'total_pp': 3.89, 'sharpe_diff': -0.136, 'mdd_pp': -3.88}}`
- delta vs STICKY: `{'QQQ': {'total_pp': -1.85, 'sharpe_diff': -0.748, 'mdd_pp': 1.83}, 'SPY': {'total_pp': -1.21, 'sharpe_diff': -0.754, 'mdd_pp': 1.2}}`
### QQQ
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | -33.1% | +0.0pp | 35.2% | -1.03 | 1.00 |
| STICKY_CUT | -3.1% | +30.0pp | 3.5% | -1.60 | 0.01 |
| DEFENSE_ONLY | -10.4% | +22.6pp | 10.9% | -2.13 | 0.07 |
| SCHEME_A | -4.9% | +28.2pp | 5.4% | -2.35 | 0.02 |

### SPY
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | -18.2% | +0.0pp | 24.5% | -0.75 | 1.00 |
| STICKY_CUT | -1.7% | +16.5pp | 2.0% | -1.48 | 0.01 |
| DEFENSE_ONLY | -6.8% | +11.4pp | 7.1% | -2.10 | 0.07 |
| SCHEME_A | -2.9% | +15.3pp | 3.2% | -2.24 | 0.02 |

## 2022_relief_JunAug (2022-06-17 → 2022-08-16, n=43, permit_frac=0.5814)

- delta vs DEFENSE: `{'QQQ': {'total_pp': 0.0, 'sharpe_diff': nan, 'mdd_pp': 0.0}, 'SPY': {'total_pp': 0.0, 'sharpe_diff': nan, 'mdd_pp': 0.0}}`
- delta vs STICKY: `{'QQQ': {'total_pp': 0.0, 'sharpe_diff': nan, 'mdd_pp': 0.0}, 'SPY': {'total_pp': 0.0, 'sharpe_diff': nan, 'mdd_pp': 0.0}}`
### QQQ
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| STICKY_CUT | 0.0% | -22.4pp | 0.0% | nan | 0.00 |
| DEFENSE_ONLY | 0.0% | -22.4pp | 0.0% | nan | 0.00 |
| SCHEME_A | 0.0% | -22.4pp | 0.0% | nan | 0.00 |
| BUY_HOLD | 22.4% | +0.0pp | 4.9% | 9.02 | 1.00 |

### SPY
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| STICKY_CUT | 0.0% | -17.7pp | 0.0% | nan | 0.00 |
| DEFENSE_ONLY | 0.0% | -17.7pp | 0.0% | nan | 0.00 |
| SCHEME_A | 0.0% | -17.7pp | 0.0% | nan | 0.00 |
| BUY_HOLD | 17.7% | +0.0pp | 3.3% | 8.54 | 1.00 |

