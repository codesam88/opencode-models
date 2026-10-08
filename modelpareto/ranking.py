"""Rank models by score per dollar: score / input-price, descending.

Free models score an infinite ratio and lead their benchmark, ordered by
score. Models missing a price or the benchmark's score are excluded — a
ratio cannot be computed for them.
"""

from __future__ import annotations

from .data import Model


def by_score_per_price(models: list[Model], benchmark: str) -> list[Model]:
    """Best value first. Ties on ratio break toward the higher score."""
    ranked = [
        m for m in models
        if m.price_input is not None and benchmark in m.scores
    ]
    return sorted(
        ranked,
        key=lambda m: (
            -float("inf") if m.price_input == 0 else -m.scores[benchmark] / m.price_input,
            -m.scores[benchmark],
        ),
    )
