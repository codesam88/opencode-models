# model-pareto

Price/score Pareto frontier of AI models, from the
[opencode.ai/data](https://opencode.ai/data) catalog.

For a chosen benchmark, the tool plots every priced model's score against its
input price (log scale) and lists the **Pareto frontier**: models where no
other model is both cheaper and scores higher. Frontier membership is the
claim; everything else is context.

## Usage

```sh
python3 -m modelpareto fetch                     # refresh data/ from opencode.ai
python3 -m modelpareto benchmarks                # list benchmarks + coverage
python3 -m modelpareto render                    # pareto.html, best-covered benchmark
python3 -m modelpareto render --benchmark "SWE-Bench Verified" --out swe.html
python3 -m modelpareto list --source zen         # three tables: value, worst, priciest
python3 -m modelpareto list --source go --benchmark "SWE-Bench Verified"
```

`render` defaults to the benchmark covering the most models. The output is one
self-contained HTML file (inline SVG, no external assets) — open it from disk
or serve the directory.

`list` filters to a Console tier (`zen` = curated, `go` = open, `all` = the
whole catalog) and prints three tables: **best value** first, then **worst
score** (bottom 30%) and **highest cost** (top 30%). The culls are disjoint
and cost claims first, so a model that is both priciest and lowest-scoring is
reported once, under cost.

Best value splits into bands — **free** (use first), **included usage**
(lines the tier grants a monthly allowance for), **pay-as-you-go** — and is
ranked by score per dollar *inside* each band. A line's cost is 20% input +
60% cached read + 20% output; `--price input|output` picks a single rate
instead.

Ranking combines the two benchmark families: Terminal-Bench rows are ordered
by score per dollar and SWE-Bench-only rows are inserted at their SWE
percentile (`--benchmark X` ranks on one benchmark instead). The families are
not comparable, so they only indicate relative performance.

Pricing comes from the tier docs rather than the catalog, which carries one
Zen-style price per model: documented rates replace it, and every priced
variant (peak/off-peak, token window) gets its own line. Free lines — `-free`
tier slugs and zero-priced models — are priced at a tenth of the cheapest
real price in the set and tagged `[free]`, so "free" sorts and divides like
any other cost instead of going infinite.

Tier membership lists ship in `modelpareto/sources/` as slug files scraped
from the tier pages; doc-calibrated prices are snapshotted in
`data/tier_prices/` and refreshed by `fetch` (nothing else fetches docs).
Refresh the slugs manually when the tiers change.

Stdlib-only, Python ≥ 3.10, no dependencies. Tests: `python3 -m unittest discover -s tests`.

## Data

`data/` holds one JSON per model, copied verbatim from
`https://opencode.ai/data/<lab>/<model>.json`. Parsing is tolerant: models
without pricing or without the selected benchmark are simply excluded from the
frontier rather than failing a render.

`data/tier_prices/{zen,go}_prices.json` are snapshots of the Console tier
pricing tables (parsed from the tier docs), rewritten by `fetch`.

## Caveats

- Prices are list prices from one catalog (OpenCode's); they can lag provider
  price changes. Tier pricing is snapshotted offline (`data/tier_prices/`),
  so a stale snapshot is stale prices until the next `fetch`.
- Benchmarks are heterogeneous (metric, harness, conditions differ per source).
  `list` orders Terminal-Bench rows and SWE-Bench rows separately rather than
  mixing their scores; compare within one benchmark.
- Frontier membership depends entirely on which models the catalog covers.
