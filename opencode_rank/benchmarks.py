"""Inventory of benchmarks present in a dataset, for the selector."""

from __future__ import annotations

from collections import Counter

from .data import Model


def available(models: list[Model]) -> list[tuple[str, int]]:
    """Return ``(benchmark, model_count)`` pairs.

    Sorted by coverage (best-covered first), ties broken by name so the
    listing is stable between runs.
    """
    counts: Counter[str] = Counter()
    for m in models:
        counts.update(m.scores.keys())
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
