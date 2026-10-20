---
status: done
type: bug
priority: high
area: cli
---

## Notes

Standard-tier critical review of `opencode_rank/` (2026-10-20): one discoverer
(`opencode/mimo-v2.6-flash-free`, next #3 free pick), one challenger on a
different provider and family (`opencode-go/beam-free`), parent consolidation
with spot-checks against source and live `data/`. Raw transcript (Phase 1
claims verbatim + Phase 2 verdicts) lives at `/tmp/opencode/review-findings.md`
— ephemeral; this file is the durable record.

## Outcome

All FIX NOW items implemented, reviewed, and green on PR #4 (merged as noted
below unless stated otherwise):
R1 (keyed fetch crash), R3 (source-all availability leak), R2 step 1
(canonical dedupe in `next`), R4+R7 (arena provenance + corrupt-cache guard),
R8 (snapshot loader guards), R6 docs (stale `--refresh` references removed).
Deferred follow-up: R2 step 2 (full `merge_aliases` wiring across load path,
pricing entries, and availability) — needs its own task when scheduled.

## Findings, ordered for fixing

### FIX NOW

- **R1 (S0). Keyed `fetch` crashes on `PriceEntry` subscript.** `free_slugs`
  comprehension in `cli.py:441-453` indexes `e["model_id"]` on dataclass
  instances; the `except (OSError, ValueError)` doesn't catch `TypeError`,
  so every keyed fetch with a free row dies before usage export and boards.
  Fix: attribute access (`e.model_id`, skipping `None`), plus a CLI test
  with a free entry in the tier snapshot.
- **R3 (S2). `--source all` pool bypasses the availability filter.**
  `cli.py:770-782`: the `offered()` filter runs only in the tier branch;
  dropped-but-listed models fall into the `-` pool mislabeled and
  mispriced (observed: Ling 3.0 Flash Fin, Hy3 preview). Fix: apply the
  same filter to the `-` pool (it should hold only genuinely neither-tier
  models). Test with a dropped model under `--source all`.
- **R2 (S2, challenger REJECT overturned — see below). Alias twins take two
  `next` slots and two scores.** `meta/muse-spark-1.3{,-contributor}.json`
  carry identical benchmark values, so per `identity.py`'s own alias map
  they are one model — yet `list` scores them apart and `next` spends two
  slots, against README "one model, one slot" and the module's present-tense
  contract. Fix in two steps for risk: (1) dedupe `_take` picks by
  `canonical_id`; (2) wire `merge_aliases` through load (availability +
  pricing + catalog, per the module's own three-place warning).
- **R5 (S2, latent). Losing override relabels the kept value's source.**
  `overrides.py:171` writes `sources[key]` before the `override_wins`
  check at `:183`; the wrong tier then feeds scale ends and board
  comparisons. Fix: move the assignment under the wins branch. Test with
  a losing override asserting `sources` unchanged.
- **R4 (S2, latent). Arena merge drops the provenance map.**
  `merge_arena_score` (`arena.py:85-97`) rebuilds `Model` without `sources`,
  so a board row can later displace an independent run it must never touch.
  Fix: pass `sources` through. Test with a synthetic arena match.
- **R7 (S2). Arena cache load crashes every view on corrupt JSON.**
  `ArenaCache.load` (`arena.py:32-35`) is the one view-critical loader
  without the try/except all five siblings have. Fix: same guard. Test
  with a truncated `scores.json`.
- **R8 (S2, challenger-added). Tier snapshot loader has the same gap.**
  `_snapshot_ids` (`availability.py:29-39`) catches only `JSONDecodeError`;
  non-dict rows and unreadable files crash every view via `_current_offered`.
  Fix: same guard pattern. Test with a non-dict tier_prices row.

### Docs item (no code fix)

- **R6 (CONVENTION-GAP). Stale `--refresh` references.** The code is right
  (fetch-only network); the `scores` help text (`cli.py:1119`) and the
  `SECURITY.md:14` row advertise a flag that argparse rejects. Fix: delete
  both mentions.

### Struck (kept visible)

- *No struck items.* The challenger's single REJECT (R2, "pending feature,
  not a defect") was overturned on live-data evidence: the alias map's own
  precondition (identical benchmark values) holds in `data/`, so the
  double recommendation is a behavior bug under the repo's stated rule, not
  a feature request. Rationale recorded here so the debate is not
  re-litigated.

