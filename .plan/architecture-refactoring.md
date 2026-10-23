---
status: in-progress
type: decision
priority: high
area: backend
---

## Notes

Define the target high-level architecture for opencode-models and capture it
as `docs/architecture.md`. Today the code is ad hoc: output rendering is
mixed with commands (`cli.py` ~1900 lines owns parsing, business decisions,
formatting, and `print` per command), commands are mixed with function
(`_rows_for`, roster synthesis, cost-base selection, grading live next to
argparse), and function rests on several sources with ad hoc aggregation
rules and no lifecycle management (catalog, tier prices, plan snapshots,
live gateway lists, arena cache, boards, curated overlays — each with its
own freshness/fallback/merge rule decided at the call site).

The deliverable is the architecture document only — no implementation, no
refactor, no code changes. It inventories implemented functionality, fits it
into a consistent layering (ingestion → storage → pure domain processing →
typed per-command use cases → presentation), and states per component its
contract and what must change to get there. No implementation steps, effort
estimates, or sequencing.

## Deep-review pass (before writing)

- Inventory every module under `opencode_models/` (all ~20: `fetch`,
  `pricing`, `boards`+`labelmap`, `arena`, `live`, `availability`, `models`,
  `overrides`, `identity`, `scoring`, `ranking`, `allowance`, `tables`,
  `seed`, `pareto`, `benchmarks`, `auth`, `cli`) plus
  `scripts/update_go_pricing.py`: responsibility, inputs/outputs, I/O
  performed, callers.
- Inventory all 8 commands (`list`, `next`, `scores`, `models`,
  `overrides`, `fetch`, `usage`, `auth`): which pipeline each runs, where
  pipelines agree by convention (`_rows_for`) vs by interface, where each
  re-derives availability/naming/cost.
- Map every `data/` layer (`opencode/`, `tier_prices/`, `boards/`,
  `aiarena/`, `available/`, `plans/`, `usage/`, `overrides/`, `manual/`) +
  shipped fallbacks (`opencode_models/data/plans/`) + `local-data` branch:
  writer, reader(s), freshness rule, fallback position, retention rule.
- Record dead/semi-dead code found (`pareto.frontier` has no caller; `seed`
  mixes install-time deploy with runtime paths) as explicit keep-or-delete
  decisions for the document.
- Read `README.md` "How it works", `LIMITS.md`, `data/overrides/README.md`,
  and the `.plan/` backlog so the document aligns with documented semantics
  rather than redefining them.

## Document shape (target)

- `docs/architecture.md`: problem statement (the three mixings), full
  functionality inventory table, target layering with one-way dependencies,
  per-component contract + what-must-change, target data-flow with
  invariants (scales over full catalog, never-delete records, upstream
  never edited / curation as overlay, ingestion writes / views read),
  structural test strategy (synthetic-input domain suites, synthetic-tree
  storage tests, injected-transport ingestion tests, fake-store use-case
  tests, golden-output presentation tests).
- Out of scope for the document: implementation steps, sequencing, effort,
  API signatures beyond input/output contracts.

## Acceptance

- `docs/architecture.md` exists and covers every module, command, and data
  layer above — reviewer can check each off against the tree.
- Each component states its architectural contract and direction of change;
  no detailed implementation steps or effort included.
- Key structural points captured: ingestion separate from processing;
  domain pure and synthetic-testable; rendering separate from commands;
  commands behind typed interfaces to domain logic.
- `plan lint` passes for this file.

## Revised scope (2026-10-23): two-phase review, design doc as deliverable

Phase 1 — first review of components: inventory what exists, propose a
target architecture, and map each existing component/feature onto it
(where it fits, where it is split across layers, where it has no home).

Phase 2 — deep review per component/feature: for each mapped item decide
whether the component should change to fit the architecture or the
architecture should bend to accommodate a reality the first pass missed.
Either outcome is recorded with reasoning; the architecture is adjusted,
not just the components.

