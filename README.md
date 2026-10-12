# opencode-rank

Price/score Pareto frontier of AI models, from the
[opencode.ai/data](https://opencode.ai/data) catalog.

Unrelated to the opencode coding agent (`sst/opencode`): this tool ranks
*models* — the name follows the data lineage, since opencode's catalog is
the primary source (with tbench.ai, Artificial Analysis and the arena
dataset behind it).

For a chosen benchmark, the tool plots every priced model's score against its
input price (log scale) and lists the **Pareto frontier**: models where no
other model is both cheaper and scores higher. Frontier membership is the
claim; everything else is context.

## Install

The tool is stdlib-only and installs with anything that understands Python
packages — [uv](https://docs.astral.sh/uv/) is the recommended path:

```sh
uv tool install .            # install the `opencode-rank` binary to $PATH
uvx --from . opencode-rank   # or run it once without installing
```

A fresh install has no data yet, so populate it once (this is the only
network-touching command):

```sh
opencode-rank fetch          # downloads into your user data dir
```

Data lives in `$OPENCODE_RANK_DATA`, else the XDG user data dir
(`~/.local/share/opencode-rank`). Working from a checkout instead? Point at
the committed dataset with `--data data` (or `OPENCODE_RANK_DATA=data`).

For development, `uv sync` creates the repo virtualenv; `just test`, `just
list` and `just check` wrap `uv run` so the console and the tests run without
installing anything.

## Usage

```sh
opencode-rank fetch                     # refresh the data from opencode.ai
opencode-rank conflicts                 # curated overrides vs upstream
opencode-rank benchmarks                # list benchmarks + coverage
opencode-rank render                    # pareto.html, best-covered benchmark
opencode-rank render --benchmark "SWE-Bench Verified" --out swe.html
opencode-rank list                      # both providers merged, best value
opencode-rank list --tables all         # also worst-score and highest-cost
opencode-rank list --source zen         # one provider only
opencode-rank list --source all --benchmark "SWE-Bench Verified"
opencode-rank next                      # the next 4 models to use, in order
opencode-rank next --count 6 --source go
```

`render` defaults to the benchmark covering the most models. The output is one
self-contained HTML file (inline SVG, no external assets) — open it from disk
or serve the directory.

`next` answers the narrower question "what should I reach for next?". It
draws from the same pipeline `list` uses, so it can never disagree with the
ranking it came from. Three rules shape the sequence:

- **Eligibility is strength, not the `++` marker.** Picks must be within 75%
  of the strongest score in the catalog. The marker is not used to gate them:
  it is a position in an ordering that divides by price, so it measures
  value. In the live data MiMo-V2.6-Pro scores 89.9 — the best model in the
  catalog — and is graded `o`, while a free model scoring 24.58 is graded
  `++`. Gating on the marker recommends the weak model and misses the strong
  one.
- **Alternation is forced.** Picks alternate between Zen and Go wherever
  both can supply one, so following the sequence spreads across the two
  plans instead of draining one allowance before touching the other. A paid
  Go model may come before a free Zen one. It gives way in two cases only:
  a plan has nothing eligible left, or taking the strongest candidate first
  would demote it.
- **One model, one slot.** Free on Zen and paid on Go are two lines for one
  model — the `[free]` suffix is the only difference in the name — so a
  model is never recommended twice, and price variants of one model do not
  take two slots either.

The view keeps the rules out of the output: it prints one headline, the
sequence, and a pointer to [LIMITS.md](LIMITS.md) for what allowances cost.
It does not consult how much of each allowance is left.

`list` covers the Console **providers** (`zen` = curated, `go` = open) and
prints the **best value** table. `--tables all` also prints the **worst
score** (bottom 30%) and **highest cost** (top 30%) tables. Either way the
cull happens: those two groups of rows are taken *out* of best value, so the
default view is the models worth looking at and nothing else. The culls are disjoint and cost claims first,
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
instead. The numbers are all rounded integers: `SCORE/$` rounds to
whole numbers, and the cost column counts cents (`¢/1M` = round(price·100)),
so a $0.0612 line reads as `6`, not `0.0612`. Columns are measured from the
lines actually being shown (header included), so nothing shifts when a price
or a provider list outgrows the rest.

The score columns show **every ordering axis side by side** — `TB2`, `TB4`,
`SWE` — each carrying the model's raw best run on that axis, blank where it
has none. A row therefore displays both base scores the ordering is built
from instead of one merged number plus a "which axis" tag, and the axes
stay comparable because 2.x and 4.0 never share a column (a 55.8 next to an
83 reads as a regression otherwise). `--benchmark X` switches to the
single-score layout: that benchmark's score and its tag.

Each line is prefixed with a rank marker — `++` the top decile of the
ordering, `+` the next tenth, `o` the middle, `-` the bottom fifth, `·` a row
no benchmark reaches (unranked, not last). The marker rides on position in
the *whole* ordering, so a row in the worst-score or highest-cost table still
shows how it ranked overall — a priciest-model row can be `++`.

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

Tier membership lists ship in `opencode_rank/sources/` as slug files scraped
from the tier pages; doc-calibrated prices are snapshotted in
`data/tier_prices/` and refreshed by `fetch` (nothing else fetches docs).
Refresh the slugs manually when the tiers change.

Stdlib-only, Python ≥ 3.10, no dependencies. Tests: `just test`
(`uv run python -m unittest discover -s tests`); `just check` adds a wheel
build. CI runs the suite on 3.10/3.12/3.14 without the dataset, since CI
never fetches third-party records. What the tool touches on the
network, the scraper etiquette and how to report a security issue are in
[SECURITY.md](SECURITY.md). What the *plans* actually meter — Zen's credits,
Go's per-model dollar limits and windows — is in [LIMITS.md](LIMITS.md).

## Data

`data/` is split by who writes it — and **the scraped layers are fetched, not
committed**. They are third-party records, they go stale, and they are large;
`opencode-rank fetch` populates them into your data directory, and a private
`local-data` branch keeps a committed copy for offline work (see below).

| directory | contents | written by | in the repo? |
|---|---|---|---|
| `opencode/` | upstream records, one JSON per model, verbatim from `https://opencode.ai/data/<lab>/<model>.json` | `fetch` only | no — `.gitignore`d |
| `aiarena/` | arena.ai (LMArena) Elo cache, `{slug: elo}` | `scores --refresh` | no — `.gitignore`d |
| `tier_prices/` | snapshots of the Console tier pricing tables | `fetch` | no — `.gitignore`d |
| `boards/` | official benchmark boards, fetched from whoever runs them (see below) | `fetch` | no — `.gitignore`d |
| `overrides/` | an escape hatch for hand-pinned benchmark values — currently empty, since `fetch` brings the boards themselves (see `data/overrides/README.md`) | nobody — `fetch` reports disagreements via `conflicts` | **yes** (the directory, no data) |

### Official boards, fetched from their owners

`fetch` also pulls the boards themselves, so the axes are anchored on the
numbers of whoever runs each benchmark rather than on one registry's
transcription (which is ~93% vendor self-report):

| board | where it comes from | what it carries |
|---|---|---|
| Terminal-Bench 4.0 | the official board page's own data payload (tbench.ai; the page renders in a browser, but streams the leaderboard it renders from) | accuracy ± CI95, the agent, the reasoning effort and the date per board row |
| SWE-bench (Verified, Multilingual, Multimodal, Lite) | swebench.com's embedded leaderboard | `resolved` per checked submission, with the agent that ran it |

A board row replaces a model's score only when its source is *strictly*
better tiered than what the model already carries — an official board beats
a vendor's own number (MiniMax-M2's self-reported 69.4 on SWE-bench Verified
yields to the board's 61.0), but it never displaces an independent run, and
every replacement or disagreement is printed by `conflicts`. Sources that
only render numbers in a browser with no embedded data (Artificial Analysis,
Vals AI, Scale's SWE-Atlas, DeepSWE) are not fetched; their numbers arrive
via the registry or a curated override. A JS-rendered page is not
automatically a dead end, though — tbench.ai looked like one and is not,
because a Next.js page streams the data it renders from. aider's polyglot
board *is* static HTML but was dropped as stale and redundant: its newest
entry is GPT-5-era (Aug 2025), and diff-editing is already covered by
SWE-bench and Terminal-Bench.

Working from a checkout: `just fetch` (or `opencode-rank fetch`) once, then the
`--data data` recipes work. If you want the dataset committed rather than
fetched, use the `local-data` branch — it is intentionally never pushed.

The upstream layer is never edited: curated scores are merged at load time,
keyed by (benchmark name, version), so an overlay can *add* a benchmark the
registry lacks or *pin* a value it also carries. A pinned value applies only
when its source is at least as good as upstream's — sources are tiered:
independent evaluators that run every model on one harness (Artificial
Analysis) beat official benchmark boards (tbench.ai), which beat vendor
model cards and launch posts. Everything a disagreement touches is printed
by `conflicts`; to accept upstream, delete the entry. Rows whose model
carries an overlay are marked `*` in `list`.

Parsing is tolerant: models without pricing or without the selected
benchmark are simply excluded from the frontier rather than failing a render.

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
