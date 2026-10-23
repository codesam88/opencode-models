"""Rank models by score per dollar: score / input-price (or output-price),
descending. Free models score an infinite ratio and lead their benchmark,
ordered by score. Models missing the price or the benchmark's score are
excluded — a ratio cannot be computed for them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .identity import canonical_id
from .models import Model
from .pricing import variant_of, weighted_cost
from .scoring import combined, derived_scores, scales, tracked

_PRICE_FIELDS = ("price_input", "price_output")


def _price_of(m: Model, field: str) -> float | None:
    if field not in _PRICE_FIELDS:
        raise ValueError(f"unknown price field {field!r}")
    return getattr(m, field)


def by_score_per_price(
    models: list[Model], benchmark: str, price_field: str = "price_input"
) -> list[Model]:
    """Best value first. Ties on ratio break toward the higher score.

    *price_field* selects the cost axis: ``price_input`` (default) or
    ``price_output``.
    """
    ranked = [
        m
        for m in models
        if _price_of(m, price_field) is not None and benchmark in m.scores
    ]
    return sorted(
        ranked,
        key=lambda m: (
            -float("inf")
            if _price_of(m, price_field) == 0
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

#: The ordering axes shown side by side in the console: (key, column label,
#: benchmark family, versioned?). ``tb2`` is versioned so only real 2.x/3.x
#: runs count — Terminal-Bench Hard and Science are separate benchmarks that
#: merely share the prefix, and 4.0 is its own scale.
AXES = (
    ("tb2", "TB2", PRIMARY_FAMILY, True),
    ("tb4", "TB4", TB4_FAMILY, False),
    ("swe", "SWE", SECONDARY_FAMILY, False),
)

#: Share of the list culled into each side table.
CULL_FRACTION = 0.3

#: Rank grades for the console's marker column, read off the 0-100 score
#: itself rather than off position in the list: 90+ is `++`, 75+ is `+`,
#: 60+ is `o`, 50+ is `-`, below that `--`. Absolute bands mean a strong
#: field can share the top and a weak one is never flattered by it.
GRADE_PLUSPLUS = 90.0
GRADE_PLUS = 75.0
GRADE_O = 60.0
GRADE_MINUS = 50.0

BAND_FREE = "free"
BAND_INCLUDED = "included"
BAND_PAYG = "payg"


@dataclass(frozen=True)
class Row:
    """One printable line of the console list."""

    model: Model
    name: str  # display label: variant and [free] applied
    price: float  # representative cost, $/1M (0 if free)
    free: bool
    band: str
    score: float | None  # primary family score, else secondary
    benchmark: str | None  # the key `score` came from
    grade: str = "o"  # rank marker: ++ / + / o / -- / ·
    provider: str = "-"  # zen / go, "-" when in neither tier
    entry: object | None = None  # pricing.PriceEntry when doc-priced
    synthetic: bool = False  # roster-only row: offered, so always listed


def _best(
    scores: dict[str, float],
    family: str,
    skip: str | None = None,
    versioned: bool = False,
) -> tuple[str, float] | None:
    """Highest score under *family*, as ``(key, value)``.

    Families are matched with spaces folded to dashes so ``Terminal Bench
    2.1`` and ``Terminal-Bench 2.1`` count as the same benchmark.

    *skip* excludes keys on a harder scale of the same family — a 4.0 score
    must not win a slot that is compared against 2.x numbers.

    *versioned* restricts the family to actual version runs: after the
    family prefix a version number must follow. Terminal-Bench Hard and
    Terminal-Bench-Science are separate benchmarks that happen to share the
    prefix — a 20.5 on TB Hard is not a "Terminal-Bench 2.x" run and must
    never fill a slot compared against 2.1 board numbers.
    """
    best = None
    folded_skip = skip.lower().replace(" ", "-") if skip else None
    folded_family = family.lower().replace(" ", "-")
    for key, value in scores.items():
        folded = key.lower().replace(" ", "-")
        if not folded.startswith(folded_family):
            continue
        if versioned and not _version_run(folded[len(folded_family) :]):
            continue
        if folded_skip and folded.startswith(folded_skip):
            continue
        if best is None or value > best[1]:
            best = (key, value)
    return best


def _version_run(after_family: str) -> bool:
    """True when what follows the family prefix is a version, not a word.

    ``-2.1`` and ``-3.0`` are version runs; ``-Hard`` and ``-Science-0.1``
    are different benchmarks sharing the prefix.
    """
    return after_family.lstrip("-_ ")[:1].isdigit()


def ratio(score: float, price: float) -> float:
    """Score per dollar; a free line beats everything rather than going inf..."""
    return float("inf") if price == 0 else score / price


def build_rows(
    models: list[Model],
    entries=(),
    free_ids=frozenset(),
    axis: str = "est",
    provider: str = "-",
    table: dict | None = None,
    est_rates: dict[str, float] | None = None,
    reach_variants: bool = True,
) -> list[Row]:
    """Price every line of the console list.

    A documented line replaces the catalog price (the catalog has no notion
    of tier: it carries the Zen rate for a model Go sells cheaper), and one
    model can produce several rows — one per priced variant. Free rows are
    really free: the export meters them at $0 and the throttle lives
    elsewhere (unpublished rate limits, not the dollar math), so they
    carry price 0, read "-" for score/$, and sort ahead of every
    cost-based line.

    *axis* picks what a line costs: ``fix`` (20/60/20 in/cached/out),
    a single ``input`` / ``output`` rate, or ``est`` (the Go snapshot's
    page-estimated $/req, plan-invariant like the page prices). ``est``
    applies to Go and catalog lines; Zen lines and models missing from
    the snapshot fall back to ``fix``.
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
        if axis == "est" and provider != "zen" and est_rates:
            est = est_rates.get(model.model_id.split("/")[-1])
            if est is not None:
                return est
        if entry:
            return weighted_cost(entry.input, entry.output, entry.cached_read)
        return weighted_cost(
            model.price_input, model.price_output, model.price_cached_input
        )

    staged = []  # (model, entry, display name, cost, free, monthly limit)
    for m in models:
        slug = m.model_id.split("/")[-1]
        documented = list(by_model.get(slug, []))
        if reach_variants:
            # a free designation reaches the catalog base record, which
            # carries no variant of its own: the free offering still stages
            # a row with the callable free id. Synthetic roster models name
            # their own exact offering, so they need no reach.
            documented += by_model.get(f"{slug}-free", [])
        cost = cost_of(m, None)
        if not documented:
            if cost is None:
                continue  # no price at all — nothing to rank it on
            staged.append(
                (m, None, m.name, cost, m.model_id in free_ids or cost == 0, None)
            )
            continue
        seen = set()
        for entry in documented:  # one row per documented offering
            key = (entry.model_id, variant_of(entry.name))
            if key in seen:
                continue  # the same variant from a second tier is one line
            seen.add(key)
            suffix = variant_of(entry.name)
            label = f"{m.name} {suffix}" if suffix else m.name
            staged.append(
                (
                    m,
                    entry,
                    label,
                    cost_of(m, entry),
                    entry.free,
                    entry.limit,
                )
            )

    rows = []
    for model, entry, name, cost, free, limit in staged:
        if free:
            price, band = 0.0, BAND_FREE
            if "free" not in name.lower():
                name = f"{name} [free]"
        else:
            price = cost
            band = BAND_INCLUDED if limit is not None else BAND_PAYG
        # the row's score is the model's own: every benchmark it carries on
        # that benchmark's 0-100 scale, median across them. *table* is the
        # scale of the whole catalog, passed in so every view grades against
        # the same yardstick.
        derived = derived_scores(model, table or scales(models))
        score = combined(derived)
        strongest = max(derived, key=lambda k: derived[k]) if derived else None
        rows.append(
            Row(
                model=model,
                name=name,
                price=price,
                free=free,
                band=band,
                score=score,
                benchmark=strongest,
                provider=provider,
                entry=entry,
            )
        )
    return rows


