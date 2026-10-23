#!/usr/bin/env python3
"""Generate data/plans/zen.json from the OpenCode Zen docs page.

The page (https://opencode.ai/docs/zen/) holds three tables: Endpoints
(Model | Model ID | Endpoint | AI SDK Package), Pricing (Model | Input |
Output | Cached Read | Cached Write), and Deprecated (Model | Deprecation
date). There are no plan tabs and no monthly caps (credit-based), so the
snapshot carries no windows, limits, request estimates, or token
assumptions: each model record has limits/requests_per_month/assumptions
set to None. Tiered rows like "Claude Sonnet 4.5 (<= 200K tokens)"
collapse to one slug and keep each tier's prices under "tiers".

The generated file is a snapshot that merges extend-not-replace into any
existing copy: new slugs are added, changed records updated, slugs missing
from the page are kept with "dropped": true - nothing is ever deleted.

Usage:
    python3 scripts/update_zen_pricing.py                 # fetch and write the snapshot
    python3 scripts/update_zen_pricing.py --from FILE     # parse a saved copy
    python3 scripts/update_zen_pricing.py --check         # validate the snapshot offline

Snapshots: the runtime copy lives in data/plans/ (gitignored). The
frozen fallback shipped inside the wheel is refreshed deliberately with
an explicit --out, never by default:

    python3 scripts/update_zen_pricing.py --out opencode_models/data/plans/zen.json
"""

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# Reuse the Go script's table parser and helpers rather than duplicating.
from . import go_pricing

PAGE_URL = "https://opencode.ai/docs/zen/"


def _user_agent():
    """This tool's User-Agent, versioned from the installed package."""
    try:
        return f"opencode-models/{version('opencode-models')}"
    except PackageNotFoundError:  # bare checkout without an install
        return "opencode-models/0.0.0+unknown"


USER_AGENT = _user_agent()
DEFAULT_OUT = "data/plans/zen.json"
PLANS = ("zen",)

PRICING_HEADER = (
    "Model",
    "Input",
    "Output",
    "Cached Read",
    "Cached Write",
)
ENDPOINTS_HEADER = ("Model", "Model ID", "Endpoint", "AI SDK Package")
DEPRECATED_HEADER = ("Model", "Deprecation date")


def _pricing_row(row, endpoints):
    if len(row) != len(PRICING_HEADER):
        raise ValueError(f"unexpected pricing row: {row!r}")
    base = go_pricing._base_name(row[0])
    slug = go_pricing._lookup(endpoints, base)
    free = row[1] == "Free" or row[2] == "Free"
    if free:
        return slug, base, True, None
    tier = {
        "label": go_pricing._tier_label(row[0]),
        "in": go_pricing._money(row[1]),
        "out": go_pricing._money(row[2]),
        "cached": go_pricing._money(row[3]),
        "cached_write": go_pricing._money(row[4]),
    }
    return slug, base, False, tier


def _plan_rows(rows, endpoints):
    out = {}
    for row in rows:
        slug, base, free, tier = _pricing_row(row, endpoints)
        entry = out.get(slug)
        if entry is None:
            entry = {"name": base, "free": free, "tiers": []}
            out[slug] = entry
        if entry["free"] != free:
            raise ValueError(f"{slug}: free flag differs across rows")
        if free:
            continue
        if any(t["label"] == tier["label"] for t in entry["tiers"]):
            raise ValueError(f"{slug}: duplicate tier label {tier['label']!r}")
        entry["tiers"].append(tier)
    return out


def parse_page(html):
    """Parse a saved Zen docs page into (models by slug, warnings)."""
    parser = go_pricing._SectionParser()
    parser.feed(html)
    endpoints = {}
    endpoint_urls = {}
    sdk_packages = {}
    pricing_rows = None
    deprecated_rows = None
    for _plan, header, rows in parser.tables:
        key = tuple(header)
        if key == ENDPOINTS_HEADER:
            for row in rows:
                if len(row) >= 2:
                    endpoints[row[0]] = row[1]
                    if len(row) >= 3:
                        endpoint_urls[row[1]] = row[2]
                    if len(row) >= 4:
                        sdk_packages[row[1]] = row[3]
        elif key == PRICING_HEADER:
            pricing_rows = rows
        elif key == DEPRECATED_HEADER:
            deprecated_rows = rows
    if not endpoints:
        raise ValueError("Endpoints table not found")
    if pricing_rows is None:
        raise ValueError("pricing table not found")
    plan_rows = _plan_rows(pricing_rows, endpoints)
    deprecated_map = {}
    for row in deprecated_rows or []:
        if not row:
            continue
        slug = go_pricing._lookup(endpoints, row[0], soft=True)
        if slug is not None:
            deprecated_map[slug] = row[1] if len(row) > 1 else None
    warnings = []
    models = {}
    for slug, g in plan_rows.items():
        dep_date = deprecated_map.get(slug)
        models[slug] = {
            "name": g["name"],
            "free": g["free"],
            "tiers": g["tiers"],
            "limits": None,
            "requests_per_month": None,
            "assumptions": None,
            "dropped": False,
            "first_seen": None,
            "deprecated": slug in deprecated_map,
            "deprecation_date": dep_date,
            "endpoint": endpoint_urls.get(slug),
            "sdk_package": sdk_packages.get(slug),
            "notes": None,
        }
    # deprecated slugs that still have a pricing row -> warn
    for row in deprecated_rows or []:
        if not row:
            continue
        slug = go_pricing._lookup(endpoints, row[0], soft=True)
        if slug is not None and slug in models:
            warnings.append(f"{slug}: deprecated but still has a pricing row")
    for slug in endpoints.values():
        if slug not in models:
            warnings.append(f"{slug}: in Endpoints but not in pricing tables")
    return models, warnings


