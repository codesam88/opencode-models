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

Provider plan-option suffixes are stripped to find the base model. The suffixes
``-free`` and ``-contributor`` on the slug (the last path segment of
``lab/slug``) are plan designations, not different weights: a free or
contributor variant carries the same benchmark scores as its base. The suffixes
are stripped repeatedly, so ``X-contributor-free`` folds to ``X``, and only
when the remainder is non-empty (a bare suffix is left alone). A designation
with no twin in the catalog keeps its own identity rather than vanishing.
"""

from __future__ import annotations

from dataclasses import replace

from .models import Model

#: Provider plan-option suffixes on the slug segment. Stripped repeatedly by
#: :func:`canonical_id` to fold a variant onto its base model.
VARIANT_SUFFIXES: tuple[str, ...] = ("-free", "-contributor")


def _strip_suffixes(slug: str) -> str:
    changed = True
    while changed:
        changed = False
        for suffix in VARIANT_SUFFIXES:
            if slug.endswith(suffix):
                candidate = slug[: -len(suffix)]
                if candidate:
                    slug = candidate
                    changed = True
                    break
    return slug


def canonical_id(model_id: str) -> str:
    """The model *model_id* names, with any plan designation folded away."""
    if "/" in model_id:
        lab, slug = model_id.rsplit("/", 1)
        return f"{lab}/{_strip_suffixes(slug)}"
    return _strip_suffixes(model_id)


def plan_ids(model_id: str) -> set[str]:
    """Every catalog id that is this model, designation or not."""
    return {model_id, canonical_id(model_id)}


def merge_aliases(models: list[Model]) -> tuple[list[Model], list[str]]:
    """Fold designation records into the model they are a designation of.

    Benchmarks union (the plain record wins a disagreement, so a designation
    can never quietly restate a number), and the cheapest price is kept: the
    tier price tables replace it wherever they list the model, and where they
    do not, the price someone can actually pay is the honest one. Order is
    the catalog's, so the tables do not reshuffle. A designation with no twin
    keeps its own identity.

    Returns ``(merged, warnings)``: one warning per same-key value conflict,
    naming the canonical model and benchmark and which value was kept.
    """
    groups: dict[str, list[Model]] = {}
    order: list[str] = []
    for model in models:
        canonical = canonical_id(model.model_id)
        if canonical not in groups:
            groups[canonical] = []
            order.append(canonical)
        groups[canonical].append(model)

    merged: list[Model] = []
    warnings: list[str] = []
    for canonical in order:
        group = groups[canonical]
        if len(group) == 1:
            merged.append(group[0])
            continue
        plain = next((m for m in group if m.model_id == canonical), None)
        if plain is None:
            # no twin in the catalog: keep each designation's own identity
            merged.extend(group)
            continue
        variants = [m for m in group if m.model_id != canonical]
        scores, sources = dict(plain.scores), dict(plain.sources)
        for other in variants:
            for key, value in other.scores.items():
                if key in scores and scores[key] != value:
                    warnings.append(
                        f"{canonical}: conflicting {key} "
                        f"({other.model_id} {value!r} vs "
                        f"{plain.model_id} {scores[key]!r}); "
                        f"kept {plain.model_id}"
                    )
            scores = {**other.scores, **scores}  # plain record wins
            sources = {**other.sources, **sources}

        def _cheapest(values):
            known = [v for v in values if v is not None]
            return min(known) if known else None

        merged.append(
            replace(
                plain,
                price_input=_cheapest(
                    [plain.price_input] + [m.price_input for m in variants]
                ),
                price_output=_cheapest(
                    [plain.price_output] + [m.price_output for m in variants]
                ),
                price_cached_input=_cheapest(
                    [plain.price_cached_input]
                    + [m.price_cached_input for m in variants]
                ),
                scores=scores,
                sources=sources,
            )
        )
    return merged, warnings
