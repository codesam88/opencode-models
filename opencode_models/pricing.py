"""Tier price snapshots scraped from the OpenCode docs (Zen and Go).

The catalog carries one provider list price with no notion of Console tier,
so it cannot say what a model costs *here* — DeepSeek V4 Pro is $1.74 on Zen
but $0.66 off-peak on Go, and Gemini 3.6/3.7/3.8 Flash list at half the Zen
rate in the catalog. The docs' own pricing tables are the authority, so
``refresh_prices`` snapshots them to ``<tier>_prices.json`` next to the tier
slug files and ``load_prices`` reads them back.

Two rules the docs force on us:

* Every priced **variant** (``DeepSeek V4 Pro (Off-Peak)`` / ``(Peak)``,
  ``(<= 200K tokens)`` / ``(> 200K tokens)``) is its own line entry — one
  model can legitimately appear several times at different rates.
* Rows reading ``Free`` are free (``free=True``, price 0), and Go rows carry
  the model's included monthly usage limit in dollars.

The representative cost is a weighted mix of the three token classes
(20% input, 60% cached read, 20% output) rather than the bare input rate: an
agent re-sends the same context every turn, so cached reads dominate the
bill. Missing cached pricing means the provider offers no cache discount, so
those reads are billed at the input rate.
"""

from __future__ import annotations

import html as _html
import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

PRICING_URLS = {
    "zen": "https://opencode.ai/docs/zen/",
    "go": "https://opencode.ai/docs/go/",
}

#: Weight of each token class in the representative cost.
COST_WEIGHTS = {"input": 0.2, "cached": 0.6, "output": 0.2}


@dataclass(frozen=True)
class PriceEntry:
    """One priced line from a tier's documentation table."""

    tier: str
    model_id: str | None
    name: str
    input: float
    output: float
    cached_read: float | None = None
    limit: float | None = None
    free: bool = False


def weighted_cost(
    input_: float | None, output: float | None, cached_read: float | None
) -> float | None:
    """Representative $/1M across token classes (20/60/20).

    ``None`` input means we know nothing about this model. An absent cached
    rate means there is no cache discount (billed at the input rate); an
    absent output rate drops out and the remaining weights renormalise.
    """
    if input_ is None:
        return None
    cached = input_ if cached_read is None else cached_read
    parts = [(COST_WEIGHTS["input"], input_), (COST_WEIGHTS["cached"], cached)]
    if output is not None:
        parts.append((COST_WEIGHTS["output"], output))
    total_weight = sum(w for w, _ in parts)
    return sum(w * price for w, price in parts) / total_weight


_VARIANT_SUFFIX = re.compile(r"\s*(\((?:≤|>|Off-Peak|Peak|50% off)[^)]*\))\s*$")


#: Nominal $/1M rates for the free tiers, so the list view can divide by
#: something instead of pricing every free model alike.
#:
#: Order-of-magnitude, not metered: the free-tier cap is unpublished, so the
#: daily budget X is anchored on the observed Space-Bunny limit (662 requests
#: in a day, 2026-10-12) and normalised to X = $0.10/day. Each model's rate
#: is its share of that budget — its measured $/request over Space-Bunny's,
#: converted back to $/1M at its own traffic profile. Full derivation, the
#: Oct-9 contradiction, and every assumption in LIMITS.md ("Estimating the
#: free tier's size"). All of them sit below the cheapest paid rate
#: ($0.034/1M), so free still ranks first on value — just not all equal.
FREE_TIER_NOMINAL_RATES: dict[str, float] = {
    "step-5-preview-free": 0.019,
    "mimo-v2.5-free": 0.0056,
    "longcat-2.5-preview-free": 0.0033,
    "big-pickle": 0.0030,
    "mimo-v2.6-flash-free": 0.0028,
    "space-bunny-free": 0.0018,
    "muse-spark-1.3-contributor-free": 0.0013,
}

#: The anchor measurement itself, for a free model nobody has mapped yet.
FREE_TIER_DEFAULT_RATE = 0.0018


