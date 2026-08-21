# Scheme A Re-Risk Backtest

Note: target_budget <= defense_ceiling always; A cannot beat live DEFENSE_ONLY on long-only returns by construction. Value is measured primarily vs STICKY_CUT (cut-and-freeze).

- recommend_enable_step_up: **True**
- ok={'recent_gt_sticky': True, 'bear_mdd_vs_defense': True, 'a_not_far_below_defense_bull': True}

## recent_full (2023-08-01 → 2026-08-13, n=791, permit_frac=0.4109)

- delta vs DEFENSE: `{'QQQ': {'total_pp': -4.73, 'sharpe_diff': -0.027, 'mdd_pp': -1.55}, 'SPY': {'total_pp': -5.37, 'sharpe_diff': -0.128, 'mdd_pp': -1.14}}`
- delta vs STICKY: `{'QQQ': {'total_pp': 12.72, 'sharpe_diff': -0.105, 'mdd_pp': 5.62}, 'SPY': {'total_pp': 8.41, 'sharpe_diff': -0.233, 'mdd_pp': 4.76}}`
### QQQ
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | 94.6% | +0.0pp | 22.8% | 1.18 | 1.00 |
| STICKY_CUT | 6.4% | -88.2pp | 2.5% | 0.96 | 0.10 |
| DEFENSE_ONLY | 23.8% | -70.8pp | 9.7% | 0.88 | 0.43 |
| SCHEME_A | 19.1% | -75.5pp | 8.1% | 0.85 | 0.36 |

### SPY
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | 76.9% | +0.0pp | 18.8% | 1.32 | 1.00 |
| STICKY_CUT | 5.5% | -71.4pp | 2.0% | 1.12 | 0.10 |
| DEFENSE_ONLY | 19.3% | -57.6pp | 7.9% | 1.01 | 0.43 |
| SCHEME_A | 13.9% | -62.9pp | 6.8% | 0.88 | 0.36 |

## 2022_p2t (2022-01-03 → 2022-10-12, n=200, permit_frac=0.18)

- delta vs DEFENSE: `{'QQQ': {'total_pp': 3.87, 'sharpe_diff': 0.369, 'mdd_pp': -2.47}, 'SPY': {'total_pp': 3.05, 'sharpe_diff': 0.482, 'mdd_pp': -1.8}}`
- delta vs STICKY: `{'QQQ': {'total_pp': -2.56, 'sharpe_diff': 0.241, 'mdd_pp': 3.93}, 'SPY': {'total_pp': -1.5, 'sharpe_diff': 0.243, 'mdd_pp': 2.73}}`
### QQQ
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | -34.0% | +0.0pp | 34.6% | -1.28 | 1.00 |
| SCHEME_A | -7.6% | +26.3pp | 9.5% | -1.47 | 0.19 |
| STICKY_CUT | -5.1% | +28.9pp | 5.5% | -1.71 | 0.10 |
| DEFENSE_ONLY | -11.5% | +22.5pp | 11.9% | -1.84 | 0.23 |

### SPY
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | -24.1% | +0.0pp | 24.5% | -1.23 | 1.00 |
| SCHEME_A | -4.6% | +19.5pp | 6.1% | -1.24 | 0.19 |
| STICKY_CUT | -3.1% | +21.0pp | 3.4% | -1.48 | 0.10 |
| DEFENSE_ONLY | -7.6% | +16.4pp | 7.9% | -1.72 | 0.23 |

## 2022_full (2022-01-03 → 2022-12-30, n=256, permit_frac=0.2383)

- delta vs DEFENSE: `{'QQQ': {'total_pp': 3.0, 'sharpe_diff': 0.157, 'mdd_pp': -2.78}, 'SPY': {'total_pp': 2.26, 'sharpe_diff': 0.243, 'mdd_pp': -1.8}}`
- delta vs STICKY: `{'QQQ': {'total_pp': -3.54, 'sharpe_diff': -0.028, 'mdd_pp': 3.86}, 'SPY': {'total_pp': -1.95, 'sharpe_diff': -0.074, 'mdd_pp': 2.73}}`
### QQQ
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | -33.1% | +0.0pp | 35.2% | -1.03 | 1.00 |
| STICKY_CUT | -4.9% | +28.2pp | 5.6% | -1.33 | 0.10 |
| SCHEME_A | -8.4% | +24.7pp | 9.5% | -1.36 | 0.18 |
| DEFENSE_ONLY | -11.4% | +21.7pp | 12.2% | -1.51 | 0.22 |

### SPY
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | -18.2% | +0.0pp | 24.5% | -0.75 | 1.00 |
| STICKY_CUT | -2.3% | +15.9pp | 3.4% | -0.88 | 0.10 |
| SCHEME_A | -4.3% | +13.9pp | 6.1% | -0.96 | 0.18 |
| DEFENSE_ONLY | -6.5% | +11.7pp | 7.9% | -1.20 | 0.22 |

## 2022_relief_JunAug (2022-06-17 → 2022-08-16, n=43, permit_frac=0.5814)

- delta vs DEFENSE: `{'QQQ': {'total_pp': 0.0, 'sharpe_diff': 0.0, 'mdd_pp': 0.0}, 'SPY': {'total_pp': 0.0, 'sharpe_diff': 0.0, 'mdd_pp': 0.0}}`
- delta vs STICKY: `{'QQQ': {'total_pp': 0.0, 'sharpe_diff': 0.0, 'mdd_pp': 0.0}, 'SPY': {'total_pp': 0.0, 'sharpe_diff': 0.0, 'mdd_pp': 0.0}}`
### QQQ
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | 22.4% | +0.0pp | 4.9% | 9.02 | 1.00 |
| STICKY_CUT | 4.2% | -18.2pp | 1.0% | 5.44 | 0.20 |
| DEFENSE_ONLY | 4.2% | -18.2pp | 1.0% | 5.44 | 0.20 |
| SCHEME_A | 4.2% | -18.2pp | 1.0% | 5.44 | 0.20 |

### SPY
| scheme | total | excess | MDD | Sharpe | mean_b |
|---|---:|---:|---:|---:|---:|
| BUY_HOLD | 17.7% | +0.0pp | 3.3% | 8.54 | 1.00 |
| STICKY_CUT | 3.4% | -14.3pp | 0.7% | 5.72 | 0.20 |
| DEFENSE_ONLY | 3.4% | -14.3pp | 0.7% | 5.72 | 0.20 |
| SCHEME_A | 3.4% | -14.3pp | 0.7% | 5.72 | 0.20 |

