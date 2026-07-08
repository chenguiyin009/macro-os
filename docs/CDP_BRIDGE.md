---
tags: [macro-os, cdp, pine]
---

# CDP Bridge ? Pine Script Data Integration

## Architecture

```text
TV MCP ??? pine_get_source ??? Read Pine Editor content
         ?
         ???? pine_set_source ??? Inject merged 10-call script
         ?
         ???? pine_smart_compile ??? Compile + Save
         ?
         ???? chart_manage_indicator ??? Add/Remove from chart
         ?
         ???? data_get_pine_tables ??? Read table.new() output
              data_get_study_values ??? Read plot() values
```

## 10-Call Optimization

### Kept (10 calls)

| # | Symbol | Module | Purpose |
|---|--------|--------|---------|
| 1 | CME:SR31! (SOFR) | M1 | Short-end funding |
| 2 | CBOT:ZQ1! (Fed Funds) | M1 | Fed policy path |
| 3 | CBOT:ZN1! (10Y) | M2 | Treasury rates |
| 4 | AMEX:TIP (TIP) | M2 | Real yields |
| 5 | AMEX:GLD (GLD) | M2+M3 | Gold / safe haven |
| 6 | AMEX:DBC (DBC) | M2 | Commodities |
| 7 | TVC:DXY (DXY) | M1+M2+M3 | Dollar index |
| 8 | AMEX:HYG (HYG) | M3 | High yield credit |
| 9 | CBOE:VIX (VIX) | M3 | Volatility |
| 10 | AMEX:SPY (SPY) | M3 | Equities |
| ? | chart close (QQQ) | M3 | No request.security needed |

### Removed (11 calls ? out of 21 total)

ZT, ZB, SHY, BIL, USDCNH, USDJPY, JNK, LQD, VVIX, MOVE, QQQ(separate)

## Table Output Format

```text
Row 0: Front-End State | Benign/Mixed/Tightening
Row 1: SOFR Path       | -0.30 (latest ROC)
Row 2: Score           | 0-3
Row 3: Rates State     | Neutral/Real Yield Tightening/Disinflationary
Row 4: Real Yield Proxy | +0.24 (positive = tightening)
Row 5: Score           | 0-3
Row 6: Global Risk State | Calm/Watch/Risk-Off/Shock
Row 7: HY Credit Stress | -0.15 (negative = improving)
Row 8: Score           | 0-4
Row 9-11: M1/M2/M3 Scores
Row 12: Note

Study Values: SOFR Path, Fed Funds, Real Yield, BEI, HY Stress, VIX Stress
Hidden Plots: M1 Score, M2 Score, M3 Score (display.none)
```

## Degradation Factor

When Pine data is unavailable: Fracture Score ? 0.85
(Prevents missing dimensions from amplifying visible ones)
