# Show available recipes
[private]
help:
    @just --list

# Refresh model data from opencode.ai
fetch:
    python3 -m modelpareto fetch

# List benchmarks with coverage
benchmarks:
    python3 -m modelpareto benchmarks

# Render Pareto HTML report
render:
    python3 -m modelpareto render

# Text table: zen tier, best value first
list-zen:
    python3 -m modelpareto list --source zen

# Text table: go tier, best value first
list-go:
    python3 -m modelpareto list --source go

# Text table: full catalog, best value first
list-all:
    python3 -m modelpareto list --source all

# Run tests
test:
    python3 -m unittest discover -s tests