def merge_rows(*groups: list[Row]) -> list[Row]:
    """Concatenate provider groups onto one free-price scale.

    Free rows already carry price 0 from build_rows, so there is nothing
    to re-price: the whole view agrees by construction. Provider tags
    and order are kept.
    """
    return [row for group in groups for row in group]


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
        providers = sorted(
            {p for side in (current.provider, row.provider) for p in side.split(",")}
        )
        merged[key] = replace(keep, provider=",".join(providers))
    return [merged[key] for key in keys]


def order_rows(rows: list[Row]) -> list[Row]:
    """Best score per dollar first; the rest cannot be valued.

    The score is already comparable across axes — each benchmark reads on
    its own 0-100 scale and the median combines them — so there is no
    skeleton to build and no percentile to insert at. Value order is the
    combined score divided by the price, ties toward the higher score, and
    a free scored line leads everything paid (infinite value sorts first,
    not last). Rows no benchmark reaches cannot be valued at all: free ones
    close the listing (they cost nothing whatever — maximising free usage
    means seeing them), paid ones follow behind, and rows nothing reaches
    and nobody sells free drop out.
    """
    scored = sorted(
        (r for r in rows if r.score is not None),
        key=lambda r: (-ratio(r.score, r.price), -r.score),
    )
    listed = {id(r) for r in scored}
    tail = [
        r
        for r in rows
        if r.score is None
        and id(r) not in listed
        and (r.free or _measured(r) or r.synthetic)
    ]
    free = [r for r in tail if r.free]
    paid = [r for r in tail if not r.free]
    return scored + free + paid