Deliverable is `docs/architecture.md` as a high-level design document:
all major features, their touch points with other components/layers, and
what to consider when growing this 0.1 into a more robust and reliable
tool. Not a detailed action plan — no implementation steps, sequencing, or
effort. Open questions and alternatives considered belong in the document,
not just the winning shape.

## Progress — Phase 1 inventory done (2026-10-23, worker: opencode-go/beam-free)

Worker returned a read-only inventory (session ses_e96ac2e5cffefbmTV0YDZJexZm):
module table (21 modules + 2 standalone scripts), 8-command pipeline table,
13-row data-layer table, dead-code list, top-3 violations. Key findings for
the proposal step: `cli._load` is the de facto composition root; `_rows_for`
is the only shared pipeline (`list`/`next`) with availability/naming/cost
re-derived per command; `scripts/update_{go,zen}_pricing.py` duplicate
`allowance.py` logic instead of importing it; `identity.merge_aliases` is
tested but not wired into production; `pareto.frontier`,
`ranking._take`, `allowance.{validate_estimates,merge_snapshots,…}` and
`data/usage/free_tier_nominals.json` have no production readers. Full report
in worker session history; next is the proposed architecture + component
mapping (still Phase 1).

## Progress — Phase 1 proposal: architecture + component mapping (2026-10-23)

Verification of the worker's surprising claims (all confirmed against the
tree unless noted): `pareto.frontier` has no production caller — only
`tests/test_pareto.py` and a stale "Pareto frontier" argparse description
(`cli.py:1753`); `ranking._take` is defined but never called (`_take_banded`
is the live path); `identity.merge_aliases`/`plan_ids` are tests-only, and
`.plan/v01-review.md` (R2 step 2, done plan) independently records full
alias wiring as deferred — so variant folding is partial in production;
`scripts/update_{go,zen}_pricing.py` import nothing from the package and
each define their own `merge`/`validate`, duplicating
`allowance.merge_snapshots`/`validate_estimates`. NOT confirmed:
`data/usage/free_tier_nominals.json` — no reference anywhere in the tree
(code, data/, docs); treat as unverified, do not put it in the design doc
without evidence.

Proposed layering (one-way dependencies, downward only):

1. Ingestion — every network read and input-data disk write. No scoring,
   ranking, or display merging.
2. Storage — owns the data directory: layout, typed loaders, fallback
   chains, freshness answers, seeding, retention. Only layer that knows
   file paths. Returns records + load diagnostics, never paths.
3. Domain — pure processing: records in, records out. No I/O, no `print`.
   Every README "How it works" rule maps to one tested function here.
4. Application — one typed use case per command: orchestration only,
   returns a result object. No formatting.
5. Presentation — renders result objects to text. Owns columns, markers,
   units, TTY behavior. Imports no other layer's logic.

Component mapping (component → home layer → fit):

- FITS (move mostly as-is): `scoring`, `ranking` (already pure),
  `allowance` math, `benchmarks`, `tables`, `auth` (cross-cutting config),
  `labelmap` (as `boards` source configuration).
- SPLIT across layers today, must be separated: `cli.py` (all five —
  parsers stay in Application, `_rows_for`/`_load`/`_synthesize_roster_models`
  to Domain/Application services, all `_header`/`_section`/`_cells`/usage
  formatters to Presentation); `availability.py` (roster fallback chain to
  Storage, `canonical_id`/slug matching to Domain); `fetch.py` (transport +
  writes stay Ingestion, `offered` filtering moves out to the caller);
  `pricing.py` (docs scrape to Ingestion, snapshot load to Storage,
  `weighted_cost` to Domain); `boards.py` (fetchers to Ingestion, tier
  arbitration merge to Domain); `arena.py` (lookup to Ingestion, cache load
  to Storage, axis merge to Domain); `seed.py` (install-time deploy vs
  runtime fallback vs git-subprocess branch reads are three behaviors).
