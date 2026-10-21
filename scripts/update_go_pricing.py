#!/usr/bin/env python3
"""Generate data/plans/go.json from the OpenCode Go docs page.

The page (https://opencode.ai/v2/docs/console/go) holds, per plan tab
(Go $10 / Go Plus $40): a pricing table (token prices $/1M plus a monthly
dollar limit per model), a published request-estimate table, token-assumption
bullets ("N input, N cached, N output tokens per request"), and an Endpoints
table mapping display names to model IDs (used for slug mapping; tiered
rows like "Qwen3.7 Plus (<= 256K tokens)" collapse to one slug and keep each
tier's prices under "tiers" - tiered rows share one monthly limit).

The generated file is a snapshot that merges extend-not-replace into any
existing copy: new slugs are added, changed records updated, slugs missing
from the page are kept with "dropped": true - nothing is ever deleted.

Before writing, a validation gate reproduces the page's own monthly request
estimates from limits + prices + token assumptions
(cpr = (t_in*p_in + t_cache*p_cached + t_out*p_out) / 1e6, req = limit/cpr,
windows 20%/50%/100% - docs/model-go-plan.md). Deviations above 10% fail
shut with a nonzero exit and no write. Documented page-internal exceptions:
kimi-k2.7-code deviates 26% (bounded at 30% here), and
gemini-4-argon-contributor publishes no token assumptions (reproduction
impossible, warned not failed).

Usage:
    python3 scripts/update_go_pricing.py                 # fetch and write the snapshot
    python3 scripts/update_go_pricing.py --from FILE     # parse a saved copy
    python3 scripts/update_go_pricing.py --check         # validate the snapshot offline

Snapshots: the runtime copy lives in data/plans/ (gitignored). The
frozen fallback shipped inside the wheel is refreshed deliberately with
an explicit --out, never by default:

    python3 scripts/update_go_pricing.py --out opencode_models/data/plans/go.json
"""

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

PAGE_URL = "https://opencode.ai/v2/docs/console/go"
USER_AGENT = "opencode-models/0.1.0"
DEFAULT_OUT = "data/plans/go.json"
PLANS = ("go", "go_plus")
PANEL_PLAN = {"go": "go", "go-plus": "go_plus"}
WINDOWS = {"rolling": 0.2, "weekly": 0.5, "monthly": 1.0}

PRICING_HEADER = (
    "Model",
    "Input",
    "Output",
    "Cached Read",
    "Cached Write",
    "Monthly limit",
)
REQUESTS_HEADER = (
    "Model",
    "Requests per 5 hours",
    "Requests per week",
    "Requests per month",
)
ENDPOINTS_HEADER = ("Model", "Model ID", "Endpoint", "AI SDK Package")

GATE_TOLERANCE = 0.10
# Documented deviations from the 10% gate (docs/model-go-plan.md): the
# page's own estimate is internally inconsistent, capped rather than exempt.
KNOWN_DEVIATIONS = {"kimi-k2.7-code": 0.30}
# No token assumptions published on the page; reproduction impossible.
NO_ASSUMPTIONS = {"gemini-4-argon-contributor"}

_MONEY = re.compile(r"\$([\d,]+(?:\.\d+)?)")
_NUM = re.compile(r"([\d,]+(?:\.\d+)?)")
_TIER_SUFFIX = re.compile(r"^(.*?)\s*\(([^()]+)\)$")
_ASSUMPTION = re.compile(
    r"^(.+?) — ([\d,]+) input, ([\d,]+) cached, ([\d,]+) output tokens"
    r" per request$"
)


def _clean(parts):
    return " ".join("".join(parts).split())