def nominal_free_rate(model_id: str, rates=None) -> float:
    """The nominal $/1M of a free model id, whatever names it.

    Matches the slug with or without its ``-free`` suffix, since the catalog
    record (``stepfun/step-5-preview``) and the tier line
    (``step-5-preview-free``) spell it both ways for one model. *rates* is
    the fetched snapshot when there is one; otherwise the committed table
    below, which is only ever the offline fallback.
    """
    table = rates if rates is not None else FREE_TIER_NOMINAL_RATES
    slug = model_id.split("/")[-1]
    for key in (
        slug,
        f"{slug}-free",
        slug[: -len("-free")] if slug.endswith("-free") else slug,
    ):
        if key in table:
            return table[key]
    return table.get("__default__", FREE_TIER_DEFAULT_RATE)


#: Where the usage export lives: per-day, per-model traffic for the whole
#: workspace, both products. Person data never leaves the response — the
#: refresh below aggregates the numeric columns and drops the rest.
USAGE_EXPORT_URL = "https://opencode.ai/console/api/v2/usage/export?range=30d"

#: The free-tier budget is anchored on an observed limit event, not chosen:
#: this workspace's Space-Bunny requests started failing after 662 in a day
#: (2026-10-12). Normalised to a nominal X per day so the number stays a
#: rationing estimate rather than a bill anyone could mistake for metered.
FREE_TIER_ANCHOR_MODEL = "space-bunny-free"
FREE_TIER_ANCHOR_REQUESTS_PER_DAY = 662
FREE_TIER_BUDGET_USD_PER_DAY = 0.10

#: A profile drawn from fewer requests is noise, not a measurement: it is
#: left out of the snapshot and falls back to the anchor rate at load.
MIN_PROFILE_REQUESTS = 20

#: Serving rates for a free model with no catalog price (the stealth ones).
#: Small-model rates, stated plainly in LIMITS.md rather than hidden.
PROXY_RATES = (0.16, 0.32, 0.004)

FREE_TIER_SNAPSHOT_NAME = "free_tier_nominals.json"


def _catalog_rates(catalog_dir: Path) -> dict[str, tuple[float, float, float]]:
    """``{slug or id: (input, output, cached) $/1M}`` from catalog records."""
    rates: dict[str, tuple[float, float, float]] = {}
    for path in sorted(Path(catalog_dir).glob("*.json")):
        try:
            model = json.loads(path.read_text())["model"]
            price = model.get("priceUsdPer1M") or {}
            rates[model["id"]] = (
                price.get("input"),
                price.get("output"),
                price.get("cachedInput"),
            )
            rates[model["id"].split("/")[-1]] = rates[model["id"]]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return rates