- HOMELESS / decision needed in Phase 2: `pareto.frontier` (wire to a view
  or delete; stale argparse string goes either way); `ranking._take`
  (delete); `scripts/update_*` (import the package vs stay standalone —
  bends against stdlib-only/installability constraints);
  `overrides`+`manual` curation deploy rules (Storage lifecycle);
  live-read-through side effects inside `models`/`list`/`next`
  (explicit Application policy with `origin: live|snapshot` in the result,
  or read-only commands); `usage` command scope (source + math + renderer
  currently one block).
- Prior art: `.plan/v01-review.md` R2 step 2 already owns the
  component-change side of alias wiring when scheduled; the design doc
  records the target, that plan tracks the work.

## Progress — data-freshness tiers + I2-retirement hypothesis (2026-10-23)

Proposed management dimension (three tiers by change rate): T1 large and
rarely changing (benchmarks, per-model base info — `fetch`, weekly cadence);
T2 frequently changing (pricing, live/available endpoints — `sync` flow,
key-gated API as source, user-invoked or lazy: on each run the application
checks the age of the previously fetched data and syncs when older than the
tier threshold, on the order of 4–8h for price/model info); T3 manual
curation (overrides/backfill — before release, optionally at the user).

I2-retirement hypothesis (UNPROVEN — needs a payload probe before Phase 2
decides): tier-price docs scraping may be redundant if the Console config
API carries equivalent pricing. Against it today: `live.fetch_live`
discards everything except the `disabled` flag (only slugs + status reach
`available/`), while the `est` cost base, usage `Est.` column, variant
rates, free marking, and display names still come from `tier_prices/` +
`data/plans/` (manual page-scrape scripts). Retiring I2 requires proof on
two points: (a) the `/api/v2/config` payload actually carries per-model
token prices, limits, and assumptions equivalent to the page snapshots;
(b) an offline fallback for keyless runs (shipped snapshots or retained
`tier_prices/` snapshots) so `list`/`next` keep working without a key.
Probe: authenticated GET of the config URL, compare payload fields vs
`plans/<tier>.json` schema and `PriceEntry` fields, then decide.

DECIDED (2026-10-23, probe worker session ses_e9694aefcffeT1hQO43fNk36uK):
verdict ABSENT — the config payload carries only slugs + `disabled` (+ rare
`package` hint; providers `opencode` 49 models / `opencode-go` 0). No token
prices, limits, assumptions, variants, or free marking anywhere. I2 docs
scraping and `data/plans/` snapshots stay; the config API remains an
availability-only source for `available/`. (Side finding: bare urllib GET
gets 403 — the `User-Agent: opencode-models/<ver>` header is required.)

Export-as-validation (2026-10-23): no API endpoint serves rate cards
(config: slugs + `disabled` only; gateway lists: IDs only; window API:
percentages; billing: balance; export CSV: realized spend per day-model).
DECISION-INPUT for Phase 2: export-derived measured data goes in a SEPARATE
runtime layer (e.g. `data/measured/`), never merged into `plans/*.json`.
Plans snapshots are universal published rate cards (ship in the package,
same for every workspace); export data is workspace-local realized traffic
(key-gated, trailing-30d, daily change). Merging them would conflate "what
the plan charges" with "how I use it" — the conflation README Limitations
rejects for ranking — and would leak workspace measurements into a shipped
artifact. Domain joins the two records at view time (the existing measured
vs page-estimated comparison with explicit inputs); ranking never sees
measured data. The measured file carries its own `meta` (export range,
`fetched_at`) so lazy-sync age checks work per source.

