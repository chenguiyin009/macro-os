.PHONY: lint test run-runtime replay-events clean

# ── Lint ──────────────────────────────────────────────────────────────────────
lint:
	@echo "Running ruff linter..."
	@python -m ruff check . --select=E,F,I,N,W --ignore=F401 --exit-zero || true
	@echo "Running mypy type checker..."
	@python -m mypy core/ adapters/ runtime/ scripts/ --ignore-missing-imports --check-untyped-defs || true

# ── Test ──────────────────────────────────────────────────────────────────────
test:
	@echo "Running tests..."
	@python -m pytest tests/ -v --tb=short

# ── Run ────────────────────────────────────────────────────────────────────────
run-runtime:
	@echo "Running macro pipeline (single cycle)..."
	@python -m runtime.main

run-loop:
	@echo "Running macro pipeline (scheduler loop)..."
	@python -m runtime.main --loop

dry-run:
	@echo "Running dry run..."
	@python -m runtime.main --dry-run

# ── Replay (placeholder for Step 2) ────────────────────────────────────────────
replay-events:
	@echo "Replay engine not yet implemented — see Step 2"

# ── Validate ───────────────────────────────────────────────────────────────────
validate:
	@python scripts/validate_ledger.py

# ── Clean ──────────────────────────────────────────────────────────────────────
clean:
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@echo "Cleaned build artifacts"
