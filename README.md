# Macro OS

Macro Trading System — Event-driven macro decision engine.

## Architecture

```
MCP → LLM Parser → Feature Builder → Regime → Score → Decision → Event Store → Feishu
```

### Core Principles

- **Fully stateless**: No runtime memory. All state derived from `EVENTS.log.jsonl`.
- **Event sourcing**: Append-only event store. Every output is an event.
- **Idempotent**: Duplicate events detected and rejected via sha256 `event_id`.
- **Config separated**: All thresholds in `config/thresholds.yaml`. No magic numbers.
- **LLM isolated**: LLM only parses MCP output into structured features. No decision-making.
- **Pure function core**: `regime.py` and `scoring.py` have zero IO or global state.
- **Orchestrator ≠ Strategy**: Orchestrator coordinates calls, contains zero business logic.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Dry run (no events written)
make dry-run

# Single pipeline cycle
make run-runtime

# Run tests
make test

# Validate event ledger
make validate
```

For a step-by-step operator guide covering both `runtime.main` and the `Global Sentinel` Pine webhook path, see [docs/USAGE.md](docs/USAGE.md).

## Configuration

Set environment variables with the `MACRO_OS_` prefix, or create a `.env` file:

```
MACRO_OS_FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
MACRO_OS_MCP_SCRIPT_PATH=/path/to/mcp-script.mjs
MACRO_OS_MCP_TIMEOUT_SECONDS=8
MACRO_OS_SCHEDULER_INTERVAL_MINUTES=15
```

## Project Structure

```
macro-os/
├── runtime/           # Entry points and orchestration
│   ├── main.py        # CLI entry point
│   ├── orchestrator.py # Pipeline coordinator (no business logic)
│   └── scheduler.py   # Interval-based scheduler
├── core/              # Pure business logic
│   ├── regime.py      # Regime classifier (pure function)
│   ├── scoring.py     # Risk scoring engine (pure function)
│   ├── features.py    # Feature transformation (no decisions)
│   └── schemas.py     # Pydantic models
├── adapters/          # External integrations
│   ├── tradingview.py # MCP client wrapper
│   ├── feishu.py      # Notification client
│   └── vault.py       # Idempotent event store
├── config/            # Separated configuration
│   ├── settings.py    # pydantic-settings loader
│   └── thresholds.yaml # All magic numbers
├── vault/             # Event store directory
│   ├── EVENTS.log.jsonl # Append-only event ledger
│   ├── OS.md          # System architecture
│   └── STATUS.md      # Current system state
├── scripts/           # Utility scripts
│   └── validate_ledger.py  # Ledger integrity checker
├── tests/             # Test suite
│   ├── test_regime.py
│   ├── test_scoring.py
│   └── test_idempotency.py
├── Makefile
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

## Deployment

```bash
# Docker
docker-compose up -d

# Local dev mode
docker-compose --profile dev run dev
```

## License

Internal — Macro OS
