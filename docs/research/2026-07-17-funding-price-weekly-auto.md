# 资金价格与风险资产周报（自动生成）

- **周期**：2026-07-13 — 2026-07-17
- **生成时间**：2026-07-16T15:11:21.493967Z
- **研究象限**：Q1_STRESS_TEST（压力测试）
- **hard_regime_hint**：`TIGHT_LIQUIDITY`
- **数据源**：fred+yfinance

## 核心判断

Q1 stress test: duration re-pricing; credit/usd not confirming systemic squeeze

## 资金价格读数

| 变量 | 水平 | 5D变化(bp) |
|------|------|------------|
| 10Y TIPS | 2.33 | 3.0 |
| 10Y 名义 | 4.58 | 3.0 |
| 30Y 名义 | 5.08 | 3.0 |
| 2Y 名义 | 4.18 |  |
| 10Y BEI | 2.23 |  |
| DXY(代理) | 120.5046 |  |
| HY OAS(bp) | 271.0 |  |
| VIX | 15.67 |  |

## 研究层细节

- 真实利率方向：`up`
- 名义利率方向：`up`
- 传导层：`duration_valuation`
- 信用稳定：`True` / 美元突破：`False`
- 主导驱动：tips_10y_real, ust_30y_nominal

## 说明

本文件由 `scripts/generate_funding_price_weekly.py` 自动生成，属于研究层 SSOT 草稿。
不直接改写 `decide()` 预算；Q1 默认 hint 为 TIGHT_LIQUIDITY，而非 LIQUIDITY_SQUEEZE。
