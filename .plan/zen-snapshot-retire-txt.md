---
status: done
type: feature
priority: high
area: backend
---

# Retire tier txt rosters; ship plans/zen.json; suffix-rule variants

## Notes

- 2026-10-23: user direction — Option B (delete txt), plans/zen.json mirrors
  go.json fallback/live workflow, `-free`/`-contributor` are provider plan-option
  suffixes (same weights/scores as base; free variants ephemeral; benchmarks usually
  base-only). Workers run on `opencode-go/beam-free`; parent critically reviews.
- Workers never commit; parent verifies diffs and commits on request.

## Goal

Tier membership and free flags derive from refreshed data (live `available/`,
`tier_prices/`, `plans/*.json` + shipped fallbacks). No hand-maintained roster.

## Steps

1. Suffix-rule canonicalization (`identity.py`): `-free`/`-contributor`
   repeat-strip; twin-guard kept; `MODEL_ALIASES` subsumed. (worker, Beam Free)
2. `scripts/update_zen_pricing.py` + `data/plans/zen.json` from the zen docs page
   (endpoints + pricing tables; limits/requests/assumptions null; envelope mirrors
   go.json). justfile `plans-update-zen`/`plans-check-zen`/`plans-freeze-zen`.
   (worker, Beam Free)
3. CLI cutover: roster chain live → tier_prices → plans → shipped; `_select`
   fixed tiers; one shared slug matcher; free from snapshot/price-zero; delete
   `sources/*.txt` (+`sources.py` if empty); wire `unlisted()` drift into fetch
   output or drop it deliberately. (worker, Beam Free)
4. Test + docs sweep: `load_source` mocks, agreement test, skill doc, README/LIMITS,
   packaging check for shipped zen.json. Full suite + lint green. (parent)
