# Changelog

All notable changes to Macro OS are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to semantic versioning for the runtime contract.

## [5.0.0] - 2026-07-09

The "bulletproof runtime" milestone. Five atomic commits close the gap between
"passes on the author's machine" and "reproducible from a clean `git clone`",
and harden the decision kernel's audit contract so that *every* authority branch
— including `HARD_VETO` — emits a uniform, machine-readable four-step trail.

### Added

- **Dependency manifest completeness** (`requirements.txt`): declared the runtime
  and test dependencies that were previously only satisfied by pre-installed
  environments — `aiosqlite`, `httpx`, `fastapi` (all top-level imports inside
  `runtime/macro_core.py`) and `pytest` (a first-class verification command).
  A clean `git clone` + `pip install -r requirements.txt` now survives test
  collection instead of crashing with `ModuleNotFoundError: aiosqlite`.
- **`.gitignore`**: ignores generated runtime artifacts (`macro_os_ledger.db`,
  `tmp_cdp_output.json`, `macro_history_synthetic.csv`), Python caches
  (`__pycache__/`, `*.pyc`), and logs/pytest cache (`*.log`, `.pytest_cache/`).
  Deliberately uses `*.log`, **not** `*.jsonl`, so the event-sourcing truth
  source `vault/EVENTS.log.jsonl` stays tracked.
- **Dry-run regression tests** (`tests/test_runtime_main.py`, +75 lines): four
  new cases covering previously un-exercised main-path branches —
  `--output` file persistence, `decision is None → exit 1`, orchestrator build
  failure → exit 1, and `tv.fetch()` returning `None` → MOCK fallback still
  yields a valid decision.

### Changed

- **`runtime/main.py` path bootstrap** (robust environment resilience): the
  project root is now unconditionally inserted at the front of `sys.path`, so
  the entry point works identically whether invoked as `python runtime/main.py`
  or `python -m runtime.main`. The previous bootstrap only injected the root when
  the `MACRO_OS_ROOT` env var was set, which made the intuitive
  `python runtime/main.py --dry-run` crash with `ModuleNotFoundError` on a fresh
  checkout. The `MACRO_OS_ROOT` override is preserved as a separate, non-gating
  branch for containerized/Docker deployments.

### Fixed

- **`core/decision_kernel.py` — `HARD_VETO` audit-trail contract**
  (`audit_trail`): the `HARD_VETO` branch previously returned an **empty dict**
  (`audit_trail={}`), which would raise `KeyError` for any consumer reading
  `audit_trail["step_1_safety_gate"]` and silently broke the four-step contract
  that the other authority branches honor. It now emits the **same four-step key
  set** as `SAFETY_GATE` / `SOFT_POLICY` — `step_1_safety_gate`,
  `step_2_hard_veto`, `step_3_soft_policy`, `step_4_global_velocity_limit` — with
  the steps not executed under a veto marked `SKIPPED_DUE_TO_VETO`. This keeps the
  dictionary keys *absolutely consistent* across all authority levels (the
  alternative 5-step rename was explicitly rejected to avoid introducing a third,
  divergent schema). Combined with the pre-existing append-only
  `vault/EVENTS.log.jsonl` truth source, veto and decision lineage is now
  consistently structured and inspectable across operational cycles.
- **Repository hygiene**: purged 92 tracked `__pycache__/*.pyc` files and 3
  generated runtime artifacts from the Git index (96 files, 862 deletions),
  eliminating index noise from build/cache output.

### Tests

- Full suite: **191 passed** (baseline 186 → +4 dry-run edge cases → +1 kernel
  contract lock).
- New kernel-contract test `test_hard_veto_emits_uniform_four_step_audit_trail`
  locks the `HARD_VETO` four-step structure so a future regression fails loudly.

### Verification

From a clean virtualenv with **only** `requirements.txt` installed:

```bash
pip install -r requirements.txt
python scripts/validate_macro_config.py      # PASS
python scripts/validate_ledger.py            # PASS
python -m runtime.main --dry-run             # PASS
python runtime/main.py --dry-run             # PASS (regression fixed by #5)
python -m pytest -q                          # 191 passed
```

### Known limitations / out of scope

- The `HARD_VETO` branch is covered by the unit test
  (`test_decision_kernel.py`) but is **not** exercised by the dry-run integration
  path: the dry-run MOCK fallback always yields `TIGHT_LIQUIDITY + CRISIS`, which
  routes through the `SAFETY_GATE/CRISIS` full-defense branch. `HARD_VETO` is only
  triggered by a non-`RISK_ON` *real* regime.
- `pandas` (used by some `scripts/`) and `futu-api` (lazy-imported in
  `adapters/futu.py`) remain intentionally absent from `requirements.txt` because
  they are not on the four-command verification path. Add them if CI begins
  exercising those entry points.
- Commits were produced in a sandbox with no GitHub write access; the push to
  `chenguiyin009/macro-os` is performed by the maintainer.

[5.0.0]: https://github.com/chenguiyin009/macro-os/releases/tag/v5.0.0
