---
status: open
type: feature
priority: high
area: cli
---

## Notes

Status 2026-10-24: step 5 landed (`auth --plan go|go-plus` stores the
workspace preference; `usage --plan` overrides per run; `--go-pro`
removed; exceedance-only fit hint when the plan is unpinned — pure
`resolve_plan`/`suggest_plan` in `allowance.py`, pinned by
`tests/test_plan_identity.py`). Steps 1–3 in as before; step 7b done
with a superseding decision: instead of keeping curated `zen.txt`,
*both* tier lists were retired in favour of snapshot-derived rosters,
and a `plans/zen.json` snapshot (same envelope, no windows/caps — Zen is
credit-based) now exists alongside the Go one. Still open: step 4's
automation gaps (Action cron, `page_sha256`, per-model limit deltas —
see also the snapshot-automation idea, leaning manual),
step 6 free-tier diagnostics, step 7 verification (live matrix).

Integrate the Go allowance model (`docs/model-go-plan.md`, validator
`/tmp/opencode/go_allowance.py`) into the recommendation system, with
self-checks at every layer. Parent-drafted from the maintainer's four
inputs plus parent-proposed additions (marked P); nothing here is
building yet — decisions at the bottom go back to the maintainer first.

### Components

1. **Curated limits record + refresh flow.** A versioned snapshot of the
   page tables per plan (monthly $ limits, token prices with tier
   variants, token/req assumptions): checked on a cadence (weekly), NOT
   every invocation. The record lives online (e.g. a versioned asset on
   the GitHub releases page) so client apps pull curated data instead of
   parsing websites in the field. Publishing runs the go_allowance
   consistency check (recompute estimates from limits+prices+assumptions,
   tolerance-gated) and **expands rather than replaces**: new models are
   added, removed models are flagged never silently dropped, schema
   changes block publish and keep last-good.
2. **`usage` spot-validation command.** Polls `/zen/go/v1/usage` (3
   window %), the usage export (per-model $ spend in window) and
   billing/status (credit pool), appends timestamped % samples to local
   history for burn-rate math, and prints a per-model table: spend in
   window, implied % of model cap, remaining $/requests, plus overall
   API % vs computed. Mismatches get footnote flags, not silent
   corrections. Standing invariants checked each run: export cost ==
   tokens × page prices (the $10.91 check), model caps resolve for every
   served priced model, API % within tolerance of a stated mapping —
   with the mapping's unknown parts explicitly labeled, never fudged.
3. **Functional core.** A pure stdlib module (no I/O) owning `cpr`,
   req/window caps, remaining/headroom, credit ranking, the
   estimate-reproduction check and the expand-not-replace merge.
   `list`/`next`/`models`/`usage` call it for cost estimates; unit tests
   pin it with frozen fixtures (page snapshot + export excerpt →
   expected tables), so the math is verifiable without network.
