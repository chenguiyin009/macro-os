.PHONY: lint test run-runtime run-loop dry-run dry-run-trinity validate validate-config validate-ledger validate-trinity clean

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

# ── Run (macro-os) ─────────────────────────────────────────────────────────────
run-runtime:
	@echo "Running macro pipeline (single cycle)..."
	@python -m runtime.main

run-loop:
	@echo "Running macro pipeline (scheduler loop)..."
	@python -m runtime.main --loop

dry-run:
	@echo "Running macro dry run..."
	@python -m runtime.main --dry-run

# ── Run (trinity, bottom-up subsystem) ─────────────────────────────────────────
dry-run-trinity:
	@echo "Running trinity dry run..."
	@python -m trinity --dry-run

# ── Validate ───────────────────────────────────────────────────────────────────
validate: validate-config validate-ledger validate-trinity
	@echo "✓ All validations passed."

validate-config:
	@python scripts/validate_macro_config.py

validate-ledger:
	@python scripts/validate_ledger.py

validate-trinity:
	@python scripts/validate_trinity_ledger.py

# ── Clean ──────────────────────────────────────────────────────────────────────
clean:
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@echo "Cleaned build artifacts"
