"""Shared row pipeline for ``list`` and ``next``.

One service builds, merges and dedupes the rows both commands render, so a
recommendation can never disagree with the ranking it was drawn from. It owns
roster loading per selected provider, catalog-pool assembly (including
synthetic scoreless roster models for docs-documented slugs), availability
filtering via :func:`opencode_models.availability.current_offered`, est-rate
loading from the plan snapshot, the whole-catalog scales, and the
``build_rows`` / ``merge_rows`` / ``dedupe_providers`` orchestration. It does
not render, grade or partition -- those stay with the commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from . import availability
from .allowance import cost_per_request
from .catalog import load_catalog
from .identity import canonical_id
from .models import Model
from .pricing import load_prices
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

    def __iter__(self):
        yield self.rows
        yield self.listed
        yield self.curated
        yield self.counts
        yield self.tier_slugs
        yield self.models
        yield self.warnings


def _entries_for(source: str, data_dir: Path):
    """Documented pricing lines for a tier (Go first: interchangeable, and
    its rates are the ones with included usage attached)."""
    prices = load_prices(data_dir / "tier_prices")
    if source in prices:
        return prices[source]
    if source == "all":
        return prices.get("go", []) + prices.get("zen", [])
    return []


def _docs_known_slugs(data_dir: Path, tier: str) -> set[str]:
    """Slugs a tier's documentation knows about (tier_prices or plans)."""
    out: set[str] = set()
    snap = availability._snapshot_ids(data_dir, tier)
    if snap:
        out |= set(snap)
    out |= set(availability._plan_models(data_dir, tier))
    return out


def _synthesize_roster_models(
    data_dir: Path, tier: str, roster: set[str], models: list
) -> list:
    """Scoreless catalog records for docs-documented roster slugs with no catalog match.

    The roster is the source of truth for what is offered; the catalog only
    identifies and extends. A slug the plans offer and the docs describe but
    the catalog never carried (e.g. ``beam-free``) still renders, as an
    unscored row. Live-only or internal slugs with no docs record are
    excluded here -- that is the junk filter.
    """
    plan_models = availability._plan_models(data_dir, tier)
    entries = load_prices(data_dir / "tier_prices").get(tier, [])
    entry_by_slug: dict[str, object] = {}
    for entry in entries:
        if entry.model_id and entry.model_id not in entry_by_slug:
            entry_by_slug[entry.model_id] = entry
    catalog_canonical = {canonical_id(m.model_id.split("/")[-1]) for m in models}
    aliases = availability.load_aliases(data_dir)
    catalog_by_id = {m.model_id: m for m in models}
    out: list[Model] = []
    for slug in sorted(roster):
        if slug not in _docs_known_slugs(data_dir, tier):
            continue
        if canonical_id(slug.split("/")[-1]) in catalog_canonical:
            continue
        rec = plan_models.get(slug)
        name = rec.get("name") if isinstance(rec, dict) else None
        entry = entry_by_slug.get(slug)
        if not name and entry is not None:
            name = entry.name
        if not name:
            name = slug
        if entry is not None:
            price_input = entry.input
            price_output = entry.output
            price_cached_input = entry.cached_read
        else:
            price_input = 0.0
            price_output = 0.0
            price_cached_input = 0.0
        lab = slug.split("/")[0] if "/" in slug else slug
        scores: dict = {}
        sources: dict = {}
        alias = aliases.get(slug)
        if isinstance(alias, dict):
            target_id = alias.get("model_id")
            target = catalog_by_id.get(target_id) if target_id else None
            if target is not None:
                scores = {**target.scores, **scores}
                sources = {**target.sources, **sources}
        out.append(
            Model(
                model_id=slug,
                name=name,
                lab=lab,
                price_input=price_input,
                price_output=price_output,
                price_cached_input=price_cached_input,
                scores=scores,
                sources=sources,
            )
        )
    return out


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
    models, curated, _board_conflicts = load_catalog(data_dir)
    avail = availability.current_offered(data_dir, bypass=all_models)
    on_offer = avail.offered
    warnings: list[str] = list(avail.warnings)
    # page-estimated $/req per snapshot slug (the `est` cost base);
    # Zen lines and snapshot-missing models fall back to the fix
    est_rates = None
    if price == "est":
        try:
            plan_snapshot, _origin = availability.load_plan_snapshot(data_dir)
        except (OSError, ValueError):
            plan_snapshot = {}
        est_rates = {}
        for slug, rec in (plan_snapshot.get("models") or {}).items():
            cpr = cost_per_request(rec.get("tiers") or [], rec.get("assumptions"))
            if cpr is not None and cpr > 0:
                est_rates[slug] = cpr
    # one scale for the whole catalog: per-tier scales would make the same
    # score mean two different things in the merged view
    table = scales(models)
    groups, listed, counts, tier_slugs = [], [], {}, {}
    covered: set[str] = set()
    synthetic_ids: set[int] = set()
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
            pool, base_free = availability.members(models, tier_slugs[provider])
            catalog_pool = list(pool)
            catalog_ids = {id(m) for m in catalog_pool}
            pool = catalog_pool + _synthesize_roster_models(
                data_dir, provider, tier_slugs[provider], models
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
            counts[provider] = sum(1 for m in pool if id(m) in catalog_ids)
            covered |= {m.model_id for m in pool}
            free_ids &= {m.model_id for m in pool}
            provider_listed = [m for m in pool if id(m) in catalog_ids]
            synthetic_ids |= {id(m) for m in pool if id(m) not in catalog_ids}
        listed += provider_listed
        provider_entries = _entries_for(provider, data_dir)
        catalog_rows = [m for m in pool if id(m) not in synthetic_ids]
        groups.append(
            build_rows(
                catalog_rows,
                provider_entries,
                free_ids,
                axis=price,
                provider=provider,
                table=table,
                est_rates=est_rates,
            )
        )
        synth_rows = [m for m in pool if id(m) in synthetic_ids]
        if synth_rows:
            # synthetic models name their own exact offering: no variant
            # reach, or a free twin would stage twice under two names.
            groups.append(
                build_rows(
                    synth_rows,
                    provider_entries,
                    free_ids,
                    axis=price,
                    provider=provider,
                    table=table,
                    est_rates=est_rates,
                    reach_variants=False,
                )
            )
    rows = dedupe_providers(merge_rows(*groups))
    rows = [
        replace(r, synthetic=True) if id(r.model) in synthetic_ids else r for r in rows
    ]
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
