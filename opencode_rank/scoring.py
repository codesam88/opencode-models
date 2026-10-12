"""A model's score, on one scale the whole catalog can be compared on.

The raw benchmarks are not comparable:Terminal-Bench 2.1 runs 25–90,
Terminal-Bench 4.0 runs 16–65, SWE-bench Verified 35–96. Putting them in one
column and sorting on the result compares a 4.0 score against a 2.1 score
and calls the winner the better model.

So each benchmark gets its own 0–100 scale — its best model in this catalog
is 100, its worst is 0, and the distance between two models is proportionate
and linear in the raw score — and a model's score is the **median** of the
scales it has.

The median rather than the mean because models here carry wildly uneven
coverage: some have one benchmark, some have thirty. An average lets a model
with one good score outrank a frontier model that was evaluated properly,
and it makes the number swing as unrelated benchmarks appear. The median
answers "how does this model typically do", and one bad result cannot bury a
model that is strong on everything else.
"""

from __future__ import annotations

from statistics import median

from .data import Model
from .overrides import TIER_OFFICIAL, source_tier

#: A benchmark every model scored the same on carries no information, so it
#: normalises to the midpoint rather than to 100.
NEUTRAL = 50.0

#: How much of a benchmark's numbers must be checkable before they are
#: allowed to set the ends of its scale.
#:
#: The rule that a vendor self-report cannot set the floor exists to stop one
#: uncited number from compressing everyone else's score. Applied blindly it
#: causes the opposite fault: on Terminal-Bench 2.1 only 8 of 50 numbers
#: come from a board, and scaling the axis to those turned it into a
#: threshold - bounds of 64.3-73.4, so nearly every model clamped to 0 or
#: 100 and the primary axis stopped discriminating. A scale drawn from a
#: small minority is a threshold, not a scale, so the ends stand aside until
#: the checkable numbers are a real share of the field.
CHECKABLE_SHARE = 0.25

#: How many benchmarks a model needs before its score means anything.
#:
#: The median of one number is that number. A model measured on one
#: benchmark it happens to lead reads as a top model on the strength of a
#: single result - in the live catalog 26 of 63 scored models have one
#: benchmark and 6 of the top 9 rows are among them, one of them leading the
#: whole catalog on a lone Terminal-Bench 4.0 run. Two independent axes is
#: the smallest amount of evidence that is not one measurement, and it costs
#: coverage deliberately: a model with one number is unscored, not ranked
#: last.
MIN_AXES = 2

#: The benchmarks this tool scores on, as an explicit list.
#:
#: Everything else the catalog mentions is context, not signal. The catalog
#: carries 212 benchmark names, of which 137 are held by one or two models -
#: a niche evaluation nobody else ran. Counted, they hand a model a hard 0
#: (the worst of four is 0) and drag its median down: a model resolving 82 on
#: SWE-bench Verified was landing on 0 because of a 17.7 on SEC-Bench Pro.
#:
#: A prefix rule was the obvious way to write this and it was wrong in both
#: directions. Matching "Terminal-Bench" also swept in "Terminal-Bench Hard",
#: "-Science" and 3.0, which are separate task sets rather than runs of the
#: benchmark; matching "SWE-Bench" admitted Lite, Multimodal and
#: Multilingual. Each of those opens another axis in the scoring equation
#: while adding no model that could not otherwise be scored: Lite and
#: Multimodal reach no tier model at all, Multilingual is only ever carried
#: alongside Verified or Pro, and 3.0 has two. Naming the benchmarks instead
#: keeps adding one a deliberate act, with a reason attached.
#:
#: An older revision is left out as well. Terminal-Bench 2.0 is 2.1 with an
#: easier, older task set, and a public task set gets trained against: a
#: vendor can publish a 2.0 number without the model being able to do the
#: work. Keeping it costs no coverage either - the five tier models whose
#: only Terminal-Bench run is a 2.0 all carry SWE-bench - so there is
#: nothing to weigh against it. Elo is left out too: it is preference votes
#: on chat answers rather than task completion, and no model in the catalog
#: carries it.
#:
#: DeepSWE 1.1 earns its place by being the only thing that pairs with the
#: rest. It is the broadest benchmark carried by models that hold nothing
#: else scored - nine of the 25 offered models on a single axis carry it -
#: so admitting it is what makes those models rankable at all. It is also
#: the weakest entry on provenance and on signal, and both facts are worth
#: stating plainly: every one of its 29 numbers is a vendor self-report (no
#: board runs it), and its rank correlation with Terminal-Bench 2.1 over the
#: models carrying both is 0.87, so it mostly re-ranks what TB 2.1 already
#: says rather than adding an independent read. A median over five
#: benchmarks therefore leans on TB 2.1 twice.
#:
#: The bare "DeepSWE" name stays out: it shares no model with "DeepSWE 1.1"
#: in the catalog, so it is an older revision carrying four models.
#:
#: The order is also the order the benchmarks are shown in: the axis the
#: console ranks on first, then the axes that fill in.
_TRACKED = ("Terminal-Bench 2.1", "Terminal-Bench 4.0",
            "SWE-Bench Verified", "SWE-Bench Pro", "DeepSWE 1.1")


