"""Shared row pipeline for ``list`` and ``next``.

One service builds, merges and dedupes the rows both commands render, so a
recommendation can never disagree with the ranking it was drawn from. It owns
roster loading per selected provider, the tier catalog from the models.dev
slice (cards when the slice was never fetched), availability filtering via
:func:`opencode_models.availability.current_offered`, est-rate loading
from the plan snapshot, the whole-catalog scales, and the
``build_rows`` / ``merge_rows`` / ``dedupe_providers`` orchestration. It does
not render, grade or partition -- those stay with the commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from . import availability, modelsdev
from . import sync as sync_mod
from .catalog import load_catalog
from .ranking import build_rows, dedupe_providers, merge_rows
from .scoring import scales


@dataclass(frozen=True)
class RowsResult:
    """Rows plus the supporting facts both commands report."""

    rows: list
    listed: list
    curated: object
    counts: dict
    tier_slugs: dict
    models: list
    warnings: tuple[str, ...] = field(default_factory=tuple)


def rows_for(
    data_dir,
    selected,
    price: str = "est",
    all_models: bool = False,
) -> RowsResult:
    """Build, merge and dedupe the rows for *selected* providers.

    Shared by ``list`` and ``next`` so a recommendation can never disagree
    with the ranking it was drawn from.

    Unless *all_models*, a row is a model the plans actually offer: the
    live config API is the record when keyed, else the snapshots. A model
    the plans dropped is neither listed nor scored. The scales are still
    built over the whole catalog, so a score does not move when a plan
    changes what it sells.
    """
    data_dir = Path(data_dir)
    models, curated, _board_conflicts, merge_warnings = load_catalog(data_dir)
    # The models.dev slice is this data dir's go/zen catalog. Without it
    # (fresh clone, offline) the cards decide, as they did before.
    slice_by_tier = modelsdev.load(data_dir)
    aliases = availability.load_aliases(data_dir)
    # one fallback mix for every est row: the Go plan's median token mix
    fallback_mix = availability.fallback_mix(data_dir)
    # a stale plan snapshot refreshes itself as one labelled job: its
    # report opens with a header and closes with a blank line, so it is
    # separated from this command's output instead of joining its warnings
    sync_mod.refresh_plan_as_job(data_dir, [p for p in selected if p in ("go", "zen")])
    avail = availability.current_offered(data_dir, bypass=all_models)
    on_offer = avail.offered
    warnings: list[str] = list(avail.warnings) + merge_warnings
    # one scale for the whole catalog: per-tier scales would make the same
    # score mean two different things in the merged view
    table = scales(models)
    groups, listed, counts, tier_slugs = [], [], {}, {}
    covered: set[str] = set()
    synthetic_ids: set[int] = set()
    # snapshot record id -> card id, so a row built from the slice is still
    # recognised where the catalog is keyed by card ids (curated markers)
    card_of: dict[str, str] = {}
    # slugs served/documented for every selected tier: a model listed by any
    # of them belongs to that tier (or is dropped by availability) and must
    # not be routed to the catalog-only "-" pool, even when the tier dropped it
    selected_tier_slugs: set[str] = set()
    for provider in selected:
        if provider != "-":
            selected_tier_slugs |= availability.load_roster(data_dir, provider)
    for provider in selected:
        if provider == "-":
            pool = [
                m
                for m in models
                if m.model_id not in covered
                and not any(
                    availability.slug_matches(m.model_id, s)
                    for s in selected_tier_slugs
                )
            ]
            free_ids = {m.model_id for m in pool if m.price_input == 0}
            provider_listed = list(pool)
        else:
            tier_slugs[provider] = availability.load_roster(data_dir, provider)
            links: dict[str, str] = {}
            if slice_by_tier is None:
                pool, base_free = availability.members(models, tier_slugs[provider])
            else:
                tier_models, links = modelsdev.to_models(
                    slice_by_tier.get(provider, {}), models, aliases
                )
                card_of.update(links)
                # the slice is the catalog: a served slug it does not carry
                # is not rendered. --all-models drops the availability
                # scoping and shows the whole catalog for the tier.
                pool, base_free = availability.members(
                    tier_models, None if all_models else tier_slugs[provider]
                )
            free_candidate_slugs = availability.free_slugs(data_dir, provider)
            free_ids = set(base_free) | {
                m.model_id
                for m in pool
                if any(
                    availability.slug_matches(m.model_id, s)
                    for s in free_candidate_slugs
                )
            }
            if on_offer is not None:
                pool = [m for m in pool if availability.offered(m.model_id, on_offer)]
                free_ids &= {m.model_id for m in pool}
            counts[provider] = len(pool)
            covered |= {m.model_id for m in pool}
            free_ids &= {m.model_id for m in pool}
            provider_listed = list(pool)
            if slice_by_tier is not None:
                # a slice row with no card record of its own still has to be
                # listed: nothing scores it, so the ranking would drop it
                synthetic_ids |= {id(m) for m in pool if m.model_id not in links}
        listed += provider_listed
        if provider in ("go", "zen"):
            # Single choke point for estimate math: the loader fills
            # priced records that publish no token mix with the Go plan's
            # median, so every tier — zen publishes none — estimates, and
            # each row carries the flag. The snapshot on disk is
            # untouched; validation keeps reading the raw files.
            plan_models, filled = availability.plan_records(data_dir, provider)
        else:
            plan_models = {}
            filled = set()
        catalog_rows = [m for m in pool if id(m) not in synthetic_ids]
        groups.append(
            build_rows(
                catalog_rows,
                records=plan_models,
                free_ids=free_ids,
                axis=price,
                provider=provider,
                table=table,
                fallback_mix=fallback_mix,
                median_slugs=filled,
            )
        )
        synth_rows = [m for m in pool if id(m) in synthetic_ids]
        if synth_rows:
            # a slice row with no card names its own exact offering: no
            # variant reach, or a free twin would stage twice under two names
            groups.append(
                build_rows(
                    synth_rows,
                    records=plan_models,
                    free_ids=free_ids,
                    axis=price,
                    provider=provider,
                    table=table,
                    reach_variants=False,
                    fallback_mix=fallback_mix,
                    median_slugs=filled,
                )
            )
    rows = dedupe_providers(merge_rows(*groups))
    rows = [
        replace(r, synthetic=True) if id(r.model) in synthetic_ids else r for r in rows
    ]

    # Marker keys ride on the row: curated overlays from the catalog
    # composition, median estimates from the fallback mix. Views only
    # render them; the decision lives here, once.
    marked = []
    for r in rows:
        keys = []
        # a slice row carries the record id; the overlay is keyed by the
        # card id the record names
        if r.model.model_id in curated.curated or (
            card_of.get(r.model.model_id) in curated.curated
        ):
            keys.append("curated")
        if r.median:
            keys.append("median")
        marked.append(replace(r, markers=tuple(keys)) if keys else r)
    rows = marked
    return RowsResult(
        rows=rows,
        listed=listed,
        curated=curated,
        counts=counts,
        tier_slugs=tier_slugs,
        models=models,
        warnings=tuple(warnings),
    )


__all__ = ["RowsResult", "rows_for"]
