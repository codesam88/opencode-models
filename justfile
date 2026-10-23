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

# Refresh stale sources in your data dir (age-gated; warnings, never fails)
sync:
    uv run --no-sync opencode-models sync

# Refresh data/plans/*.json from the docs pages (gate fails shut)
plans-sync:
    uv run --no-sync python -m opencode_models.go_pricing
    uv run --no-sync python -m opencode_models.zen_pricing

# Validate the local snapshots offline (default: data/plans)
plans-check:
    uv run --no-sync python -m opencode_models.go_pricing --check
    uv run --no-sync python -m opencode_models.zen_pricing --check

# Promote validated snapshots to the shipped fallback (curation: review, then PR)
plans-freeze: plans-check
    cp data/plans/go.json opencode_models/data/plans/go.json
    cp data/plans/zen.json opencode_models/data/plans/zen.json

# Unit tests
test:
    uv run --no-sync python -m unittest discover -s tests

# Lint: ruff check/format/complexity + offline zizmor
lint:
    uv run --no-sync ruff check opencode_models tests
    uv run --no-sync ruff format --check opencode_models tests
    uvx zizmor --no-online-audits .github/workflows/

# Full check: tests + entry point resolving + wheel build
check: test
    uv run --no-sync opencode-models --help >/dev/null
    uv build

# Sweep tracked files for secret patterns
audit:
    @grep -rniE 'BEGIN (RSA |OPENSSH |EC )?PRIVATE ''KEY|aws_secret_''access_key|ghp_''[A-Za-z0-9]{36}|github_pat_''[A-Za-z0-9]{22}' --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ . || echo "no tracked secret patterns"  # '' splits stop it matching its own pattern text