def refresh_free_nominals(
    usage_dir: Path, catalog_dir: Path, get, free_slugs: set[str]
) -> dict[str, float] | None:
    """Derive free-tier nominal rates from the fetched usage export.

    Aggregates trailing-30d requests and token volumes per free model, prices
    each request at catalog rates (proxy where the catalog has none), and
    normalises against the anchor: the budget over the anchor's observed
    daily requests, converted back to $/1M at each model's own traffic
    profile. Writes ``usage/free_tier_nominals.json`` and returns the rates.

    User columns are never stored — only per-model numeric totals reach the
    file. Returns None (writing nothing) when the anchor itself is absent,
    since there is then nothing to normalise against.
    """
    import csv
    import io as _io
    from datetime import datetime, timezone

    traffic: dict[str, list[float]] = {}
    for row in csv.DictReader(_io.StringIO(get(USAGE_EXPORT_URL))):
        slug = (row.get("model") or "").strip()
        if slug not in free_slugs:
            continue
        try:
            values = [
                float(row.get(k) or 0)
                for k in (
                    "requests",
                    "input_tokens",
                    "output_tokens",
                    "cache_read_tokens",
                )
            ]
        except ValueError:
            continue
        acc = traffic.setdefault(slug, [0.0, 0.0, 0.0, 0.0])
        for i, value in enumerate(values):
            acc[i] += value
    catalog = _catalog_rates(catalog_dir)

    def cost_per_request(slug: str, totals: list[float]) -> float | None:
        n, inp, out, cached = totals
        if n < MIN_PROFILE_REQUESTS:
            return None
        base = slug[: -len("-free")] if slug.endswith("-free") else slug
        rates = catalog.get(base) or catalog.get(slug) or PROXY_RATES
        rin, rout, rcache = (r if r is not None else 0.0 for r in rates)
        if rcache == 0.0 and rates[2] is None:
            rcache = rin  # no cache discount: reads bill at the input rate
        return ((inp / n) * rin + (out / n) * rout + (cached / n) * rcache) / 1e6

    if FREE_TIER_ANCHOR_MODEL not in traffic:
        return None
    anchor = cost_per_request(FREE_TIER_ANCHOR_MODEL, traffic[FREE_TIER_ANCHOR_MODEL])
    if not anchor:
        return None
    unit = FREE_TIER_BUDGET_USD_PER_DAY / FREE_TIER_ANCHOR_REQUESTS_PER_DAY
    rates: dict[str, float] = {}
    for slug, totals in sorted(traffic.items()):
        each = cost_per_request(slug, totals)
        if not each:
            continue
        n, inp, out, cached = totals
        rep_tokens = 0.2 * inp / n + 0.6 * cached / n + 0.2 * out / n
        rates[slug] = unit * (each / anchor) / (rep_tokens / 1e6)
    usage_dir = Path(usage_dir)
    usage_dir.mkdir(parents=True, exist_ok=True)
    (usage_dir / FREE_TIER_SNAPSHOT_NAME).write_text(
        json.dumps(
            {
                "meta": {
                    "window": "trailing 30d usage export",
                    "fetched_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    "anchor_model": FREE_TIER_ANCHOR_MODEL,
                    "anchor_requests_per_day": FREE_TIER_ANCHOR_REQUESTS_PER_DAY,
                    "budget_usd_per_day": FREE_TIER_BUDGET_USD_PER_DAY,
                    "min_profile_requests": MIN_PROFILE_REQUESTS,
                },
                "rates": rates,
            },
            indent=2,
        )
    )
    return rates


def load_free_nominals(data_dir: Path) -> dict[str, float] | None:
    """Nominal rates from the fetch snapshot, or None when there is none.

    None means "never fetched", which the callers treat as "use the
    committed fallback", not as "free costs nothing".
    """
    path = Path(data_dir) / "usage" / FREE_TIER_SNAPSHOT_NAME
    if not path.exists():
        return None
    try:
        rates = json.loads(path.read_text()).get("rates")
    except (json.JSONDecodeError, AttributeError):
        return None
    return rates if isinstance(rates, dict) and rates else None


def variant_of(name: str) -> str:
    """The ``(Off-Peak)`` / ``(> 200K tokens)`` suffix of a documented line."""
    match = _VARIANT_SUFFIX.search(name)
    return match.group(1) if match else ""


def _cell_text(cell: str) -> str:
    """Tags stripped, entities decoded, ``<del>`` original prices dropped."""
    cell = re.sub(r"<del[^>]*>.*?</del>", " ", cell, flags=re.S)
    cell = re.sub(r"<[^>]+>", " ", cell)
    return re.sub(r"\s+", " ", _html.unescape(cell)).strip()


def _tables(doc: str):
    for table in re.findall(r"<table[\s\S]*?</table>", doc):
        rows = []
        for tr in re.findall(r"<tr[\s\S]*?</tr>", table):
            cells = [
                _cell_text(c) for c in re.findall(r"<t[hd][^>]*>([\s\S]*?)</t[hd]>", tr)
            ]
            if cells:
                rows.append(cells)
        if rows:
            yield rows


