# Research layer

| Path | Purpose |
|------|---------|
| `docs/research/2026-07-10-funding-price-weekly.md` | Weekly funding-price narrative SSOT |
| `docs/research/2026-07-16-macro-gap-and-plan.md` | Gap analysis vs Macro OS + plan |
| `data/research/funding_price_week_2026-07-06.json` | Structured weekly snapshot |
| `core/research/funding_price_quadrant.py` | Pure Q1-Q4 classifier + hard_regime hint |

Research output feeds Event payload / CIO. With `USE_RESEARCH_QUADRANT_HINT=true` (default), a high-confidence quadrant may set `hard_regime_raw` **before** ADR-001 red-line fold. It never writes kernel budgets directly and never maps Q1 alone to `LIQUIDITY_SQUEEZE`.