def _measured(row: Row) -> bool:
    """Whether any scored benchmark reaches this row's model."""
    return any(tracked(name) for name in row.model.scores)


def axis_scores(model: Model) -> dict[str, float | None]:
    """Each axis's raw score for *model*, None where it carries none.

    The console prints these side by side so the viewer sees the base
    scores the ordering is built from instead of one merged number.
    """
    out: dict[str, float | None] = {}
    for key, _label, family, versioned in AXES:
        best = _best(
            model.scores,
            family,
            skip=TB4_FAMILY if key == "tb2" else None,
            versioned=versioned,
        )
        out[key] = best[1] if best else None
    return out


def grade_for(score: float | None) -> str:
    """One score's marker: the band it clears, or ``·`` with no score."""
    if score is None:
        return "·"
    if score >= GRADE_PLUSPLUS:
        return "++"
    if score >= GRADE_PLUS:
        return "+"
    if score >= GRADE_O:
        return "o"
    if score >= GRADE_MINUS:
        return "-"
    return "--"


def grade_rows(rows: list[Row]) -> list[Row]:
    """Tag every row with a marker for how strong it is, keeping the order.

    The bands come from the *score*, not from position in the table. Price
    sorts the table — that is the Pareto view — but a model that scores well
    should not be graded "middle" for costing money, and a cheap model
    should not be graded "top" for being cheap.

    Rows with no score are unranked rather than last: they show ``·`` for
    insufficient benchmark data, so a model nobody benchmarks is not
    mistaken for the bottom — whether the catalog knows it or not.
    """
    return [replace(r, grade=grade_for(r.score)) for r in rows]


#: How much weaker than the strongest model known a pick may be, as a
#: fraction of its score.
#:
#: Eligibility is deliberately *strength*, not the rank marker. The marker
#: is a position in an ordering that divides by price, so it measures value:
#: in the live ordering MiMo-V2.6-Pro scores 89.9 — the best model in the
#: catalog — and is graded ``o``, while a free model scoring 24.58 is graded
#: ``++``. Gating on it recommends the weak model and misses the strong one.
RECOMMEND_FLOOR = 0.75


def next_up(
    rows: list[Row],
    count: int = 4,
    grade: str | None = None,
    floor: float = RECOMMEND_FLOOR,
) -> list[Row]:
    """The next *count* models to use, in order.

    Candidates alternate between the free and paid bands slot by slot —
    even slots take the best remaining free pick, odd slots the best
    remaining paid one — so a capped free tier still leaves half the
    sequence usable, and a fresh one still leads with what costs nothing.
    Free picks order by score, since they cost nothing and only the score
    is left to maximise; paid picks order by score per dollar, the measure
    the list view already ranks on.

    That order is then alternated between Zen and Go wherever both plans
    can supply a pick, so following the sequence spreads across the two
    instead of draining one allowance before touching the other. Plan
    alternation is a preference, not a rule: it gives way when the wanted
    band has nothing on the other plan, and band alternation gives way
    when a band runs dry — the other band fills the slot instead.

    Eligibility is strength, not the rank marker: a pick must be within
    *floor* of the strongest score in *rows*, and may be narrowed further by
    a *grade*. A short list is a short list, not an error.
    """
    scored = [r for r in rows if r.score is not None]
    if not scored:
        return []
    # the reference is the strongest model in the whole catalog, so the bar
    # cannot be dragged down by a weak set of top rows
    best = max(r.score for r in scored)
    eligible = [r for r in scored if r.score >= best * floor]
    if grade:
        eligible = [r for r in eligible if r.grade == grade]
    free = sorted((r for r in eligible if r.free), key=lambda r: (-r.score, r.name))
    paid = sorted(
        (r for r in eligible if not r.free),
        key=lambda r: (-ratio(r.score, r.price), -r.score, r.name),
    )
    # Band alternation is the slot order; the plan alternation inside each
    # slot prefers a plan the previous pick did not touch.
    return _take_banded(free, paid, count)


