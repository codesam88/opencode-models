"""Which models the plans actually offer right now.

Availability resolves a tier's offered slugs newest-first along the chain
``available/`` (live snapshot) -> ``tier_prices/`` (docs snapshot) ->
``plans/`` (runtime snapshot) -> shipped plan fallback. ``load_roster``
returns that set; ``members`` filters the catalog to it and ``offered``
checks a model against a set of ids. A tier absent from every layer is
*unknown*, not empty, so callers treat unknown as "no restriction".
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from .identity import canonical_id
from .live import load_live
from .models import Model


def _snapshot_ids(data_dir: Path, tier: str) -> set[str] | None:
    """Model ids the provider's tier snapshot lists, or None if unknown."""
    path = Path(data_dir) / "tier_prices" / f"{tier}_prices.json"
    if not path.exists():
        return None
    try:
        rows = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(rows, list):
        return None
    return {
        row["model_id"] for row in rows if isinstance(row, dict) and row.get("model_id")
    }


def load_available(data_dir: Path) -> dict[str, set[str] | None]:
    """Model ids each plan offers, by tier; None where there is no snapshot.

    None means "we have not fetched this tier", which is different from an
    empty set, which means "the plan offers nothing". The live API snapshot
    wins wherever it exists — it is what the next request is checked
    against; the docs snapshot stays the offline fallback.
    """
    live = load_live(data_dir)
    tiers = {"go", "zen"} | set(live)
    out = {}
    for tier in sorted(tiers):
        ids = live.get(tier)
        out[tier] = ids if ids is not None else _snapshot_ids(data_dir, tier)
    return out


def available_ids(data_dir: Path) -> set[str] | None:
    """Every model id any known plan offers, or None if nothing is known."""
    known = [ids for ids in load_available(data_dir).values() if ids is not None]
    return set().union(*known) if known else None


def offered(model_id: str, ids) -> bool:
    """Whether *model_id* is in the offered set *ids*.

    Matching is canonical: a free or contributor variant reaches its base
    model, so a base catalog model is offered when any variant is.

    *ids* may be a set/list/tuple of slugs or a mapping (its keys are
    used); callers in this codebase always pass sets, but the mapping
    form is retained for compatibility with snapshot dicts.
    """
    id_set = ids.keys() if hasattr(ids, "keys") else ids
    return any(slug_matches(model_id, candidate) for candidate in (id_set or ()))


def _shipped_plan(tier: str) -> dict:
    shipped = resources.files("opencode_models") / "data" / "plans" / f"{tier}.json"
    try:
        return json.loads(shipped.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _plan_models(data_dir: Path, tier: str) -> dict:
    """Models mapping for *tier*: runtime snapshot first, then shipped."""
    local = Path(data_dir) / "plans" / f"{tier}.json"
    if local.is_file():
        try:
            data = json.loads(local.read_text())
            models = data.get("models") if isinstance(data, dict) else None
            if isinstance(models, dict):
                return models
        except (json.JSONDecodeError, OSError):
            pass
    shipped = _shipped_plan(tier)
    if isinstance(shipped, dict):
        return shipped.get("models") or {}
    return {}


def load_roster(data_dir: Path, tier: str) -> set[str]:
    """Slugs a tier offers, resolved newest-first.

    Resolution order: live ``available/`` snapshot, then ``tier_prices/``
    docs snapshot, then the runtime ``plans/`` snapshot, then the shipped
    plan fallback. Unknown tiers raise ``KeyError``.
    """
    if tier not in ("go", "zen"):
        raise KeyError(tier)
    live = load_live(data_dir)
    ids = live.get(tier)
    if ids is not None:
        if isinstance(ids, dict):
            return set(ids)
        if isinstance(ids, (list, tuple, set)):
            return set(ids)
        return set()
    snap = _snapshot_ids(data_dir, tier)
    if snap is not None:
        return snap
    return set(_plan_models(data_dir, tier))


def free_slugs(data_dir: Path, tier: str) -> set[str]:
    """Slugs flagged free in the tier's price/plan snapshots."""
    free: set[str] = set()
    path = Path(data_dir) / "tier_prices" / f"{tier}_prices.json"
    if path.exists():
        try:
            rows = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            rows = None
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("free") and row.get("model_id"):
                    free.add(row["model_id"])
    for slug, rec in _plan_models(data_dir, tier).items():
        if isinstance(rec, dict) and rec.get("free"):
            free.add(slug)
    return free


def slug_matches(model_id: str, candidate: str) -> bool:
    """Whether *model_id* and *candidate* name the same model.

    Matching is canonical on the slug segment: full id, bare slug, or any
    ``-free``/``-contributor`` variant of either side all agree.
    """
    if model_id == candidate:
        return True
    m_slug = model_id.split("/")[-1]
    c_slug = candidate.split("/")[-1] if "/" in candidate else candidate
    if m_slug == c_slug:
        return True
    if canonical_id(m_slug) == canonical_id(c_slug):
        return True
    if canonical_id(model_id) == canonical_id(candidate):
        return True
    return False


def members(models: list[Model], slugs: set[str] | None):
    """Tier membership: models in a tier, plus which are price-zero free.

    Free-ness from plan-option suffixes is not inferred here; callers add
    snapshot free flags separately. ``None`` slugs means every model.

    Matching is exact-wins: a roster slug matches the catalog model carrying
    that exact slug; the canonical (suffix-folded) group matches only when
    no catalog model carries the exact slug.
    """
    if slugs is None:
        return (list(models), {m.model_id for m in models if m.price_input == 0})
    model_slugs = {m.model_id.split("/")[-1] for m in models}

    def _matches(model_id: str, candidate: str) -> bool:
        m_slug = model_id.split("/")[-1]
        c_slug = candidate.split("/")[-1] if "/" in candidate else candidate
        if m_slug == c_slug:
            return True
        if c_slug in model_slugs:
            # an exact catalog record exists: canonical fallback disabled
            return False
        if canonical_id(m_slug) == canonical_id(c_slug):
            return True
        if canonical_id(model_id) == canonical_id(candidate):
            return True
        return False

    included = [m for m in models if any(_matches(m.model_id, s) for s in slugs)]
    free = {m.model_id for m in included if m.price_input == 0}
    return included, free


# Public surface of this module.
__all__ = [
    "available_ids",
    "free_slugs",
    "load_available",
    "load_roster",
    "members",
    "offered",
    "slug_matches",
]
