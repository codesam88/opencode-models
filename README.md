# opencode-models

List [Opencode Zen / Go](https://opencode.ai/data) models by agentic coding score
and score per dollar.

Tired of trying to correlate the latest OpenCode model listings with available
benchmarks and costs?  This utility uses public/scraped benchmark reports and
the opencode API to show the current best and cheapest available models:

<img width="1050" height="218" alt="image" src="https://github.com/user-attachments/assets/f3027018-f3ff-4416-8dd9-3f69ce43ed67" />


## TLDR; quick steps

```sh
uvx opencode-models
alias ocm=opencode-models

ocm auth  # register an openapi token to always fetch the current 'live' models
ocm auth --plan go-plus  # only if your workspace is on Go Plus (limits differ)
ocm fetch # fetch benchmarks and model info from supported sources
ocm list  # List non-expensive, non-trivial models, sorted by score/$
ocm next  # 6 next-best recommended models to use (tell your agent to use this)
```

## Install

Run it once with `uvx`, or install the `opencode-models` binary to your
`$PATH`:

```sh
uvx opencode-models              # no install, just run it
uv tool install opencode-models  # install the binary
```

The name is longer than it wants to be for daily typing; a shell alias
fixes that in one line: `alias ocm=opencode-models`.

### First run

```sh
ocm auth   # save a Console token so the views are live (recommended)
ocm fetch  # download benchmark data and model info (~1 minute first time)
```

`auth` stores an OpenCode Console service-account key at
`~/.config/opencode-models/api_key` (owner-only, never echoed); the
`OPENCODE_API_KEY` environment variable takes precedence if you set one.
If your workspace is on the Go Plus plan, say so once —
`ocm auth --plan go-plus` (stored beside the key; `usage --plan`
overrides it for one run) — because usage math defaults to Go limits
and Plus caps run 2–4× higher.
`fetch` is the only command that downloads data — benchmark numbers,
tier prices, official boards — into your data directory:
`$OPENCODE_MODELS_DATA` if set, else `~/.local/share/opencode-models`.

```sh
ocm list # show best performing models ranked by score/cost
ocm list --tables all # include 30% worst perf and 30% highest cost tables
```
<img width="834" height="639" alt="image" src="https://github.com/user-attachments/assets/2ea643b3-a011-443e-a9ba-f20341ab7377" />

```sh
ocm usage # display current usage per model relative to weekly/monthly limits
```
<img width="836" height="484" alt="image" src="https://github.com/user-attachments/assets/eade91ce-fc10-4102-84b3-a22210f2000f" />
 


### Working from a checkout

```sh
just setup       # uv sync + enable git hooks (pre-commit lint/audit,
                 # pre-push guard for the local-data branch)
just test        # unittest suite
just lint        # ruff lint, format check, complexity gate
just check       # tests + entry point + wheel build
just run <cmd>   # the console against ./data, e.g. just run next
```

`just install` additionally seeds the reference dataset into your user
data dir (missing files only — it never overwrites fetched results, while
tracked curation deploys as shipped). The tool is stdlib-only: Python
≥ 3.10, no runtime dependencies; ruff comes from the locked dev group.

Project pointers: [SECURITY.md](SECURITY.md) (what touches the network,
scraper etiquette, how to report a problem), [LIMITS.md](LIMITS.md) (what
the plans actually meter), [CONTRIBUTING.md](CONTRIBUTING.md) (workflow,
releasing).

## Usage

Three commands cover daily use; four more exist for auditing and
maintenance:

| command | what it shows |
|---|---|
| `list` | the best-value table of what the plans offer today — free, included-usage and pay-as-you-go bands ranked by score per dollar |
| `next` | the next models to reach for: free and paid picks interleaved, Zen/Go alternating (default 6; tell your agent to use these) |
| `scores` | every offered model, one column per benchmark — the raw number beside where it lands |
| `models` | endpoint + display name for every model the plans list (`--all` adds an `Avail` column: `[x]` served, `[ ]` known-unserved, `[?]` unknown) |
| `overrides` | benchmark numbers more than one source reports, and which value the tool keeps |
| `fetch` | refresh everything: benchmarks, tier prices, official boards, availability |
| `usage` | Go allowance: Week/Month sections per model — Spent/Limit, Reqs, token mix, measured vs page-estimated ¢/req; credit balance in the header |
| `auth` | store the Console token for live views (owner-only file) |

### Live views and the token

Every `list` and `next` run looks for a Console token first — the
`OPENCODE_API_KEY` environment variable, then the file `auth` wrote. **With
a token the view is live:** it reads the inference gateway's model lists
(`opencode.ai/inference/v1/models` and `.../go/v1/models`) on each run —
the models this key can actually call, which is the plans' real
availability — and records the result so later offline runs stay current.
Without a token the first line warns that availability may be stale and
the last `fetch` snapshots decide (those are refreshed by any keyed
`fetch` or `models` run). A keyed run that comes back with **no Go
models** prints a warning: check that the models are enabled in the
Console and that the key is a **personal** one (Account → Personal API
Keys) with model permissions — a service-account key sees the workspace
catalog without entitlements. An invalid or expired token falls back to
snapshots with a 401 warning.

`--all-models` (alias `--all`) bypasses the availability filter entirely
— what you want when auditing rather than choosing.

### Common options

- `--source zen|go|all` — one provider or the full-catalog view
  (default: both providers merged)
- `--all-models` / `--all` — ignore the availability filter
  (`list`, `next`, `scores`)
- `--tables all` — add the worst-score and highest-cost tables to `list`
- `--benchmark NAME` — rank `list` on one benchmark instead of the
  combined score
- `--count N` — how many picks `next` recommends (default 6)
- `--cost est|fix|input|output` — cost base for the score/$ rank (default: `est`):
  est ¢/req from each model's published token mix, the Go plan's median
  where a model publishes none (~); fix 20/60/20 input-cached-output mix;
  input/output rate only
- `--data DIR` — point at a different data directory (checkout: `--data data`)

```sh
ocm list --tables all               # also the culled worst/pricey tables
ocm list --source zen               # one provider only
ocm next --count 8 --source go      # eight picks, Go plan only
ocm scores                          # one column per tracked benchmark
```

## How it works

The rest of this document is the detailed semantics: what a score means,
how the views differ, and where every number comes from.

### Which benchmarks are scored

Five benchmarks carry the scores, and they are named one by one in
`scoring._TRACKED` rather than matched by prefix:

| benchmark | tier models | all models |
|---|---|---|
| Terminal-Bench 2.1 | 37 | 50 |
| SWE-Bench Pro | 34 | 57 |
| Terminal-Bench 4.0 | 27 | 28 |
| SWE-Bench Verified | 18 | 77 |
| **DeepSWE 1.1** | **25** | **29** |

They are the most relevant and the most broadly available at the same time:
Terminal-Bench is the closest thing to "can this model finish a task on a
real machine", and SWE-bench is the closest thing to "does it fix real bugs",
so together they cover the work this tool ranks on; and between them they are
measured on enough models to compare anyone to anyone. The two SWE-bench
subsets are both kept deliberately — Pro is what the tier broadly carries
(34 models against Verified's 18), and Verified is the canonical metric and by
far the broadest number in the catalog (77).

**DeepSWE 1.1 is the exception, and it earns its place by pairing rather than
by relevance.** It is the narrowest thing here — 29 models out of 476 — and
the weakest on provenance: all 29 numbers are vendor self-reports, and no
board runs it, so it can never set the ends of its own scale. What it does is
supply the second axis for models that have nothing else: nine of the 25
offered models sitting on a single benchmark carry it, and admitting it takes
the scored field from 36 models to 45. `opencode-models scores` names that
group explicitly — "N on one benchmark" — so a thin row is never presented as
a ranked one.

Its signal is largely redundant with the axis next to it. Rank correlation
with Terminal-Bench 2.1, over the models carrying both, is **0.87**: it
mostly re-ranks what TB 2.1 already says. A median over five benchmarks
therefore leans on TB 2.1 twice, which is worth knowing before reading a
small score gap as a real difference. The bare `DeepSWE` name stays out — it
shares no model with `DeepSWE 1.1` in the catalog, so it is an older revision
carrying four models.

Everything else the catalog mentions is context rather than signal, and the
list is short enough to be worth spelling out. The catalog carries 212
benchmark names, of which 137 are held by one or two models — an evaluation
nobody else ran. Those cannot discriminate, so they are excluded twice over:
no spread means no scale, and a benchmark four models carry still hands its
worst a hard 0 that drags the median down. A model resolving 82 on SWE-bench
Verified was landing on 0 overall because of a 17.7 on SEC-Bench Pro.

The rest are excluded on the same ground — each would widen the scoring
equation without adding a model that could not otherwise be scored:

- **Terminal-Bench 3.0** (2 models) and **-Science** are separate task sets
  rather than runs of 2.1, and share only a prefix.
- **Terminal-Bench 2.0** is the same benchmark as 2.1 with an easier, older
  task set — and a public task set gets trained against. A vendor can
  publish a 2.0 number without the model being able to do the work, which is
  the overfitting the rest of this list exists to exclude. Leaving it out
  costs no coverage: the five tier models whose only Terminal-Bench run is a
  2.0 all carry SWE-bench, so none of them loses a score.
- **SWE-Bench Lite** and **Multimodal** reach no tier model at all (9 models
  each, none of them one the console prices).
- **SWE-Bench Multilingual** is carried by 29 models but never alone: every
  model that has it also has Verified or Pro, so it narrows nothing.
- **Terminal-Bench Hard** is a distinct benchmark despite the name.
- **LMArena Elo** measures preference votes on chat answers, not task
  completion, and no model in the catalog carries it.

Adding one is a deliberate change to what a score means, so it wants a
reason: benchmark coverage in the live data is the test.

### How a model's score is computed

Raw benchmark numbers are not comparable: in the live catalog Terminal-Bench
2.1 runs 24.58–89.9, Terminal-Bench 4.0 runs 1.5–64.85 and SWE-bench Verified
0.4–96. So each benchmark
gets its own **0–100 scale** — the best model in the catalog is 100, the
lowest is 0, and distances are proportionate and linear — and a model's
score is the **median** of the scales it has.

The median, not the mean: coverage here is wildly uneven, and one bad result
must not bury a model that is strong on everything else. `opencode-models
scores` gives each of those benchmarks its own column and shows the raw
number beside where it lands, so the arithmetic is checkable:

```
476 models · 5 benchmarks scored on · 45 with scores · 21 on one benchmark · 12 without any

  Model                     Score  Tb 2.1    Tb 4.0     Swe vrfd  Swe pro   Deepswe
  Muse Spark 1.3 Contributor     99  88.8→98                                      75.4→100
  Claude Fable 5                 99  88→97     44.55→62   95→100        80.3→100
```

Two more rules decide what counts as evidence.

**A model needs two benchmarks to have a score.** The median of one number
is that number, so a model measured on a single benchmark it happens to lead
reads as a top model on the strength of one result — in the live catalog 26
of 63 scored models were in that position, and 6 of the top 9 rows were,
including the one leading the whole catalog on a lone Terminal-Bench 4.0 run.
Those models are unranked, not ranked last, and the counts line above the
table says how many rest on one benchmark.

**The ends of a scale are set by numbers someone else can check** — an
official board or an independent evaluator, not a vendor's own figure. Min-max
is only as good as its extremes: counting every number, one uncited 1.5 from
a launch post was the floor of the Terminal-Bench 4.0 scale, which stretched
the range and compressed every model's derived score toward the middle. That
is the rule `scoring.scales` applies, with one guard. Where the checkable
numbers are a small minority of a benchmark's field, the ends stand aside:
only 8 of 50 Terminal-Bench 2.1 numbers come from a board, and scaling that
axis to those eight gave it bounds of 64.3–73.4, which clamped nearly every
model to 0 or 100 and stopped the primary axis discriminating at all. A scale
drawn from a few points is a threshold, not a scale.

So: a benchmark only one or two models carry has no spread, so it cannot
discriminate and is left out — 137 of 212 benchmarks are in that position.
And the score answers "how does this model typically do on what it was
measured on", so a model evaluated on a handful of benchmarks it excels at
still scores near 100.

The console prints what it measured and points here for the arithmetic;
`scoring.py` carries the reasoning next to the functions that do it. The
column headers are short (`Tb 2.1`, `Swe vrfd`) and the legend under the
table spells out which benchmark each one is.

Each row shows the model's **calculated score**, its cost, and which plan
lists it — the per-axis raw numbers live in `scores`, not here. `--benchmark
X` switches to the single-score layout: that benchmark's raw score and its
tag.

Each line is prefixed with a rank marker read off the score itself — `++`
at 90 and above, `+` at 75, `o` at 60, `-` at 50, `--` below that, `·` where
a row has no score to rank (unranked, not last). Absolute bands mean a
strong field can share the top and a weak one is never flattered by it.

Ranking is best score per dollar first. The score is already comparable
across axes — each benchmark reads on its own 0–100 scale and the median
combines them — so value order is the combined score divided by the price,
ties toward the higher score. Rows no benchmark reaches cannot be valued:
free ones close the listing, paid ones follow, and rows nothing reaches
drop out (`--benchmark X` ranks on one benchmark instead). Terminal-Bench
4.0 is a harder task set — its leaders reach ~58 where 2.1's reach ~87 —
so a 4.0 score never competes directly with 2.x numbers, and `--benchmark X`
tags which benchmark a row was ranked on. The axes are not comparable, so
they only indicate relative performance.

Pricing comes from the tier docs rather than the catalog, which carries one
Zen-style price per model: documented rates replace it, and every priced
variant (peak/off-peak, token window) gets its own line. Free lines — `-free`
tier slugs and zero-priced models — are metered at $0 (the export bills
them nothing; the throttle is unpublished rate limits, not the dollar
math), so they sort ahead of every paid line and the value column reads
`—` instead of dividing. They are *shown* with an actual `0` price.

### The `list` views

`list` covers the Console **providers** (`zen` = curated, `go` = open) and
prints the **best value** table. `--tables all` also prints the **worst
score** (bottom 30%) and **highest cost** (top 30%) tables. Either way the
cull happens: those two groups of rows are taken *out* of best value, so the
default view is the models worth looking at and nothing else. The culls are
disjoint and cost claims first, so a model that is both priciest and
lowest-scoring is reported once, under cost. Cost decides *who* lands in the
highest-cost table and score decides *who* lands in the worst-score table,
but both culls are shown worst-scoring first.

By default both providers are merged into one view. Rows that agree on model,
score and cost are collapsed to a single line *before* ranking and culling —
they are one offer, not two — with the providers enumerated in the `Prov`
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
60% cached read + 20% output; `--cost input|output` picks a single rate
instead, `--cost est` (the default) the cost per request — each line
priced from its own rates under its model's published token mix, or the
Go plan's median mix (marked `~`) where the model publishes none,
whether or not a plan record covers it. The score and the value are
rounded integers: score per dollar explains the best-value order and
free lines read `-` and sort before every paid line. The value column
names the unit its own table needs — `Score/$`, or `Score/¢` (the same
ratio over a hundred) once every value in that table is above 100,
where per-dollar numbers run to five digits — and each table is
measured in the unit it names, so a table reading cents does not widen
a neighbour's column. The cost column
counts cents — `¢/req` under `est`/`fix` keeps up to two decimals, so a
fraction of a cent survives (a $0.0612 line reads as `6.12`), while
`¢/1m` under `input`/`output` stays whole cents. Free lines still show a
`0` cost. Columns are measured from the lines actually being shown
(header included), so nothing shifts when a price or a provider list
outgrows the rest.

### The `next` sequence

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
- **Alternation is forced, on two axes.** Slots alternate between the free
  and paid bands — even slots take the best remaining free pick, odd slots
  the best remaining paid one — so a capped free tier still leaves half the
  sequence usable, and a fresh one still leads with what costs nothing.
  Within each slot the pick alternates between Zen and Go wherever both can
  supply one, so following the sequence spreads across the two plans instead
  of draining one allowance before touching the other. Either alternation
  gives way when it must: a dry band yields its slot to the other band, a
  plan with nothing eligible left is passed over, and the strongest free
  pick always leads.
- **One model, one slot.** Free on Zen and paid on Go are two lines for one
  model — the `[free]` suffix is the only difference in the name — so a
  model is never recommended twice, and price variants of one model do not
  take two slots either.

The view keeps the rules out of the output: it prints one headline, the
sequence, and a pointer to [LIMITS.md](LIMITS.md) for what allowances cost.
It does not consult how much of each allowance is left.

### Where the data comes from

`data/` is split by who writes it — and **the scraped layers are fetched, not
committed**. They are third-party records, they go stale, and they are large;
`opencode-models fetch` populates them into your data directory, and a private
`local-data` branch keeps a committed copy for offline work (see below).

| directory | contents | written by | in the repo? |
|---|---|---|---|
| `opencode/` | upstream records, one JSON per model, verbatim from `https://opencode.ai/data/<lab>/<model>.json` | `fetch` only | no — `.gitignore`d |
| `aiarena/` | arena.ai (LMArena) Elo cache, `{slug: elo}` | `fetch` | no — `.gitignore`d |
| `available/` | the live model set per tier from the inference gateway lists (served = callable), annotated with the Console config API's known set | keyed `fetch` / `models` | no — `.gitignore`d |
| `plans/` | validated plan allowance snapshots: per-model token prices, monthly limits per plan, token/req assumptions, request estimates (gated by estimate reproduction + structural checks) | `sync` refreshes stale snapshots on each run (~4h availability, ~8h pricing); `just plans-sync` forces a refresh, `just plans-freeze` promotes to the shipped fallback | no — `.gitignore`d |
| `modelsdev.json` | the go/zen catalog: models.dev's `opencode-go` and `opencode` providers sliced out of its 6 MB catalog (id, name, cost per 1M tokens, context limits, `canonical_model_id` for the join back to the cards) | `sync` refreshes it when stale (~24h) | no — `.gitignore`d |
| `boards/` | official benchmark boards, fetched from whoever runs them (see below) | `fetch` | no — `.gitignore`d |
| `usage/` | Go window poll history (`go_windows.jsonl`, appended by keyed `usage` runs for the offline fallback) | keyed `usage` | no — `.gitignore`d |
| `overrides/` | hand-pinned benchmark values (see `data/overrides/README.md`) | nobody — `overrides` reports disagreements | **yes** (the directory, no data) |

Shipped fallbacks (committed, versioned deliberately, validated — the
offline copy when no snapshot exists): `opencode_models/data/plans/go.json`
(promoted from `data/plans/` via `just plans-freeze`, pinned by
test) and `opencode_models/data/plans/zen.json` (shipped roster derived from the
plan snapshots). Rule of thumb: mutability decides the home — refreshable
state lives in `data/`, frozen seeds ship in the package.

`fetch` downloads only the models the plans offer (use `--all` for the
full catalog). Records already on disk are never deleted, so a dropped
model keeps its place in the scales rather than moving every score.

#### Which models the plans actually offer

Providers unlist models and move them between plans, so "which models can I
use" is a fact about today, not a property of the codebase. Tier membership
now comes from the same snapshots `fetch` writes — the live `available/` set,
then the runtime `plans/` copy, then the shipped plan
fallback — so there is no hand-maintained roster to drift. In the live data 13
Go models were still being listed, scored and recommended after the provider's
own documentation had stopped listing them, among them `hy3-preview` and
`glm-5`, two of the models an active backfill was chasing numbers for.

`fetch` already snapshots each tier's documentation for prices, but the
docs go stale — providers unlist models the pages still name, and list new
ones the pages do not. So availability comes from **the inference
gateway's model lists** (`opencode.ai/inference/v1/models` and
`.../go/v1/models`), fetched keyed so they are this workspace's
entitlements, and snapshotted to `data/available/` on every keyed run
(`opencode_models/live.py`). The Console config API (`/api/v2/config`) is
only consulted alongside them to mark models it knows but does not serve;
the docs snapshot stays the offline fallback. `opencode-models models`
prints the record as one sorted table — endpoint (preferring Go where
both plans list the model) and display name — with `--all` adding
known-but-unserved rows plus an `Avail` status column (`[x]`/`[ ]`/`[?]`).
The internal ids the gateway serves (`test`, `*-backup`) are the only
rows never shown; a served model no source can name is listed with `-`
where its name would go.

**Which rows exist** comes from the go/zen catalog: `sync` slices the
`opencode-go` and `opencode` providers out of
[models.dev](https://models.dev) (an open database of models and
providers) into `data/modelsdev.json`, and a served model with no record
there is not listed. That is also what keeps the gateway's internal ids
(`test`, `*-backup`) out — models.dev carries neither, so no filter of
ours has to know about them — and it is where a row's name, price and
context limits come from when no benchmark card exists. Without the
slice (fresh clone, offline) the cards decide, as before.

- **`list` and `next` show only what a plan lists** — live with a token
  (checked every run), the last snapshots offline. `next` therefore never
  recommends a model that has been dropped.
- **`--all-models`** (alias `--all`, also on `next`) lists the tier's whole
  catalog instead — every record models.dev carries for it — bypassing the
  availability filter, which is what you want when auditing rather than
  choosing.
- **The view says what it covers.** `list` notes it shows only what the
  plans offer right now and points at `--all-models` for the rest, without
  naming names — which models a plan dropped is audit material, not a
  headline.
- **A tier with no snapshot is unknown, not empty.** Offline and before the
  first `fetch`, nothing is filtered; restricting the console to an empty set
  because nobody fetched would hide everything.
- **The scales do not move.** A model you cannot buy still marks where the
  field is, so a score never changes because a plan changed what it sells.

#### Official boards, fetched from their owners

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
every replacement is printed by `overrides`. Sources that
only render numbers in a browser with no embedded data (Artificial Analysis,
Vals AI, Scale's SWE-Atlas, DeepSWE) are not fetched; their numbers arrive
via the registry or a curated override. A JS-rendered page is not
automatically a dead end, though — tbench.ai looked like one and is not,
because a Next.js page streams the data it renders from. aider's polyglot
board *is* static HTML but was dropped as stale and redundant: its newest
entry is GPT-5-era (Aug 2025), and diff-editing is already covered by
SWE-bench and Terminal-Bench.

If you want the dataset committed rather than fetched, the `local-data`
branch keeps a copy — it is intentionally never pushed.

#### Curated overrides

The upstream layer is never edited: curated scores are merged at load time,
keyed by (benchmark name, version), so an overlay can *add* a benchmark the
registry lacks or *pin* a value it also carries. A pinned value applies only
when its source is at least as good as upstream's — sources are tiered:
independent evaluators that run every model on one harness (Artificial
Analysis) beat official benchmark boards (tbench.ai), which beat vendor
model cards and launch posts. Everything a disagreement touches is printed
by `overrides`; to accept the catalog value, delete the entry. See
`data/overrides/README.md` for the entry format and rules.

Parsing is tolerant: models without pricing or without the selected
benchmark are skipped rather than failing the view.

#### Data due diligence

What this repo publishes: hand-pinned benchmark numbers
(`data/overrides/`, each carrying its source URL and provenance note),
plus code and docs. Everything the tool reads in bulk — the model
catalog, tier snapshots, official boards, arena cache — is fetched into
gitignored directories and never committed. No workspace or user data
is ever written to the repo: the usage export is aggregated in memory
and only per-model numeric totals reach disk.

Why we think republishing those numbers is fair: benchmark scores are
facts, and every curated value cites the page, chart, or board it was
read from, with the date and method, so anyone can check or contest it.
What we have *not* done is review every source site's Terms of Service,
so if any number here violates a site's terms as you read them, that is
unintentional — please open a GitHub issue, or a private security
advisory if the matter is sensitive (see SECURITY.md), and we will
correct or remove it.

## Caveats

- Prices are list prices from one catalog (OpenCode's); they can lag provider
  price changes. Tier pricing is snapshotted offline (`data/plans/`):
  read commands refresh a stale snapshot automatically (availability
  ~4h, plans ~8h; `sync` reports per source) and warn instead of
  failing when offline, proceeding on the last snapshots.
- Benchmarks are heterogeneous (metric, harness, conditions differ per source).
  `list` orders Terminal-Bench rows and SWE-Bench rows separately rather than
  mixing their scores; compare within one benchmark.
- Coverage depends on which models models.dev carries for the two opencode
  providers: a served model with no record there is invisible to every view
  until models.dev lists it.

## Limitations

What the tool deliberately does not do, and why.

**Cost per request is not a ranking axis.** The same model served to two
workspaces burns a different number of tokens per request — agent traffic
with full context re-sends versus chat-sized prompts differ by orders of
magnitude — so a model's $/request is a property of how *you* use it, not
of the model. Folding a per-request cost into score-per-dollar would rank
models on one workspace's habits: the same table would order differently
for every reader. The metric is not universally available either — no
catalog, board, or Console endpoint publishes typical per-request token
volumes (the docs' estimated-request tables imply one, but disagree with
measured traffic by 10–100× on heavy agent use) — so there is nothing
stable to divide by. `list` and `next` rank on list price, with free
lines at $0: comparable across workspaces, even where
they are less precise for any single one.

**Allowances and per-request spend are not shown.** For the same reason,
there is no allowance column and no $/request column: a Go plan's remaining
dollars depend on metered usage the tool would have to snapshot per
workspace, and free tiers publish no cap at all. What the plans grant is
documented once in [LIMITS.md](LIMITS.md); what you have used is in the
Console, not here.