def plans_of(row: Row) -> set[str]:
    """The plans a row is sold on; a shared offer counts for each of them.

    ``dedupe_providers`` labels an offer both tiers sell at the same rate as
    ``"go,zen"``. Treating that as a plan of its own would leave it out of
    the alternation entirely, so it is read as membership of both.
    """
    return set(row.provider.split(","))


def _take_banded(free: list[Row], paid: list[Row], want: int) -> list[Row]:
    """Up to *want* rows, alternating free and paid slots.

    One model is recommended once, however many lines it appears on: free on
    Zen and paid on Go are two lines for one model, and spending two slots
    on it wastes a recommendation. Even slots draw from the free pool, odd
    slots from the paid pool; a dry band yields its slot to the other pool
    rather than ending the sequence short.

    Within a slot the plan alternates too: the first unseen row on a plan
    the previous pick did not touch wins, falling back to the strongest
    unseen row in the pool when every plan left touches it. The leading pick
    is the strongest free row available, whichever plan it sits on. A plan
    with nothing left is simply passed over — there is nothing to
    alternate with.
    """
    pools = [list(free), list(paid)]
    seen: set[str] = set()
    taken: list[Row] = []
    previous: set[str] = set()
    for slot in range(want):
        first, second = pools if slot % 2 == 0 else pools[::-1]
        row = (
            _off_plan(first, seen, previous)
            or _first_unseen(first, seen)
            or _off_plan(second, seen, previous)
            or _first_unseen(second, seen)
        )
        if row is None:
            break
        taken.append(row)
        seen.add(canonical_id(row.model.model_id))
        previous = plans_of(row)
    return taken


def _first_unseen(pool: list[Row], seen: set[str]) -> Row | None:
    """The strongest row in *pool* for a model not yet recommended."""
    for row in pool:
        if canonical_id(row.model.model_id) not in seen:
            return row
    return None


def _off_plan(pool: list[Row], seen: set[str], previous: set[str]) -> Row | None:
    """The strongest unseen row in *pool* on a fresh plan.

    Nothing is fresh before the first pick, so the lead always falls
    through to the strongest row outright.
    """
    for row in pool:
        if (
            canonical_id(row.model.model_id) not in seen
            and not plans_of(row) & previous
        ):
            return row
    return None


def partition_rows(
    rows: list[Row], fraction: float = CULL_FRACTION
) -> tuple[list[Row], list[Row], list[Row]]:
    """Split into (best value, worst score, highest cost).

    The split always happens: the worst-scoring and priciest models are
    taken out of best value whether or not a caller goes on to print them.
    A view that shows one table is still a view that hid those rows, which is
    the point of the cull.

    Each cull takes the nearest whole row to *fraction* of the list (one row
    minimum once there is more than one, never on a single row). The groups
    are disjoint and the cost group has first claim: a model that is both
    priciest and lowest-scoring is reported once, under cost.
    """
    count = len(rows)
    take = 0 if count <= 1 else max(1, round(fraction * count))

    expensive = sorted(
        range(count),
        key=lambda i: (
            -rows[i].price,
            rows[i].score if rows[i].score is not None else float("inf"),
        ),
    )[:take]
    claimed = set(expensive)

    rest = [i for i in range(count) if i not in claimed]
    weakest = sorted(
        rest,
        key=lambda i: (
            rows[i].score if rows[i].score is not None else float("inf"),
            -rows[i].price,
        ),
    )[:take]
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
