# Macro OS — Architecture (immutable)

Pipeline: MCP -> LLM Parser -> Feature Builder -> Regime -> Score -> Decision -> Event Store -> Feishu

## Data sources

- DXY, VIX, HY Credit: MCP bridge
- Real Yield (TIPS): MCP bridge
- Gold: MCP bridge
- Equity: Tech rotation signals

## Regime definitions

- RISK_ON: TIPS down, DXY weak
- TIGHT_LIQUIDITY: TIPS up, DXY strong
- LIQUIDITY_SQUEEZE: VIX > 25
- TRANSITION: no clear signal

## Fallback chain

1. Live subprocess (MCP command, 8s timeout)
2. Relay log
3. Mock snapshot

## Key constraints

- System is fully stateless
- Event sourcing is source of truth
- Config strictly separated (pydantic-settings)
- LLM only for unstructured -> structured parsing
- Regime + scoring are pure functions
- Orchestrator = coordinator, no business logic
- Idempotent event store via sha256 event_id
