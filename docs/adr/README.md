# Architecture Decision Records (Macro OS)

| ID | Title | Status |
|----|-------|--------|
| 000 | [Decision Authority Map v5](./000-decision-authority-map-v5.md) | **Accepted** |
| 001 | [Physical Red Line vs SAFETY_GATE](./001-physical-red-line-vs-safety-gate.md) | **Accepted (B)** |
| — | [P0 Doc+ADR Package Design](./P0-doc-adr-package-design.md) | Design review |

## Rules

1. Behavior-changing constitutional decisions get an ADR **before** code.
2. ADRs should record options rejected, not only the winner.
3. After accept, update `PIPELINE.md` / `ARCHITECTURE.md` in the same docs PR when possible.
4. Kernel purity and four-step `audit_trail` keys are non-negotiable unless a new ADR supersedes them.
