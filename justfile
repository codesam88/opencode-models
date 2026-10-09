# Show available recipes
[private]
help:
    @just --list

# Refresh model data from opencode.ai
[global]
fetch:
    python3 -m modelpareto fetch

# List benchmarks with coverage
[global]
benchmarks:
    python3 -m modelpareto benchmarks

# Render Pareto HTML report
[global]
render:
    python3 -m modelpareto render

# Text table: zen tier, best value first
[global]
list-zen:
    python3 -m modelpareto list --source zen

# Text table: go tier, best value first
[global]
list-go:
    python3 -m modelpareto list --source go

# Text table: full catalog, best value first
[global]
list-all:
    python3 -m modelpareto list --source all

# Run tests
[global]
test:
    python3 -m unittest discover -s tests
