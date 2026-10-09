"""Rank models by score per dollar: score / input-price (or output-price),
descending. Free models score an infinite ratio and lead their benchmark,
ordered by score. Models missing the price or the benchmark's score are
excluded — a ratio cannot be computed for them.
"""

from __future__ import annotations

from .data import Model

_PRICE_FIELDS = ("price_input", "price_output")


def _price_of(m: Model, field: str) -> float | None:
    if field not in _PRICE_FIELDS:
        raise ValueError(f"unknown price field {field!r}")
    return getattr(m, field)


def by_score_per_price(models: list[Model], benchmark: str,
                       price_field: str = "price_input") -> list[Model]:
    """Best value first. Ties on ratio break toward the higher score.

    *price_field* selects the cost axis: ``price_input`` (default) or
    ``price_output``.
    """
    ranked = [
        m for m in models
        if _price_of(m, price_field) is not None and benchmark in m.scores
    ]
    return sorted(
        ranked,
        key=lambda m: (
            -float("inf") if _price_of(m, price_field) == 0
            else -m.scores[benchmark] / _price_of(m, price_field),
            -m.scores[benchmark],
        ),
    )
