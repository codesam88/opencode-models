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


def weighted_cost(input_: float | None, output: float | None,
                  cached_read: float | None) -> float | None:
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


def nominal_free_rate(model_id: str) -> float:
    """The nominal $/1M of a free model id, whatever names it.

    Matches the slug with or without its ``-free`` suffix, since the catalog
    record (``stepfun/step-5-preview``) and the tier line
    (``step-5-preview-free``) spell it both ways for one model.
    """
    slug = model_id.split("/")[-1]
    for key in (slug, f"{slug}-free",
                slug[:-len("-free")] if slug.endswith("-free") else slug):
        if key in FREE_TIER_NOMINAL_RATES:
            return FREE_TIER_NOMINAL_RATES[key]
    return FREE_TIER_DEFAULT_RATE


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
            cells = [_cell_text(c) for c in
                     re.findall(r"<t[hd][^>]*>([\s\S]*?)</t[hd]>", tr)]
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
            cached = (_money(row[i_cached])
                      if i_cached is not None and len(row) > i_cached else None)
            limit = (_limit(row[i_limit])
                     if i_limit is not None and len(row) > i_limit else None)
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
            entries.append(PriceEntry(
                tier=tier, model_id=model_id, name=name, input=inp, output=out,
                cached_read=cached, limit=limit, free=inp == 0.0,
            ))
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
