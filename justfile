# Show available recipes
[private]
help:
    @just --list -u

# uv sync + enable .githooks (guarded, never clobbers)
setup:
    uv sync
    @git config core.hooksPath >/dev/null 2>&1 || git config core.hooksPath .githooks

# Install CLI to $PATH, seed reference data (missing only)
install:
    uv tool install --force .
    uv run --no-sync python -m opencode_models.seed

# Run the console against ./data, e.g. just run next
run *args:
    uv run --no-sync opencode-models --data data {{args}}

# Refresh data/plans/go.json from the Go docs page (gate fails shut)
plans-update:
    uv run --no-sync python scripts/update_go_pricing.py

# Validate a limits snapshot offline (default: the local copy)
plans-check:
    uv run --no-sync python scripts/update_go_pricing.py --check

# Promote the validated local snapshot to the shipped fallback
plans-freeze: plans-check
    cp data/plans/go.json opencode_models/data/plans/go.json

# Unit tests
test:
    uv run --no-sync python -m unittest discover -s tests

# Lint: ruff check/format/complexity + offline zizmor
lint:
    uv run --no-sync ruff check opencode_models tests scripts
    uv run --no-sync ruff format --check opencode_models tests scripts
    uvx zizmor --no-online-audits .github/workflows/

# Full check: tests + entry point resolving + wheel build
check: test
    uv run --no-sync opencode-models --help >/dev/null
    uv build

# Sweep tracked files for secret patterns
audit:
    @grep -rniE 'BEGIN (RSA |OPENSSH |EC )?PRIVATE ''KEY|aws_secret_''access_key|ghp_''[A-Za-z0-9]{36}|github_pat_''[A-Za-z0-9]{22}' --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ . || echo "no tracked secret patterns"  # '' splits stop it matching its own pattern text
