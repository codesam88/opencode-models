"""Pure functional core for the Go plan allowance model.

Equations and window fractions come from ``docs/model-go-plan.md``:
``cpr = (t_in*p_in + t_cache*p_cached + t_out*p_out) / 1e6`` ($/req),
``R_month = limit / cpr``, windows as fractions of the monthly limit.

The gate/merge logic here intentionally duplicates the validation in
``go_pricing`` (which works on a live page snapshot while this module
works on parsed dicts handed in by callers). This module never reads
files or the network.

This core's assumption-based ``cost_per_request`` is a different thing
from ``pricing.weighted_cost`` (the fixed-mix representative cost used
for ranking); do not conflate them.
"""

from __future__ import annotations

WINDOW_FRACTIONS = {"rolling": 0.2, "weekly": 0.5, "monthly": 1.0}

#: Snapshot plan keys a stored preference may name.
PLAN_KEYS = ("go", "go_plus")


def resolve_plan(flag: str | None, stored: str | None) -> str:
    """Which snapshot plan key to use: explicit flag, stored preference, Go.

    The flag takes CLI spellings (``go-plus`` with a dash); the stored
    value is already a snapshot key. Anything unrecognized falls back to
    the Go default — the workspace plan is never guessed, only defaulted.
    """
    if flag == "go-plus" or flag == "go_plus":
        return "go_plus"
    if flag == "go":
        return "go"
    if stored in PLAN_KEYS:
        return stored
    return "go"


def suggest_plan(
    spend: dict[str, float], limits: dict[str, dict]
) -> tuple[str, str] | None:
    """A Plus hint from measured spend, or None when there is none to give.

    Returns ``("go_plus", evidence)`` when some known model's monthly
    spend exceeds its Go cap — the one case where the numbers point at
    Plus. Light usage fits both plans, unknown models are skipped rather
    than assumed, and uncapped (null-limit) models cannot exceed. Silence
    is a valid answer: absence of evidence says nothing about the plan.
    """
    worst: tuple[str, float, float] | None = None
    for slug, dollars in spend.items():
        caps = limits.get(slug) or {}
        cap = caps.get("go")
        if cap is None or dollars <= cap:
            continue
        if worst is None or dollars - cap > worst[1] - worst[2]:
            worst = (slug, dollars, cap)
    if worst is None:
        return None
    slug, dollars, cap = worst
    return (
        "go_plus",
        f"{slug} spent ${dollars:,.2f} vs Go cap ${cap:,.2f}",
    )


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
        limit = (rec.get("limits") or {}).get("go")
        published = (rec.get("requests_per_month") or {}).get("go")
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


def check_export_prices(
    rows: list[dict], records: dict[str, dict], tolerance: float = 0.10
) -> list[str]:
    """Flag export rows whose reported cost diverges from page-math reproduction.

    Each row's cost is recomputed from its own tokens against the model's
    base tier (null cached reads bill at the input rate, as in
    :func:`cost_per_request`); a row diverging past *tolerance* — the
    publish gate's 10% — is returned as a human line naming the model
    and both numbers. Rows for unknown models, models without tiers or
    assumptions, and free models carry nothing to check against and are
    skipped silently: unverifiable is not a violation.
    """
    findings = []
    for row in rows:
        rec = records.get(row.get("model") or "")
        if not isinstance(rec, dict) or rec.get("free"):
            continue
        tiers = rec.get("tiers") or []
        assumptions = rec.get("assumptions")
        if not tiers or not assumptions:
            continue
        base = tiers[0]
        cached = base.get("cached")
        if cached is None:
            cached = base.get("in")
        try:
            computed = (
                row.get("input_tokens", 0) * base["in"]
                + row.get("cache_read_tokens", 0) * cached
                + row.get("output_tokens", 0) * base["out"]
            ) / 1e6
            reported = row["cost"]
        except (KeyError, TypeError):
            continue
        if reported == 0:
            if computed != 0:
                findings.append(
                    f"{row['model']}: reported $0 vs page math ${computed:.6g}"
                )
            continue
        if abs(reported - computed) / reported > tolerance:
            findings.append(
                f"{row['model']}: reported ${reported:.6g} vs page math ${computed:.6g}"
            )
    return findings


def check_caps_resolve(
    slugs: list[str], records: dict[str, dict], plan: str
) -> list[str]:
    """Flag served models whose headroom cannot be computed for *plan*.

    A priced model with no plan record, or with no limit for the plan,
    would render as a silent dash; the finding names it instead. Free
    models need no caps and are skipped.
    """
    findings = []
    for slug in sorted(slugs):
        rec = records.get(slug)
        if not isinstance(rec, dict):
            findings.append(f"{slug}: no plan record")
        elif not rec.get("free") and (rec.get("limits") or {}).get(plan) is None:
            findings.append(f"{slug}: no {plan} monthly limit")
    return findings


def compare_window_percent(
    api_percent: float | None,
    spend: dict[str, float],
    caps: dict[str, float | None],
) -> dict:
    """API window percent beside spend-implied percents; never a verdict.

    The mapping from the API's percent to per-model spend is unknown
    (it matches neither the strongest model nor spend-vs-sum-of-limits),
    so this reports all three numbers and labels the unknowns instead
    of fudging a pass/fail. ``max_model`` is the strongest model's share
    of its cap; ``spend_share`` is total spend over total caps, ignoring
    uncapped models. ``note`` is set whenever ``api`` is missing.
    """
    shares = [spend[slug] / cap for slug, cap in caps.items() if slug in spend and cap]
    total_spend = sum(spend.get(slug, 0.0) for slug in caps)
    total_caps = sum(cap for cap in caps.values() if cap)
    return {
        "api": api_percent,
        "max_model": max(shares) * 100 if shares else None,
        "spend_share": total_spend / total_caps * 100 if total_caps else None,
        "note": (
            None
            if api_percent is not None
            else "api percent unknown — mapping to spend is unstated"
        ),
    }


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
