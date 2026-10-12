"""Which models the plans actually offer right now.

The membership lists under ``opencode_rank/sources/`` are committed text,
hand-maintained, and they drift in both directions: a model the provider
unlisted stays in the list, and a newly offered model is missing from it. In
the live data 13 Go slugs were still being offered to the console after the
provider's own docs stopped listing them, two of them models we were actively
trying to benchmark.

``fetch`` already snapshots the tier documentation for prices, and every row
of that snapshot names the model the provider is offering. That snapshot is
the availability record, so it is what this module reads: the same source,
the same refresh, and current by construction.

A tier with no snapshot is *unknown*, not empty. Restricting the console to
an empty set because nobody has fetched a snapshot would hide everything, and
the shipped lists are still the best answer available offline - so the
callers treat unknown as "no restriction" and the console says so.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import sources


def _snapshot_ids(data_dir: Path, tier: str) -> set[str] | None:
    """Model ids the provider's tier snapshot lists, or None if unknown."""
    path = Path(data_dir) / "tier_prices" / f"{tier}_prices.json"
    if not path.exists():
        return None
    try:
        rows = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return {row["model_id"] for row in rows if row.get("model_id")}


def load_available(data_dir: Path) -> dict[str, set[str] | None]:
    """Model ids each plan offers, by tier; None where there is no snapshot.

    None means "we have not fetched this tier", which is different from an
    empty set, which means "the plan offers nothing".
    """
    return {tier: _snapshot_ids(data_dir, tier)
            for tier in sources.available_sources()}


def available_ids(data_dir: Path) -> set[str] | None:
    """Every model id any known plan offers, or None if nothing is known."""
    known = [ids for ids in load_available(data_dir).values() if ids is not None]
    return set().union(*known) if known else None


def offered(model_id: str, ids: set[str]) -> bool:
    """Whether *model_id* is in the offered set *ids*.

    The tier pages list free variants under their own slug
    (``step-5-preview-free``) while the catalog carries one model under the
    base slug (``stepfun/step-5-preview``) — the same reach
    :func:`sources.members` implements. Exact matching drops such a model
    from the pool entirely, which is how every free row once vanished from
    the list view.
    """
    slug = model_id.split("/")[-1]
    return (slug in ids or model_id in ids
            or f"{slug}-free" in ids or f"{model_id}-free" in ids)


def unlisted(data_dir: Path) -> dict[str, set[str]]:
    """Slugs the shipped lists carry that the provider no longer offers.

    Worth surfacing rather than silently dropping: a model that quietly
    disappears from a plan is a change the console should report, not one a
    reader has to notice is missing.
    """
    dropped: dict[str, set[str]] = {}
    for tier, ids in load_available(data_dir).items():
        if ids is None:
            continue                      # no snapshot: nothing was dropped
        missing = sources.load_source(tier) - ids
        if missing:
            dropped[tier] = missing
    return dropped
