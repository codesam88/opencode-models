# Show available recipes
[private]
help:
    @just --list

# Install the tool: uv sync for the repo, or a global entry point
bootstrap:
    uv sync

# Install the CLI onto your PATH (uv's tool dir, or pipx/uvx), then seed
# the reference dataset into the XDG data dir: fetched layers copy missing
# files only (your fetch results are never overwritten), while tracked
# overrides/ deploy so pushed curation reaches the installed tool
install:
    uv tool install --force .
    uv run --no-sync python -m opencode_rank.seed

# Unit tests
test:
    uv run --no-sync python -m unittest discover -s tests

# Full check: tests + the entry point resolving + a wheel build
check: test
    uv run --no-sync opencode-rank --help >/dev/null
    uv build

# Local opsec sweep: tracked secret patterns
audit:
    @grep -rniE 'BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|aws_secret_access_key|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9]{22}' --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ . || echo "no tracked secret patterns"

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

# Next models to use, free and paid picks interleaved
next:
    uv run --no-sync opencode-rank --data data next

# Text table: full catalog, incl. models in neither provider
list-all:
    uv run --no-sync opencode-rank --data data list --source all