Pricing-source map (2026-10-23, verified): the two pricing layers are NOT
the same scrape. I2 (`pricing.refresh_prices`) reads the docs pricing
tables (`opencode.ai/docs/zen/`, `/docs/go/`); the plans scripts read
elsewhere — Go from `opencode.ai/v2/docs/console/go` (per-plan tabs with
tiers + monthly allowances + assumptions), Zen from `opencode.ai/docs/zen/`
(same page as I2, different tables: Endpoints + pricing). So Zen is one
page/two parsers, Go is two pages/two parsers — whether the Go tables agree
is unverified and a cross-check candidate. `plans/*.json` also carry the
monthly allowances (`limits`, `requests_per_month`, Go `windows`). Shipping
asymmetry confirmed: the wheel ships `opencode_models/data/` (aliases +
plans snapshots, inside the package) but never `tier_prices/` (gitignored
runtime data) — so fresh keyless installs rank off shipped plans snapshots
and tier_prices-dependent behavior (variants, free marking, display names)
degrades until first fetch. Phase 2 unification candidate stands (one
pricing snapshot per tier, single schema, `est` derived not joined), with
the shippable location as the natural home.

Docs-source map (2026-10-23, live-page probe): no v2 Zen pricing page
exists (`/v2/docs/console/zen` and `/v2/docs/zen` both 404) — Zen's only
source is `docs/zen/`, which is current (GPT-6/Claude-5-era tables). The
live `docs/go/` page carries rates + monthly limits (Go and Go Plus tabs)
+ estimated requests + token assumptions + endpoints + model list ALL ON
ONE PAGE — i.e. everything I2 and the Go script extract, in a single
fetch. Unverified whether `v2/docs/console/go` serves the same content
under a second route (same PlanTabs shape expected); if so, Go collapses
to one URL too. Bonus signals on these pages Phase 2 should adopt:
`docs/zen/` has an Endpoints table (model → model-ID mapping, feeds roster
identity) and a Deprecated-models table with dates (feeds Storage
lifecycle: never-delete + deprecated marking instead of silent drops).

Cross-plan reconciliation (2026-10-23): (1) DECIDED lazy-sync-only —
`model-go-allowance` step 4's Action-cron refresh is dropped; the
`plan-snapshot-automation` idea must be rewritten against the unified
snapshot schema + sync flow (page_sha256/delta-report gaps carry over as
sync-report requirements). (2) `robustness.md` items are absorbed
WITH MODIFICATIONS: ingestion failure-as-warning becomes the ingestion
contract, but its `tier_prices/`-mtime staleness rule dies with that path
(superseded by per-source `meta` timestamps + thresholds), and its
auto-refresh out-of-scope is reversed by the lazy-sync decision.
(3) No conflict with `inference-availability` (done; personal-key +
gateway-lists story stands — also explains this environment's go=0 probe
readout as a key-class artifact) or `zen-snapshot-retire-txt` (done;
snapshot-derived rosters, no hand rosters — adopted by the Storage
design).

## Progress — Phase 2 fan-out launched (2026-10-23, all beam-free, background)

Five parallel workers, Phase 1 decisions as constraints, read-only (no
doc/code/plan edits, no commits, no live fetches): ingestion
(ses_e96845314ffejcIfVu4a9XdFkw), storage (ses_e96845302ffe2iXK31vGZ6mbdT),
domain (ses_e968452feffeoD11a9ZuNaciM), application+presentation
(ses_e968452faffekxJOfqxr7Xa3xX), sync lifecycle (ses_e968452e8ffecla0OgYIVowA8m).
Next on completion: parent consolidation (resolve contradictions, record
bend-vs-change) into `docs/architecture.md`.

Received 2026-10-23: application+presentation report
(ses_e968452faffekxJOfqxr7Xa3xX) — per-command contracts, service
assignments, warnings-as-data inventory, renderer mapping; verdict all
component-CHANGES, no constraint overturned. Open component question (not
a bend): auth verification endpoint for service keys (config API 403s;
inference list accepts). Awaiting: ingestion, storage, domain, sync.

