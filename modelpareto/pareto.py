"""Pareto frontier of models over (price, score).

A model is on the frontier when no other model is both cheaper and scores
higher (or equal on one axis and strictly better on the other). Input price
in USD per 1M tokens is the cost axis; the chosen benchmark's score is the
quality axis. Models lacking a price or that benchmark's score cannot be
placed and are excluded.
"""

from __future__ import annotations

from .data import Model


def frontier(models: list[Model], benchmark: str) -> list[Model]:
    """Return the price-vs-score Pareto set, cheapest first.

    On an exact (price, score) tie the earlier model in the input wins.
    """
    competitors = [
        m for m in models if m.price_input is not None and benchmark in m.scores
    ]
    kept: list[Model] = []
    # Cheapest first; on equal price, highest score first — so the tie-winner
    # is decided before any comparison happens.
    for m in sorted(competitors, key=lambda m: (m.price_input, -m.scores[benchmark])):
        if any(
            k.price_input <= m.price_input and k.scores[benchmark] >= m.scores[benchmark]
            for k in kept
        ):
            continue
        kept.append(m)
    return kept
