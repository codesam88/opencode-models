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

# Text table: both providers merged, best value first
list:
    python3 -m modelpareto list

# Text table: zen provider only
list-zen:
    python3 -m modelpareto list --source zen

# Text table: go provider only
list-go:
    python3 -m modelpareto list --source go

# Text table: full catalog, incl. models in neither provider
list-all:
    python3 -m modelpareto list --source all

# Run tests
test:
    python3 -m unittest discover -s tests