Received 2026-10-23: ingestion report (ses_e96845314ffejcIfVu4a9XdFkw) —
eight items, seven component-CHANGES, one BEND (arena accepts
staleness-by-fetch-cadence, no TTL in read paths; only `meta.fetched_at`
added). Notable: `fetch.py` must drop its `availability` import (layer
violation); Go script should switch from v2 route to `docs/go/` single
page; `fetch_boards` must distinguish broken-source from empty-source;
failure contract formalized as (OSError, ValueError, JSONDecodeError,
KeyError, TypeError) → warn + exit 0, no prints in ingestion. No
constraint overturned. Awaiting: storage, domain, sync.

Received 2026-10-23: domain report (ses_e968452feffeoD11a9ZuNaciM) —
eight items, all component-CHANGES, no bends, no overturns. Notable:
`merge_aliases` placed FIRST in Application composition, before curation
(overlays apply to canonical models once); `by_score_per_price` kept and
adopted for `--benchmark` (replacing the ad hoc sort), `_take` and
`pareto.frontier` deleted; `validate_estimates`→ingestion gate and
`merge_snapshots`→Storage helper (scripts import package); `est` derived
per entry via `cost_per_request`; full result-contract field lists
(Ranked/Scores/Models/Conflicts/UsageViews + AuxViews signatures).
Awaiting: storage, sync.

Received 2026-10-23: storage report (ses_e96845302ffe2iXK31vGZ6mbdT) —
seven items, all component-CHANGES, no overturns, one clarification: the
fallback chain is live → RUNTIME unified → shipped base (runtime layer
stays between, per existing `_plan_models`); unknown-vs-empty kept for
the live layer only. Notable: `tier_prices/` migration = ignore (+
optional present-but-unread warning), with a code-reference repoint list;
boards/aiarena caches need `meta` wrappers (`schema_rev` everywhere) for
lazy sync; `data/measured/` = sync-overwrite, latest-only, seed-excluded;
deprecated-table adoption workable with one open flag-vs-sidecar question
for the doc. Awaiting: sync.

Received 2026-10-23: sync report (ses_e968452e8ffecla0OgYIVowA8m) —
trigger/thresholds (T1 warn-7d explicit-fetch, T2 lazy 4h availability /
8h pricing, T3 no age check), `meta` delta (`schema_rev` + `page_sha256`,
bare-payload wrapping with migration bend), structured sync-report shape,
keyless matrix (incl. `models` strictness as intentional + fetch-sitemap
fix), T3 CONFIRMED, cache seam placed post-Storage records + post-Domain
scales (partial caching unsafe), definitive data-kind matrix.
`free_tier_nominals.json` resolved: file exists in gitignored `data/`
with meta but no writer/reader in tree — orphan data, excluded from the
doc. Flag for the doc: lazy auto-refresh of human-gated plans data must
preserve the validation gate (Go 10% tolerance) in automated form.
Phase 2 complete — parent consolidation into `docs/architecture.md` next.

## Progress — `docs/architecture.md` written (2026-10-23, parent consolidation)

All five Phase 2 reports received with no constraint overturned (one
ingestion bend: arena staleness-by-cadence; one storage clarification:
live → runtime → shipped chain; one open flag-vs-sidecar question;
auth-verify endpoint left as component question; scripts-vs-package
mechanism left to implementation). Consolidated into
`docs/architecture.md` (10 sections: starting point, layering,
ingestion, storage, domain, application+presentation, pricing
consolidation + data-kind table, sync design, growth considerations,
non-goals). Restructured 2026-10-23: the doc is now the future-state
spec only; everything that needs refactoring lives below as the work
inventory. Status stays in-progress pending maintainer review;
closing the task (done) is the maintainer's call after review.

## Refactor inventory (the work to arrive at the spec)

Destinations are the architecture's layers/seams. Ordering, effort, and
execution belong to follow-up implementation plans.

