# TradingView Desktop Monitor ops (macro-os)

## Current runtime chain

```text
TV MCP script
  -> relay log (tech snapshot events; rarely FeatureSchema)
  -> macro-liquidity sidecar (relay/logs/macro-os-features.latest.json)
  -> yfinance + FRED merge + last-good cache
  -> research-aligned mock
```

## Start order

1. Chrome CDP: `relay/start-tradingview-chart.ps1` (port 9222)
2. Feishu relay health: `http://127.0.0.1:8787/health`
3. Tech rotation alerts: `relay/start-tv-monitor.ps1` (QQQ rotation chart)
4. Macro liquidity (needs Composite studies on chart):
   `relay/start-macro-liquidity-monitor.ps1`

## Macro liquidity chart requirement

`macro-liquidity` profile is **ready** only when a chart contains one of:
- Macro Liquidity Composite
- Realtime Liquidity 1/2/3
- Front End Funding Proxy / Rates Real Yield Curve Proxy / Global Dollar Risk Proxy

Current QQQ tech-rotation layout alone is **not** enough (`macroChart: null`).

When ready, monitor writes:
- `relay/logs/macro-liquidity.latest.json`
- `relay/logs/macro-os-features.latest.json`  (FeatureSchema-compatible)

## Env toggles (macro-os)

- `MACRO_OS_TV_MACRO_SIDECAR_ENABLED=0` disable sidecar
- `MACRO_OS_TV_MACRO_SIDECAR_MAX_AGE=300`
- `MACRO_OS_YFINANCE_ENABLED` / `MACRO_OS_FRED_ENABLED` / `MACRO_OS_MACRO_CACHE_ENABLED`

## Honest field coverage

| Source | Good for |
|--------|----------|
| macro-liquidity sidecar | danger/risk score, VIX/DXY drivers if plotted |
| yfinance | VIX, DXY, nominal yields, GLD/QQQ |
| FRED | TIPS real yield, BEI, HY OAS bp |
