"""Rank models by score per dollar: score / input-price (or output-price),
descending. Free models score an infinite ratio and lead their benchmark,
ordered by score. Models missing the price or the benchmark's score are
excluded — a ratio cannot be computed for them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

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
#: Terminal-Bench 4.0 (Aug 2026) is a harder task set than 2.x — the best
#: 4.0 run scores ~58 where 2.1 leaders reach ~87 — so its numbers live on
#: their own scale. 2.1 still builds the skeleton (37 tier models carry it
#: against 12 for 4.0); 4.0 fills in like SWE-Bench until coverage flips.
TB4_FAMILY = "Terminal-Bench 4.0"

#: Fill-in axes, applied after the primary skeleton is built.
SECONDARY_AXES = (SECONDARY_FAMILY, TB4_FAMILY)

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
    provider: str = "-"          # zen / go, "-" when in neither tier
    entry: object | None = None  # pricing.PriceEntry when doc-priced


def _best(scores: dict[str, float], family: str,
          skip: str | None = None) -> tuple[str, float] | None:
    """Highest score under *family*, as ``(key, value)``.

    Families are matched with spaces folded to dashes so ``Terminal Bench
    2.1`` and ``Terminal-Bench 2.1`` count as the same benchmark. *skip*
    excludes keys on a harder scale of the same family — a 4.0 score must
    not win a slot that is compared against 2.x numbers.
    """
    best = None
    folded_skip = skip.lower().replace(" ", "-") if skip else None
    for key, value in scores.items():
        folded = key.lower().replace(" ", "-")
        if not folded.startswith(family.lower().replace(" ", "-")):
            continue
        if folded_skip and folded.startswith(folded_skip):
            continue
        if best is None or value > best[1]:
            best = (key, value)
    return best


def ratio(score: float, price: float) -> float:
    """Score per dollar; a free line beats everything rather than going inf..."""
    return float("inf") if price == 0 else score / price


def build_rows(models: list[Model], entries=(), free_ids=frozenset(),
               axis: str = "weighted", provider: str = "-") -> list[Row]:
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
        primary = _best(model.scores, PRIMARY_FAMILY, skip=TB4_FAMILY)
        secondary = _best(model.scores, SECONDARY_FAMILY)
        tb4 = _best(model.scores, TB4_FAMILY)
        chosen = primary or secondary or tb4
        rows.append(Row(model=model, name=name, price=price, free=free,
                        band=band,
                        score=chosen[1] if chosen else None,
                        benchmark=chosen[0] if chosen else None,
                        provider=provider, entry=entry))
    return rows


def merge_rows(*groups: list[Row]) -> list[Row]:
    """Concatenate provider groups onto one free-price scale.

    Each group prices its own free lines against its own cheapest line;
    once zen and go share a view the whole view has to agree on what "a
    tenth of the cheapest" means, so free rows are re-priced against every
    group's real prices. Provider tags and order are kept.
    """
    rows = [row for group in groups for row in group]
    real = [row.price for row in rows if not row.free and row.price > 0]
    nominal = min(real) / 10 if real else 0.0
    return [replace(row, price=nominal) if row.free else row for row in rows]


#: How generous a band is, for picking the best deal out of a merged line.
_BAND_RANK = {BAND_FREE: 0, BAND_INCLUDED: 1, BAND_PAYG: 2}


def dedupe_providers(rows: list[Row]) -> list[Row]:
    """One line per (model, variant, score, cost), providers enumerated.

    zen and go sell many models at the same rate for the same score, so the
    pair is one offer rather than two: the providers are listed together
    (``go,zen``). When only some of the providers fund the line from a
    monthly allowance, the more generous band wins — the line is available
    that way from at least one of the providers named on it.
    """
    merged: dict[tuple, Row] = {}
    keys: list[tuple] = []
    for row in rows:
        key = (row.model.model_id, row.name, row.score, round(row.price, 9))
        if key not in merged:
            merged[key] = row
            keys.append(key)
            continue
        current = merged[key]
        keep = current if _BAND_RANK[current.band] <= _BAND_RANK[row.band] else row
        providers = sorted({p for side in (current.provider, row.provider)
                            for p in side.split(",")})
        merged[key] = replace(keep, provider=",".join(providers))
    return [merged[key] for key in keys]


def order_rows(rows: list[Row]) -> list[Row]:
    """Terminal-Bench 2.x first, then the fill-in axes by their own score.

    The benchmark families are not comparable (versions, harnesses,
    difficulty), so they are only an indication of relative performance:
    the Terminal-Bench 2.x rows build the skeleton in score-per-dollar
    order, and each row that has only SWE-Bench — or only the harder
    Terminal-Bench 4.0 — is inserted at the slot its own axis' percentile
    maps to. Unscored rows cannot be ranked and drop — except free ones,
    which are listed last (they cost nothing whatever).
    """

    def primary_of(row: Row):
        return _best(row.model.scores, PRIMARY_FAMILY, skip=TB4_FAMILY)

    def score_on(row: Row, axis: str):
        return _best(row.model.scores, axis)

    def value_key(row: Row, score: float):
        return (-ratio(score, row.price), -score)

    skeleton = sorted((r for r in rows if primary_of(r)),
                      key=lambda r: value_key(r, primary_of(r)[1]))

    result = list(skeleton)
    for axis in SECONDARY_AXES:
        placed = 0  # insertion positions only move forward within an axis
        fillers = sorted((r for r in rows if score_on(r, axis)),
                         key=lambda r: -ratio(score_on(r, axis)[1], r.price))
        rank = {id(r): i for i, r in enumerate(fillers)}
        count = len(fillers)
        for row in (r for r in fillers if not primary_of(r)):
            percentile = rank[id(row)] / (count - 1) if count > 1 else 0.0
            slot = round(percentile * len(skeleton))
            slot = min(max(slot, placed), len(result))
            result.insert(slot, row)
            placed = slot
    # free lines no benchmark covers: ranked last, not dropped — maximising
    # free usage means seeing them even though they cannot be scored
    scored = {id(r) for r in result}
    result += [r for r in rows if id(r) not in scored and r.free]
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
    # both cull tables read worst-scoring first; the expensive group still
    # *selects* on price, it just no longer sorts by it
    by_score = lambda i: (  # noqa: E731
        rows[i].score if rows[i].score is not None else float("inf"),
        -rows[i].price,
    )
    worst = [rows[i] for i in sorted(weakest, key=by_score)]
    pricey = [rows[i] for i in sorted(expensive, key=by_score)]
    return main, worst, pricey