Ingestion: drop `fetch.py`'s `availability` import + `only` filter
(caller passes allow-list); retire `PriceEntry` join format and the
`tier_prices/` write (parser logic feeds unified snapshot); Go parser
switches v2 route → `docs/go/`; `fetch_boards` distinguishes
broken-source from empty-source (parse-`None` surfaces as failure);
arena cache gains `meta.fetched_at` (no TTL); live read-through leaves
`list`/`next`/`models` for the availability-sync step; scripts thin to
wrappers over package-owned parsers/merge/gates; sitemap-unreachable
traceback closed by the failure contract; no `print` in ingestion.

Storage: `_snapshot_ids` → unified model keys; `free_slugs` reads the
unified snapshot; roster chain live → runtime → shipped; `load_curated`
splits loading from merging; boards/arena caches gain `meta` wrappers
(`schema_rev` everywhere) with legacy-tolerant readers; new age query;
`seed` drops `tier_prices` (incl. branch loop), splits deploy vs
runtime-fallback vs branch reads; new `data/measured/` loader (sync
overwrite, seed-excluded); ignore-migration for on-disk `tier_prices/`
(+ optional warning); repoint list: `_snapshot_ids`, `free_slugs`,
`_docs_known_slugs`, roster synthesis, est-rate loader, seed subdirs,
affected tests.

Domain: `build_rows` loses `est_rates` join (derive per entry);
`--benchmark` adopts `by_score_per_price`; delete `_take`,
`pareto.frontier` (+test); wire `merge_aliases` first, before curation
(execution: v01-review R2 step 2); single `Curation` entry point;
`validate_estimates` → ingestion gate, `merge_snapshots` → Storage
helper; usage arithmetic extracts to pure functions (CSV parsing is
ingestion); display/endpoint/benchmark helpers → pure AuxViews of
loaded records (signatures change off `data_dir`).

Application: eight use cases with result objects; AvailabilityService,
CatalogService, shared row-pipeline service (never duplicate);
AvailabilityService.resolve owns live pull + save + fallback +
warnings; warnings-as-data everywhere (incl. newly surfaced board/usage
silences); `models` strictness kept intentionally; fetch use case
aggregates the structured sync/fetch report.

Presentation: every `cli.py` formatting helper → renderers + one
`Style` helper (`_style`, `_headings`, `_rule`, `_section`, `_cells`,
`_widths`, `_print_row`, `_next_table`, conflict/usage/fetch printers,
markers, tags); `_fail` stays at CLI entry; renderers pure
(result + tty flag → string) for golden tests.

Decided deletions: `tier_prices/` path + format, `_take`,
`pareto.frontier` (+test), ad hoc `--benchmark` sort, inline live
fetches in read commands, per-command availability/naming re-derivation,
sitemap-traceback behavior. Left to implementation: ordering, effort,
scripts import-vs-subprocess mechanism, deprecated flag-vs-sidecar,
auth-verify endpoint.

DECIDED (2026-10-23) — pricing consolidation: `tier_prices/` and `plans/`
merge into ONE pricing snapshot per tier (single schema: rates + variants
+ limits + allowances + assumptions + request estimates; `est` derived,
not joined). A base version ships in the wheel (replacing today's
`opencode_models/data/plans/` fallback as the shippable home); `sync`
refreshes it (per-source age checks, lazy on each run). `fetch` may invoke
a sync internally (plus cache update and other refresh steps), but under
the hood the legacy `tier_prices/` path — scrape format, `PriceEntry`
join in `build_rows`, roster fallback via `_snapshot_ids` — is fully
removed, not kept as a parallel fallback. The data-type inventory (rate
card vs allowance vs assumption vs measured vs availability status) is
consolidated with it: each kind has one home, one writer, one freshness
rule. Execution belongs to a later implementation plan; conflicts and
migrations (existing checkouts with `tier_prices/` on disk) are noted for
it.

Performance note (design consideration, not Phase 1/2 scope): startup
parses every per-model source JSON on each run; as the catalog grows this
dominates launch latency. The design should leave room for a derived cache
(parsed/normalized records with invalidation on source mtime + schema rev),
but no caching work is part of this task — record the seam, not the
implementation.