def _folded(name: str) -> str:
    """The benchmark name with dashes read as spaces, whatever the registry.

    Two registries spell these both ways ("Terminal-Bench 2.1",
    "Terminal Bench 2.1") for one benchmark, and they have to land on one
    scale and in one column rather than in two near-empty ones.
    """
    return name.replace("-", " ")


_TRACKED_FOLDED = frozenset(_folded(name) for name in _TRACKED)


def tracked(name: str) -> bool:
    """Whether *name* is one of the benchmarks we score on."""
    return _folded(name) in _TRACKED_FOLDED


def canonical(name: str) -> str:
    """*name* with the dash and space spellings folded into one benchmark.

    Labs spell these both ways - "Terminal-Bench 2.1" and "Terminal Bench 2.1"
    are one benchmark from two registries - and they belong side by side in
    one column rather than in two near-empty ones. Untracked names are
    returned untouched, which is what keeps "Terminal-Bench-Science 0.1" out
    of Terminal-Bench's column.
    """
    return _folded(name) if tracked(name) else name


def scales(models: list[Model]) -> dict[str, tuple[float, float]]:
    """The 0-100 range of each tracked benchmark, ``(lowest, best)``.

    Over the models that actually carry it: the best model in this catalog
    is 100 and the lowest is 0, so the ends move when the catalog does.

    The ends are set by numbers someone else can check - an official board
    or an independent evaluator - and not by vendor self-reports. Min-max is
    only as good as its extremes: with every number counted, one uncited
    1.5 from a launch post was the floor of the Terminal-Bench 4.0 scale, so
    64.85 at the top and 1.5 at the bottom stretched the range and
    compressed every model's derived score into the middle. A benchmark with
    no board or independent number at all keeps its own ends, since
    tightening them would leave those models scored against nothing.

    A benchmark is keyed by its own name, but its bounds are pooled across
    the spellings registries use for it - one benchmark means one scale, so
    a model measured by one registry is not the worst of a pair with a model
    measured by another.
    """
    all_values: dict[str, list[float]] = {}
    checkable: dict[str, list[float]] = {}
    names: set[str] = set()
    for model in models:
        for name, value in model.scores.items():
            if not (tracked(name) and isinstance(value, (int, float))):
                continue
            key = canonical(name)
            names.add(name)
            all_values.setdefault(key, []).append(float(value))
            if source_tier(model.sources.get(name)) <= TIER_OFFICIAL:
                checkable.setdefault(key, []).append(float(value))
    tight = {key: (min(values), max(values))
             for key, values in checkable.items()
             if len(values) >= CHECKABLE_SHARE * len(all_values[key])}
    bounds = {key: tight.get(key) or (min(values), max(values))
              for key, values in all_values.items()}
    return {name: bounds[canonical(name)] for name in sorted(names)}


def normalise(score: float, bounds: tuple[float, float]) -> float:
    """One raw score onto its benchmark's 0–100 scale.

    Linear and clamped: a score outside the catalog's range — a benchmark
    fetched after the snapshot, a vendor's own figure — reads 0 or 100
    rather than extrapolating past the ends.
    """
    floor, ceiling = bounds
    if ceiling <= floor:
        return NEUTRAL
    pct = (score - floor) / (ceiling - floor) * 100.0
    return min(100.0, max(0.0, pct))


def derived_scores(model: Model,
                   table: dict[str, tuple[float, float]]) -> dict[str, float]:
    """Each benchmark we score on that this model carries, on its 0–100 scale.

    Benchmarks outside the shortlist never appear: they are not in *table*,
    which ``scales`` builds from ``_TRACKED`` alone. A benchmark with no
    spread across the catalog is left out too - it cannot say anything about
    the model, and counting it at the midpoint would pull a real median
    toward the middle for no reason.
    """
    return {name: normalise(value, bounds)
            for name, value in model.scores.items()
            if name in table and isinstance(value, (int, float))
            and (bounds := table[name])[1] > bounds[0]}


def combined(derived: dict[str, float]) -> float | None:
    """The model's score: the median of its derived scores, or None.

    None below ``MIN_AXES``, which is what makes a thinly measured model
    unranked rather than ranked on one number.
    """
    if len(derived) < MIN_AXES:
        return None
    return median(derived.values())


def score_of(model: Model,
             table: dict[str, tuple[float, float]]) -> float | None:
    """:func:`combined` of :func:`derived_scores`, in one call."""
    return combined(derived_scores(model, table))