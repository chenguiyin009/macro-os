# Research layer

| Path | Purpose |
|------|---------|
| `docs/research/2026-07-10-funding-price-weekly.md` | Weekly funding-price narrative SSOT (manual) |
| `docs/research/*-funding-price-weekly-auto.md` | Auto-generated weekly draft |
| `docs/research/2026-07-16-macro-gap-and-plan.md` | Gap analysis vs Macro OS + plan |
| `data/research/funding_price_week_*.json` | Structured weekly snapshots |
| `core/research/funding_price_quadrant.py` | Pure Q1-Q4 classifier + hard_regime hint |
| `adapters/fred.py` | Live FRED CSV macro levels (runtime fallback) |
| `core/session_hydration.py` | Vault -> previous_risk_budget / day-lock restore |
| `scripts/generate_funding_price_weekly.py` | Auto weekly pipeline |

## Runtime data chain

```text
TV MCP script -> relay log (fresh) -> FRED CSV live -> research-aligned mock
```

Env:

- `MACRO_OS_FRED_ENABLED=0` disables FRED fallback
- `HYDRATE_SESSION_FROM_VAULT` via orchestrator config (default true)

## Weekly generation

```bash
python scripts/generate_funding_price_weekly.py --source mock
python scripts/generate_funding_price_weekly.py --source fred
python scripts/generate_funding_price_weekly.py --source data/research/funding_price_week_2026-07-06.json
make weekly-report
```

Research output feeds Event payload / CIO. With `USE_RESEARCH_QUADRANT_HINT=true` (default), a high-confidence quadrant may set `hard_regime_raw` **before** ADR-001 red-line fold. It never writes kernel budgets directly and never maps Q1 alone to `LIQUIDITY_SQUEEZE`.
