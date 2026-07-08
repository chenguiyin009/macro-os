---
tags: [macro-os, config]
---

# Configuration Reference

`thresholds.yaml` is the policy center. `watchlist.yaml` is the asset metadata layer.
The validator checks both files before runtime starts.

## thresholds.yaml

```yaml
regime:
  risk_on:
    tips_yield_roc_60d_max: -0.10
    dxy_zscore_60d_max: -1.0
    tips_yield_max: 0.5
    dxy_max: 100.0
  tight_liquidity:
    tips_yield_min: 0.8
    dxy_min: 103.0
  liquidity_squeeze:
    vix_min: 25.0
    hy_credit_spread_min: 400

scoring:
  weights:
    regime_base: 0.4
    trend_strength: 0.25
    volatility_adjust: 0.20
    liquidity_adjust: 0.15

decision:
  long_confidence_min: 0.60
  short_confidence_min: 0.65
  no_trade_confidence_max: 0.35
  reduce_threshold: 0.30

constitution:
  red_lines:
    vix_escape_hatch: 40.0
    core_pce_max: 3.5
    brent_red_line: 95.0
  execution:
    max_daily_turnover: 0.25
    min_cash_buffer: 0.05
```

### Design Rules

- `tips_yield_roc_60d_max` and `dxy_zscore_60d_max` are the preferred RISK_ON triggers.
- `tips_yield_max` and `dxy_max` remain as legacy fallback thresholds.
- `scoring.weights` must sum to `1.0`; the validator rejects anything else.
- `constitution.red_lines` covers the hard stop and secondary stress filter.
- `constitution` is the top-priority policy block for circuit breakers and execution damping.

## watchlist.yaml

```yaml
assets:
  QQQ:
    macro_sensitivity: ["RATES"]
    moat_score: 0.6
    logic_stability: 0.5
    has_active_catalyst: true
    atr_percent_20d: 0.018
    beta_to_spy: 1.15
    max_portfolio_weight: 0.40
```

### Asset Fields

| Field | Range | Description |
|-------|-------|-------------|
| macro_sensitivity | list[str] | Which macro fractures affect the asset |
| moat_score | 0.0 - 1.0 | Structural protection from macro shocks |
| logic_stability | 0.0 - 1.0 | Regime independence for sizing |
| has_active_catalyst | bool | Near-term idiosyncratic catalyst flag |
| atr_percent_20d | > 0 | 20-day ATR as a volatility input |
| beta_to_spy | any finite float | Market exposure proxy used in sizing |
| max_portfolio_weight | 0.0 - 1.0 | Hard upper bound for L4 allocation |

## Validator

Run this before starting Macro OS or after editing either YAML file:

```bash
python scripts/validate_macro_config.py
```

The validator checks:

- `thresholds.scoring.weights` sums to `1.0`
- `thresholds.constitution` is present and numeric
- every watchlist asset includes the L4 risk dimensions
- asset metadata ranges are sane
