# Macro OS v5.0 ? Architecture Boundary Matrix (IMMUTABLE)

| Layer | Authority | Reads | Writes | Forbidden |
|-------|-----------|-------|--------|----------|
| sector_policy.yaml | Alpha Source | ? | preferred_sectors, budgets | Cannot initiate trades |
| LLM | Alpha Proposer | sector_policy.yaml, TV data | AllocationProposal | Cannot access Policy Engine internals |
| Policy Engine | Risk Constitution | LLM proposal, market_data | Approved allocation, audit_trail | No sector preference, no trading advice |
| Exposure Engine | Look-through Auditor | Portfolio weights | True exposure report | No weight modification |
| Hermes | Execution | Approved YAML | Orders | No override |

## Power Boundaries

1. **sector_policy.yaml** is the SINGLE source of truth for alpha. LLM reads it, proposes within its bounds.
2. **Policy Engine** ONLY enforces risk boundaries (Hard Caps, Turnover, VIX Escape). No alpha logic.
3. **Exposure Engine** verifies look-through concentrations. Alerts when single names exceed limits. Cannot modify weights.
4. **Hermes** executes approved YAML. No override authority.
