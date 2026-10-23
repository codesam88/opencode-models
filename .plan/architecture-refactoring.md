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
production callers; `merge_aliases` is tested but unwired (execution
stays with v01-review R2 step 2, placed first before curation);
`data/usage/free_tier_nominals.json` is orphan data (file exists, no
reader/writer) and excluded from the spec. Bonus page signals adopted:
Zen endpoints table (roster identity) and deprecated-models table
(lifecycle: never-delete + mark, not silent drops).

## Open questions (alternatives in docs/architecture.md §9)

- Deprecated-model marking: `Model` field vs sidecar slug set.
- `auth` verification for service keys: config API (403s) vs inference
  list (accepts).
- Pricing-script mechanism: shared core imported by scripts vs scripts
  kept standalone with sync invoking them.
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
- `merge_aliases` wires first, before curation (execution stays with
  v01-review R2 step 2 — the highest-behavior-risk item; isolate it
  from all other ranking changes).
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

Cross-cutting per step: justfile recipes (`plans-update/check/freeze`
→ sync flow) and README data-layer/"How it works" sections move in the
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

Step 1 — basic cleanups (independently committable, test-covered, no
structural rework prejudged; each verified with `just test` + `just
lint`, committed in small steps, workers never commit):
1. Delete `ranking._take` (dead; `_take_banded` is live).
2. Delete `pareto.frontier` + `tests/test_pareto.py`; reword the stale
   argparse description string.
3. Sitemap-unreachable `fetch` failure degrades to warning + keeps
   existing records (match existing per-source pattern).
4. `fetch_boards` reports per-source failures as warnings; parse-`None`
   surfaces as failure, never as empty axis.
5. Silent usage fallbacks (window API / export / balance) surface as
   warnings instead of empty sections.
6. Arena cache save adds `meta.fetched_at` (load stays tolerant).
7. `--benchmark` adopts tested `by_score_per_price` (confirm parity via
   test run).
8. `meta` wrappers for boards/arena caches with legacy-tolerant readers.

Step 2+ — structural rework per the refactor inventory above
(use-case extraction, renderer split, unified snapshot +
`tier_prices/` removal, sync flow, `seed` split, scripts thinning).
Sequencing, ownership, and the three open mechanisms to be planned
when Step 1 lands.

## Prioritization

Guiding risk rule: `list` / `next` / `scores` outputs are the product.
Any change that cannot keep them byte-identical (or explicitly intends
to change them, like alias wiring) is guilty until proven innocent.
Structure for its own sake is churn; structure that fixes a live defect
or unblocks the next fix is value.

High value — prioritize (each fixes a defect, a silent corruption, or
an availability gap):
- Sitemap/unreachable-source warn-and-continue (Step 1 items 3–5):
  fetch/commands must not traceback or go silently empty. Direct
  product availability.
- `fetch_boards` broken-vs-empty distinction: a dead board masquerading
  as an empty axis corrupts scores silently. Same class as above.
- Unified pricing snapshot + `tier_prices/` removal: the dual-file
  slug-join with silent fallback is a live correctness risk, and the
  shipping asymmetry measurably degrades fresh installs today. Biggest
  item here, but it removes a defect class rather than moving code.
- `merge_aliases` wiring (via R2 step 2): double recommendation slots
  for one model is a confirmed behavior bug. Highest behavior risk —
  isolated from all other ranking changes.
- `meta` envelopes + age query + lazy sync: turns "may be stale" from a
  guess into an answer. Enables everything freshness-related after it.
- Derive-then-compare for `est`: proves the default ranking doesn't
  shift under consolidation; keeps the highest-traffic output honest.

Simple/trivial — legitimate cleanup and maintenance, near-zero risk:
- Delete `_take`, `pareto.frontier` (+test), reword one argparse string.
- Arena `meta.fetched_at`; `by_score_per_price` adoption (parity-checked).
- Board/usage warning surfacing (additive diagnostics).
- Boards/arena `meta` wrappers with tolerant readers.

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

- `docs/architecture.md` reviewed and accepted by maintainer (spec
 -only: no migration content, no review provenance).
- Refactor inventory checkable against the tree (every symbol named).
- Step 1 lands green (`just test`, `just lint`) in small commits.
- `plan lint` passes for this file.

## Status

Spec written and restructured; Step 1 not started. This task closes
when the maintainer accepts the spec; Step 1 executes under it (or a
follow-up plan if preferred).
