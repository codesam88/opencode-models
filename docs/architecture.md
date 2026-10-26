# Architecture — `opencode-models`

Target architecture for growing this 0.1 tool into a robust and reliable
one. This document specifies the system as it should be: layers and their
contracts, data homes, sync behavior, and the interfaces between
processing and presentation. It does not describe the migration path —
what still needs refactoring, repointing, or deleting lives in
`.plan/architecture-refactoring.md`.

Companion documents: `README.md` (user-facing semantics this design
preserves), `LIMITS.md` (plan economics), `data/overrides/README.md`
(curation rules).

## 1. Layering

One-way dependencies, downward only:

```
Ingestion  →  Storage  →  Domain  →  Application  →  Presentation
(sources)     (data dir)  (pure)     (use cases)      (text)
```

- **Ingestion** performs every network read and input-data disk write.
  No scoring, ranking, or display merging. Progress surfaces as a
  returned report, not inline prints.
- **Storage** owns the data directory: layout, typed loaders, fallback
  chains, freshness answers, seeding, retention. The only layer that
  knows file paths. Returns records plus load diagnostics, never paths.
- **Domain** is pure processing: records in, records out. No I/O, no
  clock, no `print`. Every rule in README "How it works" maps to exactly
  one function here, exercised on synthetic inputs with no `data/`
  dependency.
- **Application** exposes one typed use case per sub-command, returning
  a plain result object. Orchestration only — no formatting, no column
  widths.
- **Presentation** renders result objects to text. Owns columns, markers,
  units, TTY behavior — and nothing else. Exit codes stay 0/1 with the
  message on stdout. Builders are ANSI-free regardless of TTY; only the
  print site applies styling.

Cross-cutting: `auth` (key storage, env precedence) sits beside Storage
as configuration; the secret never enters the data directory.

## 2. Ingestion

One entry point per source, one snapshot format per layer, explicit
freshness and failure policy.

- **Catalog**: sitemap crawl writing every model page for an
  caller-supplied allow-list. Never-delete retention: dropped models
  keep records so scale ends don't move. Per-model skip-on-error.
- **Pricing**: docs-table parsing (variant expansion, free-row marking)
  feeding the unified per-tier snapshot. Primary sources: the v2 Go
  page (`https://opencode.ai/v2/docs/console/go`; rates, limits,
  estimates, assumptions, endpoints on one page) and `docs/zen/`.
- **Boards**: official board fetchers with label-to-catalog resolution
  (`labelmap` as source configuration). Browser-only or stale boards
  are an explicit documented exclusion, not a runtime failure.
- **Arena**: community-mirror lookup with a timestamped local cache,
  refreshed on `fetch` only (staleness-by-cadence; no auto-refetch in
  read paths, preserving the offline-first contract).
- **Live availability**: gateway served lists plus config known set,
  merged with served-ness as truth and snapshotted. The config API is
  an availability-only source (slugs plus status); no API endpoint
  serves rate cards.
- **Plan snapshots**: page parsers with extend-not-replace merge and
  validation gates (estimate reproduction within tolerance, structural
  checks) as part of the ingestion/storage contract.
- **Failure contract**: every ingestion entry point treats transport
  and parse failures (`OSError`, `ValueError`, `JSONDecodeError`,
  `KeyError`, `TypeError`) as expected and non-fatal, returning
  empty/partial data. The Application fetch use case maps each to a
  warning and exits 0. Broken sources are distinguishable from
  genuinely empty ones.

## 3. Storage

- **Roster resolution**: live `available/` → runtime unified snapshot →
  shipped base, reusing the runtime-then-shipped pattern. Unknown
  (never fetched) vs empty (offers nothing) is distinguished for the
  live layer; roster itself is never unknown because the shipped base
  is always present.
- **Loaders**: tolerant upstream catalog reads (skip bad files, never
  fail the view); strict curated reads; tolerant readers for legacy
  snapshot shapes.
- **Snapshot envelopes**: every snapshot carries `meta` with `source`,
  `fetched_at`, and `schema_rev`; scraped pages add `page_sha256` so
  silent rewrites that still parse are detectable. Storage answers
  "how old is this source?" against per-source thresholds.
- **Retention**: catalog never-delete (scale stability); usage history
  append-and-prune with a cap; snapshots overwrite. Dropped models keep
  records and carry a deprecated mark rather than disappearing
  silently.
- **Seeding**: install-time deploy only — missing-only for fetched
  layers, same-name sync for curation (re-install intentionally brings
  newer pins). Runtime fallback is the loaders' job (shipped base),
  not the seeder's. The `local-data` branch is a seed fallback for the
  verbatim catalog, never a runtime dependency.
- **Measured layer** (`data/measured/`): sync-overwrite, latest-only
  retention, seed-excluded (workspace-local, privacy-sensitive),
  absent meaning never-fetched rather than empty.

## 4. Domain

- **Scoring**: per-benchmark 0–100 scales over the whole catalog
  (best = 100, worst = 0, linear), ends set by checkable numbers with
  the small-minority guard, combined score as the median, unscored
  below two axes. Interface: `scales(models)` + `score_of(model,
  table)`. Single-benchmark ranking is a ranking concern, not a
  scoring one.
- **Ranking**: row building (one row per priced variant), provider
  merge and dedupe, score-per-dollar ordering with free lines first,
  grade bands, best/worst/costliest partition (cost claims first),
  and the `next_up` sequence (free↔paid and Zen↔Go alternation with
  exhaustion fallback, 75%-of-best eligibility, one model one slot).
  `est` $/req derives per priced entry from the unified record's
  tiers and assumptions.
- **Identity**: one canonical folding (plan-designation suffixes, alias
  map) applied at a single choke point during record normalization, so
  records, prices, and availability never disagree about which model is
  which. Alias merging runs first in composition, before curation, so
  overlays apply to canonical models once.