def _money(cell: str) -> float | None:
    if not cell:
        return None
    if re.search(r"free", cell, re.I) and "$" not in cell:
        return 0.0
    amounts = re.findall(r"\$\s*([\d.]+)", cell)
    return float(amounts[-1]) if amounts else None  # last = discounted price


def _limit(cell: str) -> float | None:
    if not cell or re.search(r"unlimited", cell, re.I):
        return None
    return _money(cell)


def parse_pricing(doc: str, tier: str) -> list[PriceEntry]:
    """Every priced row of a tier doc page, variants kept as separate entries.

    Model ids come from the page's endpoint table, matched on the display
    name and, for variants, on the name with its ``(…)`` suffix stripped.
    Rows that resolve to no id are kept as ``model_id=None`` rather than
    dropped: an unrecognised model is a data problem, not a parse failure.
    """
    ids: dict[str, str] = {}
    price_tables: list[tuple[list[str], list[list[str]]]] = []
    for rows in _tables(doc):
        header = [h.lower() for h in rows[0]]
        if "model id" in header and "model" in header:
            i_name, i_id = header.index("model"), header.index("model id")
            for row in rows[1:]:
                if len(row) > max(i_name, i_id):
                    ids[row[i_name]] = row[i_id]
        elif "input" in header and "output" in header:
            price_tables.append((header, rows[1:]))

    entries: list[PriceEntry] = []
    by_fingerprint: dict[tuple, int] = {}
    for header, rows in price_tables:
        i_in, i_out = header.index("input"), header.index("output")
        i_cached = header.index("cached read") if "cached read" in header else None
        i_limit = header.index("monthly limit") if "monthly limit" in header else None
        for row in rows:
            if len(row) <= max(i_in, i_out):
                continue
            name = row[0]
            inp, out = _money(row[i_in]), _money(row[i_out])
            if inp is None or out is None:
                continue
            cached = (
                _money(row[i_cached])
                if i_cached is not None and len(row) > i_cached
                else None
            )
            limit = (
                _limit(row[i_limit])
                if i_limit is not None and len(row) > i_limit
                else None
            )
            model_id = ids.get(name) or ids.get(_VARIANT_SUFFIX.sub("", name))
            fingerprint = (name, inp, out, cached)
            if fingerprint in by_fingerprint:
                # the Go page repeats its table once per plan tab: identical
                # token prices, a larger monthly limit under Go Plus — keep
                # one line carrying the more generous allowance
                old = entries[by_fingerprint[fingerprint]]
                if limit is not None and (old.limit is None or limit > old.limit):
                    entries[by_fingerprint[fingerprint]] = replace(old, limit=limit)
                continue
            by_fingerprint[fingerprint] = len(entries)
            entries.append(
                PriceEntry(
                    tier=tier,
                    model_id=model_id,
                    name=name,
                    input=inp,
                    output=out,
                    cached_read=cached,
                    limit=limit,
                    free=inp == 0.0,
                )
            )
    return entries


def refresh_prices(directory, get=None) -> list[str]:
    """Snapshot every tier's pricing table; returns the files written."""
    from .fetch import http_get

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    saved = []
    for tier, url in PRICING_URLS.items():
        entries = parse_pricing((get or http_get)(url), tier)
        if not entries:
            # docs moved or renamed a column: never clobber a good snapshot
            continue
        path = directory / f"{tier}_prices.json"
        path.write_text(json.dumps([asdict(e) for e in entries], indent=1))
        saved.append(path.name)
    return saved


def load_prices(directory) -> dict[str, list[PriceEntry]]:
    """Snapshots in *directory*, keyed by tier; absent files are not an error."""
    prices: dict[str, list[PriceEntry]] = {}
    for path in sorted(Path(directory).glob("*_prices.json")):
        tier = path.name[: -len("_prices.json")]
        try:
            rows = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        entries = []
        for row in rows:
            try:
                entries.append(PriceEntry(**row))
            except (TypeError, KeyError, ValueError):
                continue
        prices[tier] = entries
    return prices
