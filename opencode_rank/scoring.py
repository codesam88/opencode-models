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

#: A benchmark every model scored the same on carries no information, so it
#: normalises to the midpoint rather than to 100.
NEUTRAL = 50.0

#: The benchmarks this tool ranks on. Everything else the catalog mentions
#: is context, not signal.
#:
#: The catalog carries 212 benchmark names, of which 164 are held by one or
#: two models — a niche evaluation nobody else ran. Counted, they hand a
#: model a hard 0 (the worst of four is 0) and drag its median down: a model
#: resolving 82 on SWE-bench Verified was landing on 0 because of a 17.7 on
#: SEC-Bench Pro. The axes are Terminal-Bench 2.x and 4.0, the SWE-bench
#: subsets, and arena Elo — the ones the console orders on.
#:
#: Terminal-Bench needs care: "Hard" and "-Science" share the prefix and are
#: separate benchmarks, not runs of it, so only a version number counts.
#: Terminal-Bench needs care: "Hard" and "-Science" share the prefix and
#: are separate benchmarks, not runs of it, so only a version number counts.
#: The SWE-bench subsets are named in words ("Verified", "Multilingual",
#: "Lite", "Pro"), so any suffix there is a subset and counts.
_TB_PREFIXES = ("Terminal-Bench", "Terminal Bench")
_SWE_PREFIXES = ("SWE-Bench", "SWE Bench")


def tracked(name: str) -> bool:
    """Whether *name* is one of the benchmarks this tool scores on."""
    if name == "LMArena Elo":
        return True
    if name.startswith(_SWE_PREFIXES):
        return True
    for prefix in _TB_PREFIXES:
        if name.startswith(prefix):
            rest = name[len(prefix):].strip()
            return bool(rest) and rest[0].isdigit()
    return False


def scales(models: list[Model]) -> dict[str, tuple[float, float]]:
    """The 0–100 range of each tracked benchmark, ``(lowest, best)``.

    Over the models that actually carry it: the best model in this catalog
    is 100 and the lowest is 0, so the ends move when the catalog does.
    """
    seen: dict[str, list[float]] = {}
    for model in models:
        for name, value in model.scores.items():
            if tracked(name) and isinstance(value, (int, float)):
                seen.setdefault(name, []).append(float(value))
    return {name: (min(values), max(values)) for name, values in seen.items()}


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
    """Every benchmark this model carries, on its benchmark's 0–100 scale.

    A benchmark with no spread across the catalog is left out: it cannot
    say anything about the model, and counting it at the midpoint would
    pull a real median toward the middle for no reason.
    """
    return {name: normalise(value, bounds)
            for name, value in model.scores.items()
            if name in table and isinstance(value, (int, float))
            and (bounds := table[name])[1] > bounds[0]}


def combined(derived: dict[str, float]) -> float | None:
    """The model's score: the median of its derived scores, or None."""
    return median(derived.values()) if derived else None


def score_of(model: Model,
             table: dict[str, tuple[float, float]]) -> float | None:
    """:func:`combined` of :func:`derived_scores`, in one call."""
    return combined(derived_scores(model, table))