class _SectionParser(HTMLParser):
    """Collects tables (tagged with their plan tab) and <li> texts.

    Content inside <del> (superseded values on discounted rows) and
    <small> (annotation badges) is dropped; the live value in a cell is
    the remaining text.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []  # (plan panel, header row, body rows)
        self.items = []  # <li> texts
        self._panels = []
        self._table = None
        self._row = None
        self._cell = None
        self._item = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag == "div":
            panel = dict(attrs).get("data-plan-panel")
            inherited = self._panels[-1] if self._panels else None
            self._panels.append(panel or inherited)
        elif tag == "table":
            plan = self._panels[-1] if self._panels else None
            self._table = {"plan": plan, "rows": []}
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "br":
            if self._cell is not None:
                self._cell.append(" ")
        elif tag in ("del", "small"):
            self._skip += 1
        elif tag == "li":
            self._item = []

    def handle_endtag(self, tag):
        if tag == "div":
            if self._panels:
                self._panels.pop()
        elif tag in ("td", "th"):
            if self._cell is not None and self._row is not None:
                self._row.append(_clean(self._cell))
            self._cell = None
        elif tag == "tr":
            if self._row and self._table is not None:
                self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table":
            if self._table is not None and self._table["rows"]:
                rows = self._table["rows"]
                self.tables.append((self._table["plan"], rows[0], rows[1:]))
            self._table = None
        elif tag in ("del", "small"):
            if self._skip:
                self._skip -= 1
        elif tag == "li":
            if self._item is not None:
                text = _clean(self._item)
                if text:
                    self.items.append(text)
            self._item = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._cell is not None:
            self._cell.append(data)
        elif self._item is not None:
            self._item.append(data)


def _num(text):
    value = float(text.replace(",", ""))
    return int(value) if value.is_integer() else value


def _money(cell):
    if cell.strip() == "-":
        return None
    match = _MONEY.search(cell)
    if not match:
        raise ValueError(f"unparseable price cell: {cell!r}")
    return _num(match.group(1))


def _limit(cell):
    if cell.startswith("Unlimited"):
        return None
    match = _MONEY.search(cell)
    if not match:
        raise ValueError(f"unparseable limit cell: {cell!r}")
    return _num(match.group(1))


def _estimate(cell):
    if cell == "Unlimited":
        return None
    match = _NUM.fullmatch(cell)
    if not match:
        raise ValueError(f"unparseable estimate cell: {cell!r}")
    return _num(match.group(1))


def _base_name(cell):
    match = _TIER_SUFFIX.match(cell)
    return match.group(1) if match else cell


def _tier_label(cell):
    match = _TIER_SUFFIX.match(cell)
    return match.group(2) if match else None


def _lookup(endpoints, name, soft=False):
    slug = endpoints.get(name)
    if slug is None and not soft:
        raise ValueError(f"model {name!r} missing from Endpoints table")
    return slug


def _pricing_row(row, endpoints):
    if len(row) != len(PRICING_HEADER):
        raise ValueError(f"unexpected pricing row: {row!r}")
    base = _base_name(row[0])
    slug = _lookup(endpoints, base)
    free = row[1] == "Free" or row[2] == "Free"
    limit = _limit(row[5])
    if free:
        return slug, base, True, None, None
    tier = {
        "label": _tier_label(row[0]),
        "in": _money(row[1]),
        "out": _money(row[2]),
        "cached": _money(row[3]),
        "cached_write": _money(row[4]),
    }
    return slug, base, False, tier, limit


def _plan_rows(rows, endpoints):
    """One plan tab's pricing rows, keyed by slug, tiers in page order."""
    out = {}
    for row in rows:
        slug, base, free, tier, limit = _pricing_row(row, endpoints)
        entry = out.get(slug)
        if entry is None:
            entry = {"name": base, "free": free, "limit": limit, "tiers": []}
            out[slug] = entry
        if entry["free"] != free:
            raise ValueError(f"{slug}: free flag differs across rows")
        if free:
            continue
        if limit is None:
            raise ValueError(f"{slug}: priced row with Unlimited limit")
        if entry["limit"] != limit:
            raise ValueError(f"{slug}: tiers disagree on monthly limit")
        if any(t["label"] == tier["label"] for t in entry["tiers"]):
            raise ValueError(f"{slug}: duplicate tier label {tier['label']!r}")
        entry["tiers"].append(tier)
    return out


