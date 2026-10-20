# Show available recipes
[private]
help:
    @just --list

# Install the tool: uv sync for the repo, or a global entry point
bootstrap:
    uv sync
    @git config core.hooksPath >/dev/null 2>&1 || git config core.hooksPath .githooks

# Install the CLI onto your PATH (uv's tool dir, or pipx/uvx), then seed
# the reference dataset into the XDG data dir: fetched layers copy missing
# files only (your fetch results are never overwritten), while tracked
# overrides/ deploy so pushed curation reaches the installed tool
install:
    uv tool install --force .
    uv run --no-sync python -m opencode_models.seed

# Unit tests
test:
    uv run --no-sync python -m unittest discover -s tests

# Lint, format check and complexity gate (ruff from the locked dev group)
lint:
    uv run --no-sync ruff check opencode_models tests
    uv run --no-sync ruff format --check opencode_models tests

# Full check: tests + the entry point resolving + a wheel build
check: test
    uv run --no-sync opencode-models --help >/dev/null
    uv build

# Local opsec sweep: tracked secret patterns (the '' splits keep this
# recipe from matching its own pattern text)
audit:
    @grep -rniE 'BEGIN (RSA |OPENSSH |EC )?PRIVATE ''KEY|aws_secret_''access_key|ghp_''[A-Za-z0-9]{36}|github_pat_''[A-Za-z0-9]{22}' --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ . || echo "no tracked secret patterns"

# Run the console against the checkout dataset, passing everything through:
# just run <command> [args...]  — e.g. just run next, just run list --source zen
run *args:
    uv run --no-sync opencode-models --data data {{args}}
