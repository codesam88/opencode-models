"""Which model a catalog id really names.

A designation is not an identity. ``meta/muse-spark-1.3`` and
``meta/muse-spark-1.3-contributor`` are the same weights on two plans: eleven
identical benchmark values, none differing, $1.25 in on Zen and $0.10 in on
Go. The catalog lists them as two models, so the console showed two rows for
one model - and two different scores, 99 and 98, because the contributor
record happened to lack a Terminal-Bench 4.0 run its twin carried. A
data-completeness artefact, not a difference between models.

So the benchmarks merge into one model, the score is computed once, and each
plan keeps its own priced line - which is exactly how a priced variant like
``(Off-Peak)`` already works. What changes is that the alias has to be known
in three places at once: the model's own record, the tier price entries keyed
by id, and the availability record. Getting two of the three right still
splits the model, so all three go through :func:`canonical_id`.

The alias map is explicit rather than a suffix rule. ``-contributor`` is a
Meta programme today and means nothing anywhere else, and a suffix rule would
quietly merge a model whose name merely ends that way - while a designation
with no twin in the catalog keeps its own identity rather than vanishing.
"""

from __future__ import annotations

from dataclasses import replace

from .data import Model

#: Designation id → the model it is a designation of.
#:
#: Added only where the two records carry identical benchmark values, so the
#: merge cannot invent a difference. A pair that disagrees is a data problem
#: to look at, not an alias to paper over.
MODEL_ALIASES: dict[str, str] = {
    "meta/muse-spark-1.2-contributor": "meta/muse-spark-1.2",
    "meta/muse-spark-1.3-contributor": "meta/muse-spark-1.3",
}


def canonical_id(model_id: str) -> str:
    """The model *model_id* names, with any plan designation folded away."""
    return MODEL_ALIASES.get(model_id, model_id)


def plan_ids(model_id: str) -> set[str]:
    """Every catalog id that is this model, designation or not."""
    ids = {model_id}
    ids.update(alias for alias, plain in MODEL_ALIASES.items() if plain == model_id)
    return ids


def merge_aliases(models: list[Model]) -> list[Model]:
    """Fold designation records into the model they are a designation of.

    Benchmarks union (the plain record wins a disagreement, so a designation
    can never quietly restate a number), and the cheapest price is kept: the
    tier price tables replace it wherever they list the model, and where they
    do not, the price someone can actually pay is the honest one. Order is
    the catalog's, so the tables do not reshuffle.
    """
    known = {m.model_id: m for m in models}
    merged: list[Model] = []
    seen: set[str] = set()
    for model in models:
        plain = canonical_id(model.model_id)
        if plain in seen:
            continue                  # already emitted as part of its group
        twin = known.get(plain)
        group = [known[alias] for alias, target in MODEL_ALIASES.items()
                 if target == plain and alias in known] if twin else []
        seen.add(model.model_id)
        seen.update(m.model_id for m in group)
        if twin is None or not group:
            merged.append(model)      # no twin: renaming must not hide it
            continue
        scores, sources = dict(twin.scores), dict(twin.sources)
        for other in group:
            scores = {**other.scores, **scores}    # plain record wins
            sources = {**other.sources, **sources}
        merged.append(replace(
            twin,
            price_input=min([twin.price_input] + [m.price_input for m in group]),
            price_output=min([twin.price_output] + [m.price_output for m in group]),
            price_cached_input=min([twin.price_cached_input]
                                   + [m.price_cached_input for m in group]),
            scores=scores,
            sources=sources))
    return merged