def _estimate_rows(rows, endpoints):
    out = {}
    for row in rows:
        if len(row) != len(REQUESTS_HEADER):
            raise ValueError(f"unexpected requests row: {row!r}")
        slug = _lookup(endpoints, _base_name(row[0]))
        out[slug] = _estimate(row[3])
    return out


def _assumption_targets(name, endpoints):
    """Resolve a bullet's model list to slugs.

    Bullets name one model ("Kimi K3") or a slash list that may abbreviate
    every entry but the first ("GLM-5.3/5.2" -> GLM-5.3, GLM-5.2); an
    abbreviation is expanded by splicing it into the first name and
    keeping the first spelling the Endpoints table recognizes.
    """
    parts = [p.strip() for p in name.split("/")]
    names = [parts[0]]
    for part in parts[1:]:
        if _lookup(endpoints, part, soft=True) is not None:
            names.append(part)
            continue
        candidates = (
            re.sub(r"\S+$", part, parts[0]),  # "Kimi K3/K2.7" -> "Kimi K2.7"
            re.sub(r"-[^-]*$", "-" + part, parts[0]),  # "GLM-5.3/5.2"
            f"{parts[0]} {part}",
        )
        for candidate in candidates:
            if (
                candidate != parts[0]
                and _lookup(endpoints, candidate, soft=True) is not None
            ):
                names.append(candidate)
                break
        else:
            raise ValueError(f"assumption bullet abbreviates unknown model {part!r}")
    return [_lookup(endpoints, n) for n in names]


def _assumptions(items, endpoints):
    found = {}
    for text in items:
        match = _ASSUMPTION.match(text)
        if not match:
            continue
        tokens = {
            "in": _num(match.group(2)),
            "cached": _num(match.group(3)),
            "out": _num(match.group(4)),
        }
        for slug in _assumption_targets(match.group(1), endpoints):
            found[slug] = tokens
    return found


def parse_page(html):
    """Parse a saved Go docs page into (models by slug, warnings)."""
    parser = _SectionParser()
    parser.feed(html)
    endpoints = {}
    pricing = {plan: None for plan in PLANS}
    estimates = {plan: None for plan in PLANS}
    for plan, header, rows in parser.tables:
        key = tuple(header)
        if key == ENDPOINTS_HEADER:
            for row in rows:
                if len(row) >= 2:
                    endpoints[row[0]] = row[1]
        elif key == PRICING_HEADER and plan in PANEL_PLAN:
            pricing[PANEL_PLAN[plan]] = rows
        elif key == REQUESTS_HEADER and plan in PANEL_PLAN:
            estimates[PANEL_PLAN[plan]] = rows
    if not endpoints:
        raise ValueError("Endpoints table not found")
    for plan in PLANS:
        if pricing[plan] is None:
            raise ValueError(f"pricing table for plan {plan!r} not found")
        if estimates[plan] is None:
            raise ValueError(f"requests table for plan {plan!r} not found")
    by_plan = {plan: _plan_rows(pricing[plan], endpoints) for plan in PLANS}
    est_by_plan = {plan: _estimate_rows(estimates[plan], endpoints) for plan in PLANS}
    first, second = PLANS
    if set(by_plan[first]) != set(by_plan[second]):
        raise ValueError("pricing tables list different models per plan")
    if set(est_by_plan[first]) != set(by_plan[first]):
        raise ValueError("pricing and requests tables list different models")
    assumptions = _assumptions(parser.items, endpoints)
    warnings = []
    models = {}
    for slug, g in by_plan[first].items():
        p = by_plan[second][slug]
        if g["free"] != p["free"]:
            raise ValueError(f"{slug}: free flag differs across plans")
        if g["free"]:
            models[slug] = {
                "name": g["name"],
                "free": True,
                "tiers": [],
                "limits": {"go": None, "go_plus": None},
                "requests_per_month": {
                    "go": est_by_plan[first][slug],
                    "go_plus": est_by_plan[second][slug],
                },
                "assumptions": None,
            }
            continue
        if [t["label"] for t in g["tiers"]] != [t["label"] for t in p["tiers"]]:
            raise ValueError(f"{slug}: tier labels differ across plans")
        if assumptions.get(slug) is None:
            warnings.append(f"{slug}: no token assumptions published")
        models[slug] = {
            "name": g["name"],
            "free": False,
            "tiers": g["tiers"],
            "limits": {"go": g["limit"], "go_plus": p["limit"]},
            "requests_per_month": {
                "go": est_by_plan[first][slug],
                "go_plus": est_by_plan[second][slug],
            },
            "assumptions": assumptions.get(slug),
        }
    for slug in endpoints.values():
        if slug not in models:
            warnings.append(f"{slug}: in Endpoints but not in pricing tables")
    return models, warnings


