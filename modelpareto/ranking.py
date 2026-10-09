"""Rank models by score per dollar: score / input-price (or output-price),
descending. Free models score an infinite ratio and lead their benchmark,
ordered by score. Models missing the price or the benchmark's score are
excluded — a ratio cannot be computed for them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .data import Model
from .pricing import variant_of, weighted_cost

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


#: Benchmark families the console list combines, in priority order.
PRIMARY_FAMILY = "Terminal-Bench"
SECONDARY_FAMILY = "SWE-Bench"

#: Share of the list culled into each side table.
CULL_FRACTION = 0.3

BAND_FREE = "free"
BAND_INCLUDED = "included"
BAND_PAYG = "payg"


@dataclass(frozen=True)
class Row:
    """One printable line of the console list."""

    model: Model
    name: str                    # display label: variant and [free] applied
    price: float                 # representative cost, $/1M (nominal if free)
    free: bool
    band: str
    score: float | None          # primary family score, else secondary
    benchmark: str | None        # the key `score` came from
    entry: object | None = None  # pricing.PriceEntry when doc-priced


def _best(scores: dict[str, float], family: str) -> tuple[str, float] | None:
    """Highest score under *family*, as ``(key, value)``.

    Families are matched with spaces folded to dashes so ``Terminal Bench
    2.1`` and ``Terminal-Bench 2.1`` count as the same benchmark.
    """
    best = None
    for key, value in scores.items():
        if key.lower().replace(" ", "-").startswith(family.lower()):
            if best is None or value > best[1]:
                best = (key, value)
    return best


def ratio(score: float, price: float) -> float:
    """Score per dollar; a free line beats everything rather than going inf..."""
    return float("inf") if price == 0 else score / price


def build_rows(models: list[Model], entries=(), free_ids=frozenset(),
               axis: str = "weighted") -> list[Row]:
    """Price every line of the console list.

    A documented line replaces the catalog price (the catalog has no notion
    of tier: it carries the Zen rate for a model Go sells cheaper), and one
    model can produce several rows — one per priced variant. Free rows are
    re-priced at a tenth of the cheapest real price in the set so that "free"
    sorts and divides like any other cost instead of going infinite.

    *axis* picks what a line costs: ``weighted`` (20/60/20 in/cached/out),
    or a single ``input`` / ``output`` rate.
    """
    by_model: dict[str, list] = {}
    for entry in entries:
        if entry.model_id:
            by_model.setdefault(entry.model_id, []).append(entry)

    def cost_of(model: Model, entry) -> float | None:
        if axis == "input":
            return entry.input if entry else model.price_input
        if axis == "output":
            return entry.output if entry else model.price_output
        if entry:
            return weighted_cost(entry.input, entry.output, entry.cached_read)
        return weighted_cost(model.price_input, model.price_output,
                             model.price_cached_input)

    staged = []  # (model, entry, display name, cost, free, monthly limit)
    for m in models:
        slug = m.model_id.split("/")[-1]
        documented = (by_model.get(slug, [])
                      + by_model.get(f"{slug}-free", []))
        cost = cost_of(m, None)
        if not documented:
            if cost is None:
                continue  # no price at all — nothing to rank it on
            staged.append((m, None, m.name, cost,
                           m.model_id in free_ids or cost == 0, None))
            continue
        variants = set()
        for entry in documented:   # first tier in *entries* owns a variant
            suffix = variant_of(entry.name)
            if suffix in variants:
                continue
            variants.add(suffix)
            label = f"{m.name} {suffix}" if suffix else m.name
            staged.append((m, entry, label, cost_of(m, entry),
                           entry.free or m.model_id in free_ids, entry.limit))

    real = [cost for _model, _entry, _name, cost, free, _limit in staged
            if not free and cost and cost > 0]
    nominal = min(real) / 10 if real else 0.0

    rows = []
    for model, entry, name, cost, free, limit in staged:
        if free:
            price, band = nominal, BAND_FREE
            if "free" not in name.lower():
                name = f"{name} [free]"
        else:
            price = cost
            band = BAND_INCLUDED if limit is not None else BAND_PAYG
        primary = _best(model.scores, PRIMARY_FAMILY)
        secondary = _best(model.scores, SECONDARY_FAMILY)
        chosen = primary or secondary
        rows.append(Row(model=model, name=name, price=price, free=free,
                        band=band,
                        score=chosen[1] if chosen else None,
                        benchmark=chosen[0] if chosen else None,
                        entry=entry))
    return rows


def order_rows(rows: list[Row]) -> list[Row]:
    """Terminal-Bench first, then greedily place the SWE-Bench-only rows.

    The two benchmark families are not comparable (versions, harnesses,
    difficulty), so they are only an indication of relative performance: the
    Terminal-Bench rows build the skeleton in score-per-dollar order, and
    each row that has only SWE-Bench is inserted at the slot its SWE
    percentile maps to. Unscored rows cannot be ranked and drop — except
    free ones, which are listed last (they cost nothing whatever).
    """

    def primary_of(row: Row):
        return _best(row.model.scores, PRIMARY_FAMILY)

    def secondary_of(row: Row):
        return _best(row.model.scores, SECONDARY_FAMILY)

    def value_key(row: Row, score: float):
        return (-ratio(score, row.price), -score)

    skeleton = sorted((r for r in rows if primary_of(r)),
                      key=lambda r: value_key(r, primary_of(r)[1]))

    with_secondary = [r for r in rows if secondary_of(r)]
    with_secondary.sort(key=lambda r: -ratio(secondary_of(r)[1], r.price))
    rank = {id(r): i for i, r in enumerate(with_secondary)}
    count = len(with_secondary)

    result = list(skeleton)
    placed = 0  # insertion positions only move forward
    for row in (r for r in with_secondary if not primary_of(r)):
        percentile = rank[id(row)] / (count - 1) if count > 1 else 0.0
        slot = round(percentile * len(skeleton))
        slot = min(max(slot, placed), len(result))
        result.insert(slot, row)
        placed = slot
    # free lines no benchmark covers: ranked last, not dropped — maximising
    # free usage means seeing them even though they cannot be scored
    result += [r for r in rows
               if not primary_of(r) and not secondary_of(r) and r.free]
    return result


def partition_rows(rows: list[Row], fraction: float = CULL_FRACTION
                   ) -> tuple[list[Row], list[Row], list[Row]]:
    """Split into (best value, worst score, highest cost).

    Each cull takes the nearest whole row to *fraction* of the list (one row
    minimum once there is more than one, never on a single row). The groups
    are disjoint and the cost group has first claim: a model that is both
    priciest and lowest-scoring is reported once, under cost.
    """
    count = len(rows)
    if count <= 1:
        cull = 0
    else:
        cull = max(1, round(fraction * count))

    expensive = sorted(range(count), key=lambda i: (
        -rows[i].price,
        rows[i].score if rows[i].score is not None else float("inf"),
    ))[:cull]
    claimed = set(expensive)

    rest = [i for i in range(count) if i not in claimed]
    weakest = sorted(rest, key=lambda i: (
        rows[i].score if rows[i].score is not None else float("inf"),
        -rows[i].price,
    ))[:cull]
    weakest_set = set(weakest)

    main = [rows[i] for i in rest if i not in weakest_set]
    worst = [rows[i] for i in sorted(weakest, key=lambda i: (
        rows[i].score if rows[i].score is not None else float("inf"),
        -rows[i].price,
    ))]
    pricey = [rows[i] for i in sorted(expensive,
                                      key=lambda i: (-rows[i].price,))]
    return main, worst, pricey
