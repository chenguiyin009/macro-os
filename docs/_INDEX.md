---
tags: [macro-os, index]
---

# Macro OS Documentation Vault

> **Hermes System - Macro Trading Intelligence Platform**
> v4.6 | 2026-07-01

## Boot Sequence (read in order)

1. [[ARCHITECTURE]] - System evolution & design philosophy
2. [[USAGE]] - How to run the runtime pipeline and CDP pull scripts
3. [[PIPELINE]] - Complete data flow from MCP to execution
4. [[DECISION_KERNEL]] - VETO architecture & authority hierarchy
5. [[DIVERGENCE_ENGINE]] - Phase mapping, hysteresis, resonance
6. [[CDP_BRIDGE]] - Pine Script data integration
7. [[CONFIGURATION]] - thresholds.yaml & watchlist.yaml reference
8. [[MACRO_DATA_FALLBACK]] - Missing-field fallback and complement fill spec
9. [[WORKBUDDY_HANDOFF]] - Project handoff for WorkBuddy
10. [[GLOSSARY]] - All terms, enums, state labels

## Quick Reference

| Layer | Module | Authority |
|-------|--------|-----------|
| Data | tradingview_mcp + pine bridge | External |
| World Model | macro_mapper + confirmation | First Class |
| Risk Engine | divergence_score + divergence_engine | Advisory |
| Constitution | decision_kernel | **Absolute VETO** |
| Exposure | exposure_dampener | Shaping |
| Allocation | fracture_aware_sizer | Targeted |


