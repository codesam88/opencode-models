"""Which models the plans actually offer right now.

Availability resolves a tier's offered slugs newest-first along the chain
``available/`` (live snapshot) -> ``plans/`` (unified snapshot) ->
shipped plan fallback. ``load_roster``
returns that set; ``members`` filters the catalog to it and ``offered``
checks a model against a set of ids. A tier absent from every layer is
*unknown*, not empty, so callers treat unknown as "no restriction".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .auth import load_key
from .freshness import AVAILABILITY_MAX_AGE
from .freshness import snapshot_age as _snapshot_age
from .identity import canonical_id
from .live import (
    LIVE_CONFIG_URL,
    api_get,
    fetch_live,
    fetch_served,
    load_live,
    merge_served,
    save_live,
)
from .models import Model


def _snapshot_ids(data_dir: Path, tier: str) -> set[str] | None:
    """Model slugs the provider's unified plan snapshot lists, or None if unknown."""
    path = Path(data_dir) / "plans" / f"{tier}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    models = data.get("models")
    if not isinstance(models, dict):
        return None
    return set(models)


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

    Resolution order: live ``available/`` snapshot, then the unified
    ``plans/`` snapshot, then the shipped plan fallback. Unknown tiers raise
    ``KeyError``.
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
    """Slugs flagged free in the tier's unified plan snapshot."""
    free: set[str] = set()
    for slug, rec in _plan_models(data_dir, tier).items():
        if isinstance(rec, dict) and rec.get("free"):
            free.add(slug)
    return free


def load_aliases(data_dir: Path) -> dict:
    """Alias map: roster slug -> {"model_id", "source", "note"}.

    Schema: ``{"<roster slug>": {"model_id": "<catalog model id>",
    "source": "<url>", "note": "<why>"}}``. The shipped file (package
    data) is unioned with an optional runtime ``data/aliases.json``; the
    runtime entry wins on a slug conflict. Missing or unreadable files
    yield ``{}`` without raising.
    """
    out: dict = {}
    try:
        shipped = resources.files("opencode_models") / "data" / "aliases.json"
        data = json.loads(shipped.read_text())
        if isinstance(data, dict):
            out.update(data)
    except (json.JSONDecodeError, OSError, FileNotFoundError, TypeError):
        pass
    try:
        path = Path(data_dir) / "aliases.json"
        if path.is_file():
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                out.update(data)
    except (json.JSONDecodeError, OSError, TypeError):
        pass
    return out


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


@dataclass(frozen=True)
class AvailabilityResult:
    """Result of resolving current availability."""

    offered: set[str] | None
    warnings: tuple[str, ...]
    tiers: object
    origin: str | None
    unknown: tuple[str, ...]
    live: bool


def current_offered(data_dir, bypass=False):
    """Resolve which models the plans offer right now."""
    data_dir = Path(data_dir)
    warnings: list[str] = []

    if bypass:
        return AvailabilityResult(
            offered=None,
            warnings=tuple(warnings),
            tiers=None,
            origin=None,
            unknown=(),
            live=False,
        )

    key = load_key()
    tiers = None
    origin = None
    unknown: list[str] = []

    if key:
        _stale = False
        for _tier in ("go", "zen"):
            _age = _snapshot_age(data_dir, f"available/{_tier}")
            if _age is None or _age > AVAILABILITY_MAX_AGE:
                _stale = True
                break
        if _stale:
            try:
                get = api_get(key)
                served = fetch_served(get)
                known = fetch_live(get)
            except (OSError, ValueError, KeyError) as exc:
                warnings.append(
                    f"warning: live model list unreachable ({exc}) — using the last snapshots"
                )
            else:
                ids = set().union(*served.values()) if served else set()
                if not ids:
                    warnings.append(
                        "warning: no models served — using the last snapshots"
                    )
                else:
                    save_live(data_dir, merge_served(served, known))
                    if not served.get("go"):
                        warnings.append(
                            "warning: the Go plan returned no models — check that "
                            "models are enabled in the Console and that the key is a "
                            "personal one with model permissions "
                            "(User \u2192 Account \u2192 Personal API Keys)"
                        )
                    tiers = merge_served(served, known)
                    origin = f"live from {LIVE_CONFIG_URL} (recorded to {data_dir / 'available'})"
                    unknown = []
                    offered_ids = ids
                    return AvailabilityResult(
                        offered=offered_ids,
                        warnings=tuple(warnings),
                        tiers=tiers,
                        origin=origin,
                        unknown=tuple(unknown),
                        live=True,
                    )

    if not key:
        warnings.append(
            "no API token found - availability may be stale, some models "
            "may be unavailable (`auth` for live data, `--all` for the "
            "full catalog)"
        )

    known = load_available(data_dir)
    if all(ids is None for ids in known.values()):
        return AvailabilityResult(
            offered=None,
            warnings=tuple(warnings),
            tiers=None,
            origin=None,
            unknown=tuple(sorted(t for t, ids in known.items() if ids is None)),
            live=False,
        )
    tiers = {t: ids for t, ids in known.items() if ids is not None}
    unknown = sorted(t for t, ids in known.items() if ids is None)
    out: set[str] = set()
    for tier_ids in known.values():
        if isinstance(tier_ids, dict):
            out |= {slug for slug, served in tier_ids.items() if served is not False}
        elif tier_ids:
            out |= tier_ids
    origin = f"snapshots in {data_dir} (run `fetch` to refresh)"
    offered_ids = out or None
    return AvailabilityResult(
        offered=offered_ids,
        warnings=tuple(warnings),
        tiers=tiers,
        origin=origin,
        unknown=tuple(unknown),
        live=False,
    )


def load_plan_snapshot(data_dir) -> tuple[dict, str]:
    """The Go plan snapshot (limits, prices, assumptions) and its origin.

    Resolution order: the data dir's ``plans/go.json`` first -- that is
    the copy ``sync`` refreshes (``just plans-sync`` forces it) -- then
    the snapshot shipped inside the package, so an installed tool with
    no data dir still resolves. A corrupt local file falls through to
    the shipped one.
    """
    from importlib import resources

    data_dir = Path(data_dir)
    local = data_dir / "plans" / "go.json"
    if local.is_file():
        try:
            return json.loads(local.read_text()), "local"
        except (json.JSONDecodeError, OSError):
            pass
    shipped = resources.files("opencode_models") / "data" / "plans" / "go.json"
    return json.loads(shipped.read_text()), "shipped"


# Public surface of this module.
__all__ = [
    "available_ids",
    "free_slugs",
    "load_plan_snapshot",
    "load_available",
    "load_aliases",
    "load_roster",
    "members",
    "offered",
    "slug_matches",
    "AvailabilityResult",
    "current_offered",
]
