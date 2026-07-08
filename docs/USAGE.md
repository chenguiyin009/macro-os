# Macro OS Usage Guide

This guide explains how to run Macro OS in this repository and how the two
data paths differ:

- the runtime decision pipeline driven by `runtime.main`
- the Global Sentinel pull script driven by Chrome CDP / relay

## 1. Pick the right entry point

| Goal | Command | What it does |
|------|---------|--------------|
| Dry-run the decision pipeline | `make dry-run` or `python -m runtime.main --dry-run` | Runs one cycle, prints the decision JSON, does not write events or send Feishu notifications. |
| Run one live cycle | `make run-runtime` or `python -m runtime.main` | Runs one cycle, writes an event to the ledger, and sends a Feishu notification if a webhook is configured. |
| Run the scheduler loop | `make run-loop` or `python -m runtime.main --loop` | Repeats the runtime pipeline on the configured interval. |
| Pull Global Sentinel from TradingView | `python scripts/pull_global_sentinel.py` | Reads a chart snapshot from `relay/tv-desktop-monitor.mjs`, extracts the Global Sentinel study, and posts the payload to a local webhook. |

Important:

- `runtime.main` does not call `scripts/pull_global_sentinel.py`.
- `scripts/pull_global_sentinel.py` does not feed the runtime pipeline automatically.
- If you want live TradingView MCP data for `runtime.main`, you must point
  `MACRO_OS_MCP_SCRIPT_PATH` to an external script that prints JSON on stdout.

## 2. Install dependencies

From the `macro-os` directory:

```bash
pip install -r requirements.txt
```

If you use Docker, build and run from the repo root or the `macro-os`
directory, depending on your shell setup.

## 3. Configure environment variables

Macro OS reads settings from the `MACRO_OS_` prefix.

Minimal runtime example:

```bash
MACRO_OS_MCP_COMMAND=node
MACRO_OS_MCP_SCRIPT_PATH=C:\path\to\mcp-script.mjs
MACRO_OS_MCP_TIMEOUT_SECONDS=8
MACRO_OS_SCHEDULER_INTERVAL_MINUTES=15
MACRO_OS_FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
```

Notes:

- `MACRO_OS_MCP_SCRIPT_PATH` is optional, but without it the runtime falls back
  to the mock snapshot path.
- `MACRO_OS_MCP_COMMAND` defaults to `node`.
- `MACRO_OS_FEISHU_WEBHOOK_URL` is optional. If it is not set, Feishu sends
  are logged locally as a mock notification.

## 4. Run the runtime pipeline

Recommended sequence:

1. Dry run first:

   ```bash
   make dry-run
   ```

2. If the output looks correct, run one live cycle:

   ```bash
   make run-runtime
   ```

3. If you want the scheduler:

   ```bash
   make run-loop
   ```

What to expect:

- `--dry-run` prints a decision JSON object to stdout.
- Logging goes through the normal logger, so log lines can appear separately
  from the JSON output.
- A live cycle appends to the event ledger and then sends a Feishu card if a
  webhook is configured.
- If you edit `config/thresholds.yaml` or `config/watchlist.yaml`, run
  `python scripts/validate_macro_config.py` before starting the runtime.

Runtime data flow:

1. `TradingViewAdapter.fetch()` tries the external MCP script first.
2. If `MACRO_OS_MCP_SCRIPT_PATH` is missing or invalid, the runtime currently
   falls back to the mock snapshot path.
3. The orchestrator transforms the features into regime, score, and decision.

Important parsing rule:

- The external MCP script must print a single JSON object on stdout.
- Extra log lines on stdout can break parsing and cause the runtime to fall
  back to mock data.

## 5. Run the Global Sentinel CDP pull

This path is separate from `runtime.main`.

Use it when you want to read the `Global Sentinel` study from the QQQ chart and
post the extracted values to the local webhook:

```bash
python scripts/pull_global_sentinel.py
```

What the script does:

1. Runs `relay/tv-desktop-monitor.mjs --once` through Node.
2. Reads the chart snapshot JSON from stdout.
3. Finds the `Global Sentinel` study in the snapshot.
4. Builds a compact payload with M1 / M2 / M3 / Danger / composite values.
5. POSTs the payload to `http://127.0.0.1:8020/webhook/global-sentinel`.

Operational requirements:

- The QQQ chart must already contain the `Global Sentinel` study.
- The relay script must be reachable at `relay/tv-desktop-monitor.mjs`.
- The environment in `scripts/pull_global_sentinel.py` currently uses hardcoded
  `PROJECT_ROOT`, `NODE`, `RELAY`, and `WEBHOOK` constants. Update them if your
  machine or folder layout is different.

If the study is not found, add `Global Sentinel` to the QQQ chart and try
again.

## 6. Docker usage

The `docker-compose.yml` file exposes two paths:

- `runtime`: long-running loop mode
- `dev`: dry-run mode for local testing

Example:

```bash
docker-compose up -d
docker-compose --profile dev run dev
```

The compose file forwards `MACRO_OS_MCP_COMMAND`,
`MACRO_OS_MCP_SCRIPT_PATH`, `MACRO_OS_MCP_TIMEOUT_SECONDS`, and
`MACRO_OS_FEISHU_WEBHOOK_URL` from the host environment.

## 7. Verify and troubleshoot

Recommended checks:

```bash
make test
make validate
python scripts/validate_macro_config.py
```

Common issues:

- If the runtime always uses mock values, check that
  `MACRO_OS_MCP_SCRIPT_PATH` points to an existing script and that the script
  prints JSON only.
- If Feishu notifications do not appear, check
  `MACRO_OS_FEISHU_WEBHOOK_URL`.
- If the CDP pull fails, verify Chrome CDP is available and the relay script
  can open the TradingView chart snapshot.
- If `python scripts/pull_global_sentinel.py` reports that the study is not
  found, make sure the `Global Sentinel` study has been added to the chart.
- If config validation fails, inspect `config/thresholds.yaml` and
  `config/watchlist.yaml` together; they are now validated as a pair.

## 8. Short version

If you only want the fastest path:

1. `make dry-run`
2. `make run-runtime`
3. `python scripts/validate_macro_config.py`
4. `python scripts/pull_global_sentinel.py` when you need the chart-side
   Global Sentinel payload

## Simulator CLI (v5)

The kernel can be invoked from the command line via the simulator:

`ash
cd macro-os
python scripts/simulate_kernel.py \\
    --phase EARLY \\
    --regime RISK_ON \\
    --soft-regime-label RISK_ON \\
    --risk-score 0.8 \\
    --confidence 0.8 \\
    --proposed-risk 0.8 \\
    --recovery false \\
    --days-in-recovery 0 \\
    --previous-risk-budget 0.0
`

Output is a JSON object with execution_outcome (final_risk_budget, final_defense_budget, action_required, reason_code) and udit_trail.
