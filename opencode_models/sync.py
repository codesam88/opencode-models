"""Lazy, on-demand refresh of stale T2 sources.

``sync`` is the user-invoked (and, later, lazily-triggered) counterpart to
``fetch``: it touches only the T2 layers (availability + plan pricing) and
only when they are older than their thresholds. T1 layers (catalog, boards,
arena) are deliberately left alone here — they are explicit ``fetch`` only,
with their own cadence warnings. Measured/export-derived data is out of
scope for this step and has no writer yet.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from . import availability, go_pricing, zen_pricing
from .freshness import AVAILABILITY_MAX_AGE, PLANS_MAX_AGE, snapshot_age

_AVAILABILITY_TIERS = ("go", "zen")


def _age_str(age: timedelta) -> str:
    total = int(age.total_seconds())
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h"
    return f"{total // 86400}d"


def _count_models(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    models = data.get("models")
    if not isinstance(models, dict):
        return None
    return len(models)


def refresh_plan(data_dir, tier, get=None):
    """Ensure one plan tier's snapshot is fresh; refresh if stale.

    Returns ``(status, messages, age)`` where *status* is ``"fresh"``,
    ``"refreshed"`` or ``"kept"`` and *messages* is a tuple of report lines
    (up-to-date / refreshed / warning + kept) ready to print or collect.
    Shared by ``sync`` and the read paths so refresh reporting stays one
    implementation. *get* is an optional transport override (tests); defaults
    to the module's own fetcher.
    """
    from pathlib import Path

    if tier not in ("go", "zen"):
        raise KeyError(tier)
    module = go_pricing if tier == "go" else zen_pricing
    data_dir = Path(data_dir)
    source = f"plans/{tier}"
    age = snapshot_age(data_dir, source)
    if age is not None and age <= PLANS_MAX_AGE:
        return "fresh", (f"{source}: up-to-date ({_age_str(age)})",), age
    try:
        if get is None:
            module.refresh(data_dir)
        else:
            module.refresh(data_dir, get=get)
    except (OSError, ValueError) as exc:  # docs down must not fail the run
        return (
            "kept",
            (
                f"warning: {tier} pricing docs not refreshed ({exc})",
                f"{source}: kept existing snapshot",
            ),
            age,
        )
    count = _count_models(data_dir / "plans" / f"{tier}.json")
    if count is None:
        msg = f"{source}: refreshed"
    else:
        msg = f"{source}: refreshed ({count} models)"
    return "refreshed", (msg,), age


def sync(data_dir: Path, get=None) -> int:
    """Refresh stale T2 sources under *data_dir*, printing a per-source report.

    Returns 0 on success; unreachable or keyless sources warn and keep their
    last snapshots rather than raising.
    """
    data_dir = Path(data_dir)
    refreshed = 0
    up_to_date = 0
    warned = 0

    # Availability (key-gated). current_offered pulls + saves when a key is
    # present and treats transport failures as warnings, so its warnings are
    # the availability step's report.
    stale_avail = []
    for tier in _AVAILABILITY_TIERS:
        source = f"available/{tier}"
        age = snapshot_age(data_dir, source)
        if age is None or age > AVAILABILITY_MAX_AGE:
            stale_avail.append(tier)
        else:
            print(f"{source}: up-to-date ({_age_str(age)})")
            up_to_date += 1

    if stale_avail:
        result = availability.current_offered(data_dir, bypass=False)
        for warning in result.warnings:
            print(warning)
        if result.live:
            for tier in stale_avail:
                count = None
                if isinstance(result.tiers, dict) and tier in result.tiers:
                    try:
                        count = len(result.tiers[tier])
                    except TypeError:
                        count = None
                if count is None:
                    print(f"available/{tier}: refreshed")
                else:
                    print(f"available/{tier}: refreshed ({count} models)")
                refreshed += 1
        else:
            for tier in stale_avail:
                print(f"available/{tier}: kept existing snapshot")
                warned += 1

    # Plan pricing (public pages).
    for tier in ("go", "zen"):
        status, messages, _age = refresh_plan(data_dir, tier, get=get)
        for message in messages:
            print(message)
        if status == "fresh":
            up_to_date += 1
        elif status == "refreshed":
            refreshed += 1
        else:
            warned += 1

    print(f"summary: {refreshed} refreshed, {up_to_date} up-to-date, {warned} warned")
    return 0


__all__ = [
    "AVAILABILITY_MAX_AGE",
    "PLANS_MAX_AGE",
    "refresh_plan",
    "sync",
]
