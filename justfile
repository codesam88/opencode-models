# Show available recipes
[private]
help:
    @just --list

# Install the tool: uv sync for the repo, or a global entry point
bootstrap:
    uv sync

# Install the CLI onto your PATH (uv's tool dir, or pipx/uvx)
install:
    uv tool install --force .

# Unit tests
test:
    uv run --no-sync python -m unittest discover -s tests

# Full check: tests + the entry point resolving + a wheel build
check: test
    uv run --no-sync opencode-rank --help >/dev/null
    uv build

# Console smoke test — needs the dataset on disk (fetch, or the local-data branch)
smoke:
    uv run --no-sync opencode-rank --data data list --source zen

# Refresh model data from opencode.ai
fetch:
    uv run --no-sync opencode-rank --data data fetch

# Curated overrides that disagree with upstream
conflicts:
    uv run --no-sync opencode-rank --data data conflicts

# List benchmarks with coverage
benchmarks:
    uv run --no-sync opencode-rank --data data benchmarks

# Render Pareto HTML report
render:
    uv run --no-sync opencode-rank --data data render

# Text table: both providers merged, best value first
list:
    uv run --no-sync opencode-rank --data data list

# Text table: zen provider only
list-zen:
    uv run --no-sync opencode-rank --data data list --source zen

# Text table: go provider only
list-go:
    uv run --no-sync opencode-rank --data data list --source go

# Text table: full catalog, incl. models in neither provider
list-all:
    uv run --no-sync opencode-rank --data data list --source all