4. **(P) Plan identity.** No endpoint exposes Go vs Plus and caps differ
   2–4× per model, so headroom math needs the plan as an input: stored
   local preference (default Go) + `--plan` override + a fit-based hint
   (which plan's limits best explain recent spend).
5. **(P) Freshness policy.** Every record carries source URL, fetched_at
   and content hash; views show snapshot age; page-shape changes fail the
   publish gate, never the client.
6. **(P) Free-tier diagnostics.** The H4 discriminator matrix as
   `usage`-table footnotes and collected error signatures (vendor 429 vs
   gate 429, per-plan route behavior), fed by failure-moment reports.
7. **(P) Hot-path budget.** No new network on `list`/`next`/`models`:
   allowance math reads the local snapshot + snapshots; only `usage`
   and the refresh flow touch the network.

### Non-goals (this task)

Zen-side allowance tables (core stays plan-agnostic so they plug in
later); changing the ranking itself to consume headroom (follows after
the core lands); anything needing new dependencies (stdlib-only holds —
page parsing is `html.parser`, with a maintainer-paste fallback).

## Steps

- [x] 1. Functional core + frozen-fixture tests: `opencode_models/allowance.py` (pure: cpr, caps, remaining, ranking, gate, merge) + 19 fixture tests. Done 2026-10-21; cross-checked vs script gate (26.4% kimi).
  caps, remaining, ranking, reproduction check, expand-merge.
- [x] 2. Snapshot schema + seed (shipped 2026-10-21: local `data/plans/go.json` + fallback `opencode_models/data/plans/go.json`, script + gate + recipes) from the current page tables (Go + Plus,
  with tier variants), stored where the maintainer decides below.
- [x] 3. `usage` command (read-only spot-check: pollers, history, per-model table, footnotes, invariants; `--go-pro` selects Plus). Done 2026-10-22; review caught a /1e5→/1e8 credit-scale slip in LIMITS (fixed).
- [ ] 4. Refresh flow (RED first where pure): scrape/parse, consistency
  gate, expand-not-replace publish to the release asset; staleness
  display in views. Manual workflow today: `just plans-update`
  (fetch+validate, exit 1 = gate failed, nothing written) → human
  confirmation (dropped real? added models have assumptions? limit deltas
  plausible?) → `just plans-freeze` (check local copy, then copy it over
  the shipped fallback; the pinned gate test guards the committed file in
  CI) → commit. Future Action: weekly cron runs plans-update;
  gate failure = red workflow, no PR; diff vs fallback = PR body; merge =
  promote. Gaps before automating: `page_sha256` in meta, per-model limit
  deltas in the report. (Split out 2026-10-23 into the idea-level plan
  `.plan/plan-snapshot-automation.md`, covering both the Go and Zen
  update scripts; the manual workflow above stays the interim answer.)
- [x] 5. Plan identity: stored preference (`auth --plan`, XDG
  `opencode-models/plan`) + `--plan go|go-plus` + exceedance-only fit
  hint (`suggest_plan`; silent unless spend beats a Go cap, silent
  when the plan was explicitly chosen). `--go-pro` removed.
- [ ] 6. Free-tier diagnostic footnotes + error-signature collection.
- [x] 7b. Consolidation (single Go pricing file): declared
  `plans/go.json` the authority (caps, tiered prices, assumptions,
  estimates); derive `tier_prices/go` variant rows from its tiers instead
  of scraping `/docs/go/`; derive Go pool membership as page-keys ∪
  served (retires hand-maintained `sources/go.txt`, keeps curated
  `zen.txt`). Verification condition: Go pool parity except +1 intended
  (`step-5-preview` appears; 6 grandfathered page-dropped models stay
  while served; 3 dead slugs vanish — they were never in the pool).
  Location: `opencode_models/data/plans/go.json` (`data.py` became
  `models.py` for the record types; `data/` is a pure shipped-data
  namespace; import sites repointed mechanically, no behavior change).
  Done 2026-10-23 with a superseding decision recorded above: both tier
  lists retired (not just go.txt), and plans/zen.json added.
- [ ] 7. Verification: full suite, ruff/zizmor (done, green in CI
  and locally), live matrix, docs. The three standing invariants live
  as tests-only tooling (pure `check_export_prices` /
  `check_caps_resolve` / `compare_window_percent` in `allowance.py`,
  pinned by `tests/test_allowance.py`) — deliberately not wired into
  live `usage` output, where a flag on noisy data would read as a bug
  report. Live matrix = the §7 runbook in `docs/model-go-plan.md`;
  H2/H4 flips wait on the burn experiments. CHANGELOG exists;
  LIMITS carries the spot-check; H1/H3 flipped.

## Open decisions (settled with maintainer 2026-10-21)

- Snapshot home: **runtime snapshot in local `data/`**
  (`data/plans/`, gitignored; committed copies ride the `local-data`
  branch). The wheel additionally ships a **frozen fallback**
  (`opencode_models/data/plans/go.json`, refreshed deliberately via
  `--out`, same validation gate) so installs work with no network.
  Resolution order (for the step-1 core): local copy first, fallback
  second. Refresh merges extend-not-replace into the local copy only.
- Refresh trigger: **conversion script now, online release + CI later.**
  An independent stdlib script (`scripts/`) converts the page tables to
  the committed JSON; the file is **extended on updates, never replaced**
  (new models added, dropped models flagged, schema changes fail shut).
  Curated release asset + CI scrape when needed.
- `usage` scope: **read-only first** — expose the model via the `usage`
  command (step 3): overview table + footnote flags + invariants.
- Default plan: **assume Go, `--go-pro` selects Plus caps** (step 5).

## Verification

- New tests RED before each implementation step; full suite green after.
- Estimate-reproduction check runs in CI and in the publish gate.
- Live: keyed `usage` output reconciles with export math to stated
  tolerances; every mismatch carries a footnote, none is silent.
- No `list`/`next`/`models` path gains a network call (grep the
  inference/usage URLs to the same call sites).
