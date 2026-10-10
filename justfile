# Show available recipes
[private]
help:
    @just --list

# Refresh model data from opencode.ai
fetch:
    python3 -m opencode_rank fetch

# Curated overrides that disagree with upstream
conflicts:
    python3 -m opencode_rank conflicts

# List benchmarks with coverage
benchmarks:
    python3 -m opencode_rank benchmarks

# Render Pareto HTML report
render:
    python3 -m opencode_rank render

# Text table: both providers merged, best value first
list:
    python3 -m opencode_rank list

# Text table: zen provider only
list-zen:
    python3 -m opencode_rank list --source zen

# Text table: go provider only
list-go:
    python3 -m opencode_rank list --source go

# Text table: full catalog, incl. models in neither provider
list-all:
    python3 -m opencode_rank list --source all

# Run tests
test:
    python3 -m unittest discover -s tests
