---
status: in-progress
type: decision
priority: high
area: backend
---

## Notes

`opencode-models` grew ad hoc: output rendering is mixed with commands
(`cli.py` ~1900 lines owns parsing, business decisions, formatting, and
`print` per command), commands are mixed with function (row building,
roster synthesis, cost selection, grading next to argparse), and function
rests on several sources with ad hoc aggregation rules and no lifecycle
management (catalog, tier prices, plan snapshots, live gateway lists,
arena cache, boards, curated overlays — freshness/fallback/merge decided
per call site).

This task produced the target architecture as `docs/architecture.md`
(future-state spec only) via a two-phase review: Phase 1 (component
inventory, proposed layering, component→layer mapping) then Phase 2
(per-layer deep review deciding for each item whether the component
changes or the architecture bends). Five worker reviews (ingestion,
storage, domain, application+presentation, sync lifecycle) overturned no
Phase 1 constraint; the single bend is arena staleness-by-cadence.

Scope note: the task began as spec-only, then gained a Step 1 cleanup
sequence below. Step 2+ structural rework is inventoried but not
sequenced — that belongs to follow-up implementation plans.

## Decisions

Layering: five layers, one-way dependencies downward — Ingestion
(network reads, input-data writes, no prints) → Storage (owns the data
directory: layout, loaders, fallback chains, freshness answers, seeding,
retention; only layer knowing file paths) → Domain (pure: records in,
records out; every README rule maps to one synthetic-tested function) →
Application (one typed use case per command, result objects, no
formatting) → Presentation (renders result objects; owns columns,
markers, units, TTY).

Freshness tiers + lazy sync: T1 (catalog/benchmarks, weekly, explicit
`fetch`, warn-only) / T2 (pricing/availability, lazy sync on each run
when older than threshold — ~4h availability, ~8h pricing) / T3
(curated, versioned with code, no age check). No timer or daemon; the
scheduled-cron direction (`model-go-allowance` step 4) was considered
and dropped. Offline or failed sync warns and proceeds on last
snapshots, never a traceback.

Pricing consolidation: `tier_prices/` and `plans/` merge into one
pricing snapshot per tier (rates + variants + limits/allowances +
assumptions + estimates; `est` derived, not joined by slug). Reasons:
the live `docs/go/` page carries everything both layers extract in a
single fetch; Zen's only source is `docs/zen/` (no v2 Zen page exists);
and the wheel ships `opencode_models/data/` but never `tier_prices/`,
so fresh keyless installs already degrade until first fetch — the
shippable snapshot closes that gap by construction. Legacy path
deleted, not dual-run; on-disk leftovers ignored (+ optional warning).

Config API is availability-only: probed payload carries slugs plus
`disabled` and nothing else, and no other endpoint serves rate cards
(gateway lists are IDs, window API percentages, billing a balance,
export realized spend). Pricing stays scrape-sourced; availability
stays API-sourced. (Probe side finding: the `User-Agent` header is
required; bare urllib gets 403.)

Measured data stays separate: export-derived realized traffic goes in
its own runtime layer, never merged into plan snapshots — merging
"what the plan charges" with "how I use it" is the conflation README
Limitations rejects for ranking, and would leak workspace measurements
into a shipped artifact. Domain joins the two at view time; ranking
never sees measured data. The export doubles as a validation signal
(measured vs page-estimated cross-check), not a source.

Accepted staleness: arena cache has no TTL and no auto-refetch in read
paths (would break the offline-first contract); it gains
`meta.fetched_at` only. Cache seam reserved post-Storage records +
post-Domain scales (partial per-model caching unsafe: scales are
global); snapshot overwrites to be atomic under concurrent runs.

Fallback chain: live → runtime unified snapshot → shipped base, with
unknown-vs-empty distinguished for the live layer only. Roster itself
is never unknown (shipped base always present). `models` keeps its
stricter stance (no key + no snapshots = hard fail) as an intentional,
documented asymmetry.

Cross-plan: `robustness.md` absorbed with modifications (failure-as-warning
becomes the ingestion contract; its `tier_prices/`-mtime rule dies with
that path; its auto-refresh exclusion reversed by lazy sync).
`plan-snapshot-automation` must be rewritten against the unified schema
+ sync flow (page_sha256/delta gaps carry over as sync-report
requirements; owner TBD). No conflict with done plans
(`inference-availability` personal-key story stands;
`zen-snapshot-retire-txt` snapshot-derived rosters adopted).

