---
status: open
type: feature
priority: high
area: cli
---

## Notes

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

- [ ] 1. Functional core + frozen-fixture tests (RED first): `cpr`,
  caps, remaining, ranking, reproduction check, expand-merge.
- [ ] 2. Snapshot schema + seed from the current page tables (Go + Plus,
  with tier variants), stored where the maintainer decides below.
- [ ] 3. `usage` command (RED first): pollers, local % history,
  per-model table with footnote flags, invariant checks.
- [ ] 4. Refresh flow (RED first where pure): scrape/parse, consistency
  gate, expand-not-replace publish to the release asset; staleness
  display in views.
- [ ] 5. Plan identity: stored preference + `--plan` + fit hint.
- [ ] 6. Free-tier diagnostic footnotes + error-signature collection.
- [ ] 7. Verification: full suite, ruff/zizmor, live matrix (keyed
  `usage` vs export vs page math), docs (README/LIMITS/CHANGELOG +
  `docs/model-go-plan.md` status flip per settled assumption).

## Open decisions (interview before building)

- Snapshot home: `data/`-local cache only vs repo-frozen fallback copy
  for offline installs (recommended: both — frozen fallback ships,
  refresh overwrites local).
- Refresh trigger: CI-scheduled scrape + PR (recommended) vs
  maintainer-run script vs staleness check inside `fetch`.
- `usage` scope: read-only overview + flags first (recommended) vs
  advice text ("switch to X") from day one.
- Default plan: assume Go until declared (recommended) vs force explicit
  choice on first `usage` run.

## Verification

- New tests RED before each implementation step; full suite green after.
- Estimate-reproduction check runs in CI and in the publish gate.
- Live: keyed `usage` output reconciles with export math to stated
  tolerances; every mismatch carries a footnote, none is silent.
- No `list`/`next`/`models` path gains a network call (grep the
  inference/usage URLs to the same call sites).
