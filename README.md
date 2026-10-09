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
python3 -m modelpareto list                      # both providers merged, three tables
python3 -m modelpareto list --source zen         # one provider only
python3 -m modelpareto list --source all --benchmark "SWE-Bench Verified"
```

`render` defaults to the benchmark covering the most models. The output is one
self-contained HTML file (inline SVG, no external assets) — open it from disk
or serve the directory.

`list` covers the Console **providers** (`zen` = curated, `go` = open) and
prints three tables: **best value** first, then **worst score** (bottom 30%)
and **highest cost** (top 30%). The culls are disjoint and cost claims first,
so a model that is both priciest and lowest-scoring is reported once, under
cost. Cost decides *who* lands in the highest-cost table and score decides
*who* lands in the worst-score table, but both culls are shown worst-scoring
first.

By default both providers are merged into one view. Rows that agree on model,
score and cost are collapsed to a single line *before* ranking and culling —
they are one offer, not two — with the providers enumerated in the `PROV`
column immediately before the model name (`go,zen`, `zen`, `go`, or `-` for
catalog models in neither tier; those only appear with `--source all`). When
only some of the named providers fund a line from a monthly allowance, the
more generous band wins: the line is available that way from at least one of
them. Rates that differ (off-peak, token window, a limit one provider has and
the other has not) keep their own line each. `--source zen` / `--source go`
narrows the view to a single provider.

Best value splits into bands — **free** (use first), **included usage**
(lines the tier grants a monthly allowance for), **pay-as-you-go** — and is
ranked by score per dollar *inside* each band. A line's cost is 20% input +
60% cached read + 20% output; `--price input|output` picks a single rate
instead. The numbers are all rounded integers: `SCORE` and `SCORE/$` round to
whole numbers, and the cost column counts cents (`¢/1M` = round(price·100)),
so a $0.0612 line reads as `6`, not `0.0612`. Columns are measured from the
lines actually being shown (header included), so nothing shifts when a price
or a provider list outgrows the rest.

Ranking combines three benchmark axes: Terminal-Bench 2.x rows are ordered by
score per dollar and build the skeleton; rows that carry only SWE-Bench, or
only the harder Terminal-Bench 4.0, are inserted at their own axis' percentile
(`--benchmark X` ranks on one benchmark instead). 4.0 is a harder task set —
its leaders reach ~58 where 2.1's reach ~87 — so a 4.0 score never competes
directly with 2.x numbers and is tagged `TB4` in the table. 2.1 builds the
skeleton while it has the coverage (37 tier models against 12); when that
inverts, promote 4.0 to primary by changing `TB4_FAMILY`'s role in
`ranking.py`. The axes are not comparable, so they only indicate relative
performance.

Pricing comes from the tier docs rather than the catalog, which carries one
Zen-style price per model: documented rates replace it, and every priced
variant (peak/off-peak, token window) gets its own line. Free lines — `-free`
tier slugs and zero-priced models — are *ranked* at a tenth of the cheapest
real price *in the whole view* (across providers, one shared scale) and tagged
`[free]`, so "free" sorts and divides like any other cost instead of going
infinite, but they are *shown* with an actual `0` price and `free` under
`SCORE/$` — the nominal price never reaches the console.

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