Verified-and-discarded: `pareto.frontier` and `ranking._take` have no
production callers (deleted Step 1);
`data/usage/free_tier_nominals.json` is orphan data (file exists, no
reader/writer) and excluded from the spec. Bonus page signals adopted:
Zen endpoints table (roster identity) and deprecated-models table
(lifecycle: never-delete + mark, not silent drops).

## Open questions (alternatives in docs/architecture.md §9)

- Deprecated-model marking: `Model` field vs sidecar slug set.
- `auth` verification for service keys: config API (403s) vs inference
  list (accepts).
- Pricing-script mechanism: DECIDED 2026-10-23 for deletion —
  `scripts/update_*.py` retired, package `go/zen_pricing` own the logic
  behind the sync surface (`sync`, `sync --force`, `sync --check`;
  justfile `sync`, `plans-sync/check/freeze`). Follow-up DECIDED:
  module `main()`s deleted too. User-facing references realigned
  2026-10-24 (README data table + Caveats, both pricing-module
  docstrings, `allowance`/`availability` comments; pinned by
  `tests/test_docs.py` against the retired names).
- Alias-merge composition order: OPEN 2026-10-24 (critical review) —
  spec (`docs/architecture.md` §4, this file's Domain inventory) says
  merge runs first, before curation; the tree runs it last in
  `load_catalog` (after curated + arena + boards). No current-data
  effect (no designation-keyed overrides exist; the `overrides`
  off-band check is output-identical), so this is a decision, not a
  bug fix: either reorder with curation tests, or amend the spec to
  record the tree order. Settle before any curation work touches
  designation ids.
- `plan-snapshot-automation` rewrite owner.

## Refactor inventory (the work to arrive at the spec)

Each block states what actually changes, in which direction, and what
keeps each step safe. Stepwise rule throughout: golden outputs stay
byte-identical until the step that intentionally changes them; `just
test` + `just lint` green after every commit; docs and justfile recipes
move in the same change as the behavior they describe.

Ingestion — direction: callers own policy, ingestion owns transport and
writes, diagnostics return (never print).
- `fetch.refresh` loses the `availability` import and `only` filter;
  signature becomes crawl-sitemap-and-write-all. The sync/fetch caller
  builds the allow-list via roster + `offered` and passes it in.
  Consider: land the caller-side filtering first so no window exists
  where everything downloads.
- `PriceEntry` join format and the `tier_prices/` write retire; the
  HTML table machinery (variant expansion, free-row marking, tab
  dedup) is reused as the unified snapshot's rate/variant parser.
  Consider: build the unified writer alongside the old one and
  cross-check outputs per model before any reader switches.
- Go parser switches v2 route → `docs/go/`; verify the v2 route serves
  identical content first, else keep it as cross-check.
- `fetch_boards` returns per-source status; parse-`None` surfaces as
  failure, never as an empty axis. Consider: add the failing-source
  warning before changing any merge behavior, so currently silent
  outages become visible without altering scores.
- Arena cache save adds `meta.fetched_at`; load stays tolerant of the
  bare dict. No TTL, no read-path refetch.
- Live pull + `save_live` leave `list`/`next`/`models` for the
  availability-sync step; origin (`live` vs `snapshot`) becomes a
  result field. Consider: extract the sync step with identical
  fallback/warning behavior first, then repoint commands one by one.
- Scripts thin to wrappers: parsers, extend-not-replace merge, and the
  Go/Zen validation gates move into package-owned ingestion/storage
  functions (mechanism open: import vs subprocess). Consider: move the
  pure functions first with parity tests against script outputs on
  saved pages (`--from` fixtures) before touching the scripts.
- Sitemap-unreachable traceback closes via the failure contract
  (Step 1 item 3); every other entry point adopts the same
  warn-and-continue shape with broken-vs-empty distinguished.

Storage — direction: all path knowledge and fallback policy collect
here; loaders return records + diagnostics.
- Roster chain becomes live → runtime unified → shipped: `_snapshot_ids`
  collapses into unified model keys, `free_slugs` reads the same
  records. Consider: introduce the unified loader beside the old chain
  and assert identical rosters per tier before switching readers.
- `load_curated` splits into Storage reads + pure Domain merge; the
  merge gains synthetic tests. Consider: split without behavior change
  first (same inputs/outputs), then extend coverage.
- Boards/arena caches gain `meta` wrappers (`schema_rev` everywhere);
  readers accept legacy bare payloads during migration. The age query
  (`meta.fetched_at` vs per-source threshold) becomes the single
  freshness primitive — no command computes staleness itself.
- `seed` three-way split: install deploy (missing-only fetched,
  same-name-sync curated) vs runtime fallback (loaders → shipped base,
  never seed) vs branch reads (install-time only, skipping the deleted
  `tier_prices/` subtree). Consider: each split lands with a test on a
  synthetic data dir; the branch loop changes last.
- New `data/measured/` loader: tolerant, sync-overwrite, latest-only,
  seed-excluded. Lands with the sync flow, not before.
- Ignore-migration for on-disk `tier_prices/` (+ optional
  present-but-unread warning). Repoint list: `_snapshot_ids`,
  `free_slugs`, `_docs_known_slugs`, roster synthesis, est-rate
  loader, seed subdirs, `test_availability.py`, `test_seed.py`.

Domain — direction: pure functions of loaded records; README rules map
1:1 to tested functions; no `data_dir` in signatures.
- `build_rows` loses the `est_rates` slug-join; `est` derives per
  priced entry via `cost_per_request` on the unified record.
  Consider: derive-then-compare against the joined values on live
  snapshots before removing the join, so the default ranking cannot
  silently shift.
- `--benchmark` adopts `by_score_per_price` (Step 1 item 7 confirms
  parity first).
- Delete `_take`, `pareto.frontier` (+test) — Step 1 items 1–2.
- `merge_aliases` wired at the `load_catalog` choke point (v01-review
  R2 step 2, landed 2026-10-24 — the highest-behavior-risk item,
  isolated from all other ranking changes): one canonical `Model` with
  a unioned score feeds scales, membership, and both priced lines;
  conflicts warn instead of staying silent; designation-keyed plan
  records keep their display name and endpoint. Open: composition
  order vs the spec (see Open questions) — merge currently runs after
  curation/boards, not before.
- Single `Curation(upstream, curated, boards)` entry point;
  `validate_estimates` → ingestion gate, `merge_snapshots` → Storage
  helper. Consider: extract with byte-identical conflict reports
  before any tier-rule touch.
- Usage arithmetic extracts to pure functions of loaded records (CSV
  parsing stays ingestion; polling/history I/O stays
  Application/Storage). Display/endpoint/benchmark helpers become pure
  AuxViews with record inputs instead of `data_dir`.

Application — direction: thin orchestration returning result objects;
policy lives in named services, never inline.
- Eight use cases; AvailabilityService, CatalogService, one shared
  row-pipeline service for `list`/`next` (never duplicated — the
  agreement guarantee). Consider: carve services out of `_load`,
  `_rows_for`, `_current_offered` with behavior frozen by existing
  tests first; use-case shells follow.
- Warnings-as-data everywhere, including newly surfaced board/usage
  silences (Step 1 items 4–5 lead, so the fields exist before the
  services need them). `models` strictness kept intentionally.
- Fetch use case aggregates the structured sync/fetch report
  (per-source status, ages, origin, counts, deltas); `fetch` may
  invoke sync internally. Consider: report shape first with current
  sources, unified-snapshot sources join when ready.

Presentation — direction: all formatting out of `cli.py` into pure
renderers.
- Every helper → renderers + one `Style` helper; `_fail` stays at CLI
  entry. Renderers pure (result + tty flag → string). Consider:
  move one command's rendering at a time with golden tests locking
  current output first, so layout regressions surface immediately.
- Golden-output tests migrate from live-data fixtures to synthetic
  result objects as each renderer extracts; keep the old tests green
  until their renderer moves.

Decided deletions: `tier_prices/` path + format, `_take`,
`pareto.frontier` (+test), ad hoc `--benchmark` sort, inline live
fetches in read commands, per-command availability/naming re-derivation,
sitemap-traceback behavior.

Cross-cutting per step: justfile recipes (`sync`, `plans-sync/check/freeze`)
and README data-layer/"How it works" sections move in the
same change as the behavior; stdlib-only stays (no new dependency
without explicit maintainer decision); ruff complexity gate applies to
new modules (split further rather than raising it).

## Implementation

Standing guidance for all implementation steps: plan diffs commit
separately from code diffs; work proceeds smallest-risk-first
(deletions → diagnostics → additive → structural); every commit keeps
`just test` + `just lint` green; golden outputs (`list` / `next` /
`scores`) stay byte-identical until the step that intentionally changes
them. Gate before Step 2+: full suite green plus a before/after output
diff on live data — Step 2+ starts only when Step 1 is landed and
outputs are proven unchanged.

## Implementation (current state 2026-10-24)

Standing guidance for all implementation steps: plan diffs commit
separately from code diffs; work proceeds smallest-risk-first
(deletions → diagnostics → additive → structural); every commit keeps
`just test` + `just lint` green; golden outputs (`list` / `next` /
`scores`) stay byte-identical until the step that intentionally changes
them.

Landed, in order:
- Step 1 cleanups (2026-10-23, six commits): dead `_take` /
  `pareto.frontier` deletions; sitemap warn-and-keep; board
  per-source diagnostics; usage warning surfacing; cache `meta`
  envelopes; `by_score_per_price` adoption. Gate passed: `list`,
  `next`, `scores` byte-identical on live snapshot data.
- Step 2 service extraction (behavior-frozen): `load_curated` split
  (Storage reads + pure Domain merge), AvailabilityService, shared
  row pipeline for `list`/`next`, age primitive
  (`freshness.snapshot_age`, thresholds single-sourced).
- Unified snapshot migration (parent takeover after two rejected
  worker rounds — do not delegate safety-net work lightly): writer +
  shipped base, reader migration, fixture migration, dead-helper
  prune; byte-identical gate on six views. Notable review fixes:
  `Row.endpoint_id` derivation, free-row base name + tag,
  null-cached `cost_per_request` billing, `-` provider allowance
  counting. `est` now derives per priced entry from the unified
  record (the old `est_rates` join is gone).
- `sync` command + lazy trigger: `sync.py` (thresholds 4h/8h,
  keyless/offline warn + exit 0), age gate in `current_offered`,
  shared `refresh_plan` in rows + usage. The fetch-composes-sync path
  was dropped in favor of the lazy trigger.
- Alias wiring (v01-review R2 step 2, 2026-10-24): `merge_aliases`
  at the `load_catalog` choke point with conflict warnings, so
  `list`, `next`, and `scores` share one identity; designation-keyed
  plan records keep their display name and endpoint
  (e.g. Go `Muse Spark 1.3 Contributor` vs Zen `Muse Spark 1.3`,
  one shared score, one `next` slot).
- Docs realignment (2026-10-24): README data table + Caveats describe
  the sync flow; pricing-module docstrings point at `sync`
  (`--force`/`--check`) instead of the deleted scripts; pinned by
  `tests/test_docs.py`.

DEFERRED (still open, each blocked as noted): Go parser v2 route →
`docs/go/` switch (needs a live-fetch identity check,
offline-unverifiable). Declined as standalone projects — ride along
with real changes only: full renderer split, `seed` three-way split,
`measured/` writer, single Curation entry point, fetch caller-side
allow-list, deprecated marking (needs flag-vs-sidecar), unified
schema minors (schema_rev policy, page_sha256 scope, Go deprecated
parsing, notes split). Delegation lesson stands: safety-net work
(tests) stays with the parent; structural work goes to workers only
with test-backed proof.

## Unified pricing schema (decided shape)

One file per tier (`plans/go.json` incl. Go Plus via per-plan-keyed
`limits`/`requests_per_month`; `plans/zen.json` standalone, no windows).
Envelope: `meta{source, fetched_at, schema_rev, page_sha256, plans}` +
`windows` (Go only) + `models{slug: record}`. Per-model: `name`, `free`,
`tiers[]` (`label,in,out,cached,cached_write`), `limits`,
`requests_per_month`, `assumptions`, `dropped`, `first_seen` — all
current, all traced to readers — plus optional forward metadata:
`deprecated`, `deprecation_date`, `notes`, `catalog_model_id`,
`endpoint_model_id`, `endpoint`, `sdk_package`, `alias_keys` (display
context + cross-storage mapping keys). Non-goals held: no measured
data, no availability status, write-once by sync. Dead on arrival:
`PriceEntry.tier` (tests-only), per-variant `limit`, `variant_of()`,
the `tier_prices/` path. Verified: `cached_write` already in plans
tiers; Zen records carry null allowances; 1 dropped model ships in Go.

Decided 2026-10-23: (5) sync MAY read current data (catalog, aliases,
pages) and write the expanded snapshot back to `data/` — enrichment
within the snapshot's own domain; no-write-back covers foreign kinds
only; sync NEVER writes into the wheel (shipped base updates via
plans-freeze + commit). Lifecycle keeps `first_seen` beside
`deprecation_date` (both ends of a model's visible lifespan).
(7) keep `requests_per_month` (no rename churn).

Still open, minor: (1) schema_rev type/bump policy; (2) page_sha256
bytes-vs-text scope; (3) Go deprecated parsing or Zen-only; (4) Zen
nulls as bare null vs `{zen: null}`; (6) endpoint value form;
(8) `cached` nullability; (9) dropped models in shipped base;
(10) `notes` split.

## Prioritization

Guiding risk rule: `list` / `next` / `scores` outputs are the product.
Any change that cannot keep them byte-identical (or explicitly intends
to change them, like alias wiring) is guilty until proven innocent.
Structure for its own sake is churn; structure that fixes a live defect
or unblocks the next fix is value.

Landed (no longer scheduled — kept here as the record of what the
risk rule paid for):
- Sitemap/unreachable-source warn-and-continue, `fetch_boards`
  broken-vs-empty, board/usage warning surfacing, `meta` envelopes:
  fetch/commands never traceback or go silently empty.
- Unified pricing snapshot + `tier_prices/` removal (incl. `est`
  derivation from the unified record): removed the dual-file slug-join
  defect class and the fresh-install shipping gap.
- `merge_aliases` wiring (R2 step 2): one model, one score, one `next`
  slot; designation plan lines keep their own names/prices.
- `meta` age query + lazy sync (`sync`, `plans-sync/check/freeze`,
  thresholds 4h/8h): staleness is now an answer, not a guess.
- Step 1 deletions, `by_score_per_price` adoption, docs realignment
  for the sync flow (pinned by `tests/test_docs.py`).

Open, highest first:
- Alias-merge composition-order decision (see Open questions): one-line
  call, gates future curation work. Decide before any change touches
  designation-id handling.
- `plan-snapshot-automation` rewrite: blocked on owner assignment;
  next item producing user-visible output once unblocked.
- Unified-schema minors (schema_rev policy, page_sha256 scope, Go
  deprecated parsing, endpoint form, `cached` nullability, dropped in
  base, notes split): each needs a decision; cheap individually.

Churn — real rework with no product gain on its own; do NOT schedule
as projects. Do incrementally (extract when a command is touched for a
real reason) or not at all:
- Full eight-use-case extraction + renderer split as a standalone
  rewrite: ~1900-line diff with zero behavior change and maximum
  regression surface. The value (maintainability) accrues only when
  someone must modify a command — so extract per command, on demand.
- `seed` three-way split beyond dropping `tier_prices`: hygiene with
  no user-visible effect; fold into whatever change next touches seed.
- Single `Curation` entry point as its own task: same verdict — do it
  inside the next overrides/boards behavior change, not alone.
- Deprecated-model marking, `data/measured/` layer ahead of any usage
  work, auth-verify endpoint swap: nice-to-haves waiting for a
  triggering need.

## Acceptance

- `docs/architecture.md` is a working reference, not a gated spec
  (decided 2026-10-24; no migration content, no review provenance).
- Refactor inventory checkable against the tree (every symbol named).
- Every change lands green (`just test`, `just lint`) in small commits.
- `plan lint` passes for this file.

## Status

Current state 2026-10-24: the product pipeline is consolidated —
service extraction, unified snapshot, `sync` + lazy trigger, and alias
wiring are all landed and green (`just test` 601, `just lint` clean),
with golden outputs held byte-identical except where a fix
intentionally changed them. Remaining work is decisions plus
deferred churn (see Prioritization): composition-order call,
snapshot-automation owner, schema minors. This task stays open while
structural work lands incrementally; it closes when no sequenced
rework remains, not on spec acceptance.