def merge(existing, parsed):
    return go_pricing.merge(existing, parsed)


def validate(models, *args, **kwargs):
    """Structural validation of a zen snapshot. Returns (errors, warnings, checked)."""
    errors, warnings, checked = [], [], []
    for slug in sorted(models):
        rec = models[slug]
        if rec.get("dropped"):
            warnings.append(f"{slug}: dropped by page, not validated")
            continue
        for key in (
            "name",
            "free",
            "tiers",
            "limits",
            "requests_per_month",
            "assumptions",
        ):
            if key not in rec:
                errors.append(f"{slug}: missing field {key}")
                continue
        if rec.get("limits") is not None:
            errors.append(f"{slug}: zen limits must be None")
        if rec.get("requests_per_month") is not None:
            errors.append(f"{slug}: zen requests_per_month must be None")
        if rec.get("assumptions") is not None:
            errors.append(f"{slug}: zen assumptions must be None")
        free = rec.get("free")
        if free not in (True, False):
            errors.append(f"{slug}: free must be bool")
            continue
        tiers = rec.get("tiers")
        if not isinstance(tiers, list):
            errors.append(f"{slug}: tiers must be a list")
            continue
        if free and tiers:
            errors.append(f"{slug}: free model with tiers")
        if (not free) and not tiers:
            errors.append(f"{slug}: priced model without tiers")
        for t in tiers:
            for k in ("label", "in", "out", "cached", "cached_write"):
                if k not in t:
                    errors.append(f"{slug}: tier missing {k}")
    return errors, warnings, checked


def _report(errors, warnings, checked, merged_counts=None, total=None):
    return go_pricing._report(
        errors, warnings, checked, merged_counts=merged_counts, total=total
    )


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def _read_models(path):
    return go_pricing._read_models(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--from",
        dest="source",
        metavar="FILE",
        help="parse a saved copy of the page instead of fetching",
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        metavar="PATH",
        help=f"snapshot path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the existing snapshot; no network, no write",
    )
    args = parser.parse_args(argv)
    out = Path(args.out)
    if args.check:
        try:
            models = _read_models(out)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        errors, warnings, checked = validate(models)
        return _report(errors, warnings, checked)
    if args.source:
        source = args.source
        try:
            raw = Path(args.source).read_bytes()
            html = raw.decode("utf-8")
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        source = PAGE_URL
        try:
            html = _fetch(PAGE_URL)
            raw = html.encode("utf-8")
        except OSError as exc:
            print(f"error: fetch failed: {exc}", file=sys.stderr)
            return 2
    try:
        parsed, parse_warnings = parse_page(html)
    except ValueError as exc:
        print(f"error: parse failed: {exc}", file=sys.stderr)
        return 2
    existing = {}
    if out.exists():
        try:
            existing = _read_models(out)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    merged, counts, merge_warnings = merge(existing, parsed)
    errors, gate_warnings, checked = validate(merged)
    warnings = parse_warnings + merge_warnings + gate_warnings
    code = _report(errors, warnings, checked, merged_counts=counts, total=len(merged))
    if code:
        print("gate failed; not writing")
        return code
    import hashlib

    doc = {
        "meta": {
            "source": source,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "schema_rev": 1,
            "page_sha256": hashlib.sha256(raw).hexdigest(),
            "plans": list(PLANS),
        },
        "models": {slug: merged[slug] for slug in sorted(merged)},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


def refresh(directory, get=None):
    """Fetch/parse/merge/write the Zen plan snapshot; return files written."""
    from pathlib import Path

    directory = Path(directory)
    out = directory / "plans" / "zen.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    get = get or _fetch
    try:
        html = get(PAGE_URL)
    except Exception as exc:  # noqa: BLE001
        raise OSError(exc) from exc
    parsed, parse_warnings = parse_page(html)
    raw = html.encode("utf-8")
    existing = {}
    if out.exists():
        try:
            existing = json.loads(out.read_text()).get("models", {})
        except (json.JSONDecodeError, OSError):
            existing = {}
    merged, counts, merge_warnings = merge(existing, parsed)
    errors, gate_warnings, checked = validate(merged)
    if errors:
        raise ValueError("; ".join(errors))
    import hashlib
    from datetime import datetime, timezone

    doc = {
        "meta": {
            "source": PAGE_URL,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "schema_rev": 1,
            "page_sha256": hashlib.sha256(raw).hexdigest(),
            "plans": list(PLANS),
        },
        "models": {slug: merged[slug] for slug in sorted(merged)},
    }
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return [out.name]
