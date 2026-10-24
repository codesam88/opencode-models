"""`list` command: the ranked console list, best value first."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from .. import availability
from ..benchmarks import available
from ..ranking import (
    BAND_FREE,
    BAND_INCLUDED,
    BAND_PAYG,
    by_score_per_price,
    grade_rows,
    order_rows,
    partition_rows,
)
from ..rows import rows_for
from ..tables import column_widths
from . import fail
from .render import cells, format_list_row, headings, price_note, section

BAND_LABELS = (
    (BAND_FREE, "free — use first"),
    (BAND_INCLUDED, "included usage"),
    (BAND_PAYG, "pay-as-you-go"),
)
#: Two layouts, both built around the one score: the default shows the
#: model's score beside its cost and plan, and ``--benchmark X`` ranks on
#: one benchmark and adds that benchmark's tag. The per-axis raw numbers
#: live in `scores`, not here. Header units follow the `--cost` mode
#: (``¢/req`` for fix/est, ``¢/1m`` for input/output); see `_header`.


def _widths(rows, axis: bool = True, price: str = "est") -> tuple[int, ...]:
    """Widest cell per column across the whole view, header included."""
    return tuple(column_widths(headings(axis, price), [cells(r, axis) for r in rows]))


def _select(data_dir: Path, source: str | None, price: str = "est"):
    """Which providers a view covers, or None if the name is not one.

    Providers are zen and go, plus ``-`` for catalog models in neither tier.
    """
    tiers = ["go", "zen"]
    if source is None:
        return list(tiers), "tiers"  # both, merged
    if source == "all":
        return list(tiers) + ["-"], "all"
    if source in tiers:
        return [source], "tier"
    return None


def _current_offered(
    data_dir: Path,
    bypass: bool = False,
) -> set[str] | None:
    """Model ids served right now, or None for no restriction.

    Thin wrapper over :func:`availability.current_offered` (the availability
    service): it renders the returned warnings and returns the offered ids.
    *bypass* (`--all`) skips every filter.
    """
    result = availability.current_offered(Path(data_dir), bypass=bypass)
    for warning in result.warnings:
        print(warning)
    return result.offered


def run(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return fail(f"no data directory at {data_dir} — run `fetch` first")

    source = args.source
    chosen = _select(data_dir, source)
    if chosen is None:
        return fail(f"unknown source {source!r} — available: go, zen, all")
    selected, scope = chosen

    _result = rows_for(
        data_dir, selected, args.cost, getattr(args, "all_models", False)
    )
    lines = list(_result.warnings)
    rows, listed, _curated, counts, tier_slugs, models = (
        _result.rows,
        _result.listed,
        _result.curated,
        _result.counts,
        _result.tier_slugs,
        _result.models,
    )
    if not listed:
        if lines:
            print("\n".join(lines))
        return fail(f"no model of {source or 'zen+go'} is in {data_dir} — run `fetch`")
    # one line per (model, variant, score, cost) before the culls see them
    if args.benchmark:
        wanted = args.benchmark
        if wanted not in dict(available(listed)):
            listing = ", ".join(f"{n} ({c})" for n, c in list(available(listed))[:10])
            if lines:
                print("\n".join(lines))
            return fail(f"unknown benchmark {wanted!r} — available: {listing}")
        rows = [
            replace(r, score=r.model.scores[wanted], benchmark=wanted)
            for r in rows
            if wanted in r.model.scores
        ]
        proxies = [
            replace(r.model, price_input=r.price, scores={wanted: r.score})
            for r in rows
        ]
        ranked = by_score_per_price(proxies, wanted, price_field="price_input")
        by_proxy = {id(p): r for p, r in zip(proxies, rows, strict=True)}
        rows = [by_proxy[id(m)] for m in ranked]
        order_note = f"benchmark {wanted} · sorted by score per $"
    else:
        rows = order_rows(rows)
        order_note = "ranked by score per $, best value first"
    # the marker reads the score itself, so a row in the cost or
    # worst-score table shows the same grade it earned in best value
    rows = grade_rows(rows)
    # the layout: every axis side by side, or the single ranked benchmark
    axis = not args.benchmark
    if not rows:
        if lines:
            print("\n".join(lines))
        return fail(f"no {source or 'zen+go'} model in {data_dir} has benchmark scores")

    if scope == "tiers":
        head = f"zen+go: {counts.get('zen', 0)} zen + {counts.get('go', 0)} go models"
    elif scope == "tier":
        head = f"{source}: {counts[source]}/{len(tier_slugs[source])} models in catalog"
    else:
        head = f"all: {len(models)} models in catalog"
    lines.append(f"{head} · {len(rows)} lines · {order_note}")
    lines.append(price_note(args.cost))
    if not getattr(args, "all_models", False):
        plans = "Go/Zen plans" if source in (None, "all") else f"{source} plan"
        lines.append(
            f"Only models available right now on the {plans} - "
            f"full list with --all-models."
        )
    lines.append("GRADE: ++ ≥90, + ≥75, o 60–75, - 50–60, -- <50, · insufficient data")

    # the split always happens — the cull is what keeps the worst-scoring
    # and priciest models out of the view — only the reporting is optional
    main, worst, pricey = partition_rows(rows)
    # one set of column widths for the whole view, so the three tables line
    # up with each other too
    widths = _widths(rows, axis, args.cost)
    lines.extend(section(f"best value · {len(main)} lines", widths, axis, args.cost))
    for band, label in BAND_LABELS:
        group = [r for r in main if r.band == band]
        if group:
            lines.append(f"{label} · {len(group)}")
            for row in group:
                lines.append(
                    format_list_row(cells(row, axis), widths, row.grade, row.name)
                )

    if args.tables == "all":
        lines.extend(
            section(
                f"worst score (bottom 30%) · {len(worst)} lines",
                widths,
                axis,
                args.cost,
            )
        )
        for row in worst:
            lines.append(format_list_row(cells(row, axis), widths, row.grade, row.name))

        lines.extend(
            section(
                f"highest cost (top 30%) · {len(pricey)} lines", widths, axis, args.cost
            )
        )
        for row in pricey:
            lines.append(format_list_row(cells(row, axis), widths, row.grade, row.name))
    print("\n".join(lines))
    return 0
