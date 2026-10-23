"""Pure functional core for the Go plan allowance model.

Equations and window fractions come from ``docs/model-go-plan.md``:
``cpr = (t_in*p_in + t_cache*p_cached + t_out*p_out) / 1e6`` ($/req),
``R_month = limit / cpr``, windows as fractions of the monthly limit.

The gate/merge logic here intentionally duplicates
``scripts/update_go_pricing.py`` (which stays standalone by design): the
script works on a live page snapshot while this module works on parsed
dicts handed in by callers. This module never reads files or the network.

This core's assumption-based ``cost_per_request`` is a different thing
from ``pricing.weighted_cost`` (the fixed-mix representative cost used
for ranking); do not conflate them.
"""

from __future__ import annotations

WINDOW_FRACTIONS = {"rolling": 0.2, "weekly": 0.5, "monthly": 1.0}


def cost_per_request(tiers: list[dict], assumptions: dict | None) -> float | None:
    """Cost per request in dollars, from the base tier (first in list).

    Returns None when there is nothing to price from: null assumptions
    (unpublished) or an empty tier list (free models).
    """
    if assumptions is None or not tiers:
        return None
    base = tiers[0]
    # absent cached pricing means no cache discount: cached reads are
    # billed at the input rate, as in pricing.weighted_cost
    cached = base["cached"] if base.get("cached") is not None else base["in"]
    return (
        assumptions["in"] * base["in"]
        + assumptions["cached"] * cached
        + assumptions["out"] * base["out"]
    ) / 1e6


def request_cap(monthly_limit: float | None, cpr_value: float | None) -> float | None:
    """Monthly request estimate: limit divided by cost per request."""
    if monthly_limit is None or cpr_value is None or cpr_value <= 0:
        return None
    return monthly_limit / cpr_value


def window_cap(
    monthly_limit: float | None, cpr_value: float | None, window: str
) -> float | None:
    """Request estimate for one allowance window."""
    if window not in WINDOW_FRACTIONS:
        raise ValueError(f"unknown window: {window!r}")
    monthly = request_cap(monthly_limit, cpr_value)
    if monthly is None:
        return None
    return WINDOW_FRACTIONS[window] * monthly


def remaining(limit: float, window: str, spend: float) -> float:
    """Dollars left in a window's allowance; floors at 0.0, never negative."""
    if window not in WINDOW_FRACTIONS:
        raise ValueError(f"unknown window: {window!r}")
    return max(0.0, WINDOW_FRACTIONS[window] * limit - spend)


def credit_ranking(models: dict[str, dict]) -> list[str]:
    """Slugs ordered by $/req ascending (beyond-included credit burn order).

    Free and unknown-cost models are excluded: free models are Unlimited
    and sit outside the dollar windows entirely, and models without
    published assumptions have no computable $/req to order by.
    """
    priced = []
    for slug, rec in models.items():
        if rec.get("free"):
            continue
        cpr = cost_per_request(rec.get("tiers", []), rec.get("assumptions"))
        if cpr is None or cpr <= 0:
            continue
        priced.append((cpr, slug))
    return [slug for _, slug in sorted(priced)]


def validate_estimates(
    models: dict[str, dict],
    tolerance: float = 0.10,
    known_exceptions: frozenset[str] = frozenset(),
) -> tuple[list[dict], bool]:
    """Reproduce published Go monthly estimates from limits+prices+assumptions.

    Returns (records, passed). Each record holds slug, computed, published,
    deviation (|computed-published|/published), and status: "ok", "failed",
    "excepted" (caller-supplied known exception, reported but never failed),
    or "unchecked" (free or assumption-less models carry no computable
    estimate and never fail). No exceptions are hardcoded here; the caller
    passes them. Only the Go plan is checked.
    """
    records = []
    for slug in sorted(models):
        rec = models[slug]
        limit = rec.get("limits", {}).get("go")
        published = rec.get("requests_per_month", {}).get("go")
        cpr = cost_per_request(rec.get("tiers", []), rec.get("assumptions"))
        computed = request_cap(limit, cpr)
        if computed is None or published is None:
            records.append(
                {
                    "slug": slug,
                    "computed": computed,
                    "published": published,
                    "deviation": None,
                    "status": "unchecked",
                }
            )
        elif slug in known_exceptions:
            records.append(
                {
                    "slug": slug,
                    "computed": computed,
                    "published": published,
                    "deviation": abs(computed - published) / published,
                    "status": "excepted",
                }
            )
        else:
            deviation = abs(computed - published) / published
            records.append(
                {
                    "slug": slug,
                    "computed": computed,
                    "published": published,
                    "deviation": deviation,
                    "status": "ok" if deviation <= tolerance else "failed",
                }
            )
    passed = all(r["status"] != "failed" for r in records)
    return records, passed


def merge_snapshots(
    existing: dict[str, dict], parsed: dict[str, dict]
) -> tuple[dict[str, dict], dict[str, int], list[str]]:
    """Extend-not-replace merge: (merged, counts, warnings).

    New slugs are added, changed records updated, slugs missing from the
    parsed page kept with "dropped": True. Nothing is ever deleted.
    """
    merged = {slug: dict(rec) for slug, rec in existing.items()}
    counts = {"added": 0, "updated": 0, "dropped": 0}
    warnings = []
    for slug, rec in parsed.items():
        if slug not in merged:
            merged[slug] = rec
            counts["added"] += 1
            continue
        old = {k: v for k, v in merged[slug].items() if k != "dropped"}
        if old != rec:
            merged[slug] = rec
            counts["updated"] += 1
        elif merged[slug].get("dropped"):
            del merged[slug]["dropped"]
    for slug, rec in merged.items():
        if slug in parsed:
            continue
        if not rec.get("dropped"):
            rec["dropped"] = True
            counts["dropped"] += 1
        warnings.append(f"{slug}: absent from page, kept with dropped: true")
    return merged, counts, warnings
