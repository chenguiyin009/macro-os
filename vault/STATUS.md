# Macro OS — Status

**Version:** v2.0 (stable)
**Last compiled:** 2026-06-30

## Current State

- Regime: TRANSITION
- Risk Score: 0.0
- Confidence: 0.0
- Action: NO_TRADE
- Data Source: mock

## Component Health

| Component     | Status      |
|---------------|-------------|
| regime.py     | operational |
| scoring.py    | operational |
| vault.py      | operational |
| tradingview.py | operational |
| feishu.py     | mock        |
| scheduler.py  | idle        |
| events ledger | empty       |

## Open Issues

- MCP bridge not yet connected to live data source
- LLM parser not yet connected (raw mock used)
- Feishu card format pending upgrade
- Replay engine not yet implemented
- Docker deployment pending