- **Curation**: one pure entry point `(upstream, curated, boards) ->
  (models, conflicts)` applying overlays then board numbers under the
  single source tier order (independent evaluator > official board >
  self-report; boards never displace independent runs). Upstream layers
  are never edited.
- **Pricing math**: the fixed-mix representative $/1M (for ranking) and
  the assumption-based $/req (for `est` and usage) are separate
  functions with a module boundary between them, never conflated.
- **Usage math**: window fractions and caps, per-model aggregation, and
  token-share rows as pure functions of loaded records.
- **Auxiliary views**: display names, availability markers, endpoint
  derivation, and the benchmark inventory as pure functions of
  already-loaded records.
- **Result contracts**: `RankedView` shared structurally by `list` and
  `next` (ordered graded rows, cull groups, picks, counts, notes);
  `ScoresView`, `ModelsView`, `ConflictsView`, `UsageView` for the
  remaining commands. Ranking never sees measured data; measured and
  plan records join at view time only.

## 5. Application + Presentation

- **One use case per command** (`list`, `next`, `scores`, `models`,
  `overrides`, `fetch`, `usage`, `auth`), each with typed inputs and a
  result object. Shared services: availability resolution (pull, save,
  fallback, warnings), catalog composition (loads + merges in order),
  and the `list`/`next` row pipeline (shared so recommendations can
  never disagree with the ranking). `usage` is Go-specific with a
  plan-parameterized result shape for future plans.
- **Warnings as data**: every warning and failure is a result field,
  rendered by Presentation — stale/unreachable/empty/unauthorized
  sources, missing data, no-token notices. `models` fails hard with no
  key and no snapshots (it is an availability view); recommendation
  commands degrade to snapshots instead.
- **`fetch` composes `sync`**: a single fetch use case delegates to
  per-source services and aggregates a structured report (per source:
  status, ages, origin, counts, deltas, warnings), which Presentation
  renders as progress + summary and a `--check`-style validation can
  consume.
- **Presentation** owns section rules, grade gutter, cost units,
  markers, endpoint/benchmark tags, and window tables via one shared
  styling helper. Renderers are pure functions of result objects, which
  is the seam for golden-output tests on synthetic fixtures.

## 6. Data homes

Each kind has one home, one writer, one freshness rule:

| Kind | Home | Writer | Freshness |
|---|---|---|---|
| Catalog record | `data/opencode/` | `fetch` (T1) | weekly, never-delete |
| Go/zen catalog (models.dev slice) | `data/modelsdev.json` | `sync` (T2) | ~24h, overwrite |
| Rate card + allowance + assumptions | unified `plans/<tier>.json` | `sync` (T2) | ~8h, overwrite |
| Availability status | `data/available/` | `sync`, key-gated (T2) | ~4h, overwrite |
| Curated overlay / manual record | `data/overrides/`, `data/manual/` | human (T3) | versioned with code |
| Board row | `data/boards/` | `fetch` (T1) | weekly, overwrite |
| Arena cache | `data/aiarena/` | `fetch` (T1) | staleness-by-cadence |
| Measured (export-derived) | `data/measured/` | `sync`, key-gated (T2) | overwrite, latest-only |
| Poll history | `data/usage/*.jsonl` | `usage` runs | append-and-prune |
| Auth key | XDG config (owner-only) | `auth` / env | n/a (secret) |

The models.dev slice carries `meta{source, fetched_at, schema_rev}` plus
`providers`, holding only the two opencode providers verbatim from
`https://models.dev/api.json`: `opencode-go` (the go tier) and `opencode`
(zen), each `{id, name, models}`. A record's fields the views read:
`name`, `cost{input, output, cache_read}` (USD per 1M tokens), `limit`,
`canonical_model_id` (the card the record maps to, used to join
benchmark scores), `family`, `status`. The slice is the tier catalog:
rows come from its records intersected with availability, so a served
slug it does not carry is not rendered — the gateway's internal ids
included, which is why no id filter exists. A missing or unreadable file
means "no slice", and the views fall back to the cards.

## 7. Sync

No timer or daemon: on each run the application checks snapshot age
against per-tier thresholds and syncs when stale (~4–8h for T2
price/model info, ~24h for the models.dev catalog slice, weekly warning
for T1). `fetch` remains the explicit
T1 refresh; `sync` runs user-invoked or lazily, and `fetch` may invoke
it internally. Offline or failed sync warns and proceeds on the last
snapshots — never a traceback. The automated validation gate
(estimate reproduction within tolerance, structural checks,
`page_sha256` comparison, per-model limit deltas) preserves in
automated form the review today's manual refresh requires; lazy
refresh never bypasses validation silently.

## 8. Growth points

- **Cache seam** (reserved, not built): between Storage and
  Application/Domain — normalized records plus derived scales.
  Invalidation covers source mtimes, snapshot `meta`
  (`fetched_at`/`page_sha256`/`schema_rev`), the tracked-benchmark
  set, and the package version. Partial per-model caching is unsafe
  (scales are global; any model-file change invalidates).
- **Atomicity**: snapshot overwrites are atomic so lazy sync can
  interleave with concurrent runs.
- **New plans/tiers** plug in as a snapshot section, a roster source,
  and a result-contract plan label.
- **New benchmarks** stay a deliberate act (tracked list + coverage
  test); new sources arrive as ingestion entries under the failure
  contract.

## 9. Open questions

Recorded alternatives, not silently settled:

- Deprecated-model marking: `Model` field vs sidecar slug set.
- `auth` verification for service keys: config API (403s) vs inference
  list (accepts).
- Pricing-script mechanism: shared core imported by scripts vs scripts
  kept standalone with sync invoking them.