def merge(existing, parsed):
    """Extend-not-replace merge: (merged, counts, warnings).

    New slugs are added, changed records updated, slugs missing from the
    page are kept with "dropped": true. Nothing is ever deleted.
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


def validate(models, known_deviations=KNOWN_DEVIATIONS, no_assumptions=NO_ASSUMPTIONS):
    """Reproduce published monthly estimates from limits+prices+assumptions.

    Returns (errors, warnings, checked) where checked holds one
    (slug, plan, computed, published, deviation) tuple per priced model
    with published assumptions.
    """
    errors, warnings, checked = [], [], []
    for slug in sorted(models):
        rec = models[slug]
        if rec.get("dropped"):
            warnings.append(f"{slug}: dropped by page, not validated")
            continue
        if rec["free"]:
            continue
        for plan in PLANS:
            if rec["limits"].get(plan) is None:
                errors.append(f"{slug}: no monthly limit on plan {plan}")
            if rec["requests_per_month"].get(plan) is None:
                errors.append(f"{slug}: no published estimate on plan {plan}")
        if rec["assumptions"] is None:
            if slug in no_assumptions:
                warnings.append(
                    f"{slug}: no token assumptions, reproduction impossible"
                )
            else:
                errors.append(f"{slug}: no token assumptions")
            continue
        if not rec["tiers"]:
            errors.append(f"{slug}: priced model without tiers")
            continue
        base, tokens = rec["tiers"][0], rec["assumptions"]
        cpr = (
            tokens["in"] * base["in"]
            + tokens["cached"] * (base["cached"] or 0)
            + tokens["out"] * base["out"]
        ) / 1e6
        if cpr <= 0:
            errors.append(f"{slug}: non-positive cost per request")
            continue
        cap = known_deviations.get(slug, GATE_TOLERANCE)
        for plan in PLANS:
            published = rec["requests_per_month"][plan]
            if published is None:
                continue
            expected = rec["limits"][plan] / cpr
            deviation = abs(expected - published) / published
            checked.append((slug, plan, expected, published, deviation))
            if deviation > cap:
                errors.append(
                    f"{slug} [{plan}]: {expected:,.0f} computed vs"
                    f" {published:,} published ({deviation:.0%} > {cap:.0%})"
                )
    return errors, warnings, checked


def _report(errors, warnings, checked, merged_counts=None, total=None):
    for warning in warnings:
        print(f"warning: {warning}")
    if merged_counts is not None:
        print(
            f"models: added={merged_counts['added']}"
            f" updated={merged_counts['updated']}"
            f" dropped={merged_counts['dropped']}"
            f" total={total}"
        )
    if checked:
        slug, plan, expected, published, deviation = max(checked, key=lambda c: c[4])
        print(
            f"gate: {len(checked)} plan-estimates checked, worst deviation"
            f" {deviation:.1%} ({slug} [{plan}], {expected:,.0f} vs"
            f" {published:,})"
        )
    for error in errors:
        print(f"gate error: {error}")
    return 1 if errors else 0


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def _read_models(path):
    try:
        return json.loads(path.read_text())["models"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise ValueError(f"cannot read models from {path}: {exc}") from exc


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
            html = Path(args.source).read_text()
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        source = PAGE_URL
        try:
            html = _fetch(PAGE_URL)
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
    doc = {
        "meta": {
            "source": source,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "plans": list(PLANS),
        },
        "windows": dict(WINDOWS),
        "models": {slug: merged[slug] for slug in sorted(merged)},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
