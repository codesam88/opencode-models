---
status: in-progress
type: feature
priority: high
area: backend
---

## Notes

models.dev is the catalog authority for the go/zen views. Maintainer
decisions (2026-10-26):

1. **models.dev replaces the cards for go/zen rows.** Cards stay for the
   catalog-only `-` pool, the whole-catalog scales, and benchmark scores
   (models.dev carries none — scores are joined back to the card whose
   `model_id` equals the record's `canonical_model_id`).
2. **Provider-scoped:** go reads `opencode-go` (37 models), zen reads
   `opencode` (130). No cross-tier fallback, so zen prices never leak
   into a go row.
3. **models.dev is the authority:** a served slug with no catalog record
   is not rendered. Roster synthesis (`_synthesize_roster_models`) goes
   away, and with it the junk filter (`is_internal`) — neither `test` nor
   `*-backup` exists in either provider, nor in the cards.

Verified numbers (2026-10-26 pull): slice = 136,610 bytes / 167 models;
whole api.json = 6.4 MB / 232 providers / 8,929 models; no `test` or
`*-backup` id in either provider; every record has `name`, `cost`,
`limit`, and `canonical_model_id` joins to our card ids
(`minimax/MiniMax-M2.7`); prices are USD per 1M tokens, `cache_read` →
`price_cached_input`.

Costs accepted: `omen-alpha` (served, in no catalog) disappears from the
views; on go, `glm-5.1`, `grok-4.6`, `kimi-k2.6`, `qwen3.6-plus`,
`qwen3.7-max` drop out of go rows (they have cards but no
`opencode-go` record) — they stay visible wherever their record exists;
`minimax-m2.5` shows on zen only, with a real price instead of `[free]`.

**Snapshot-missing rule (offline / fresh clone):** no `data/modelsdev.json`
→ the tier falls back to the previous behavior, cards ∩ roster, minus
synthesis. Junk stays hidden there too: `test` and `claude-opus-5-5-backup`
have no card and no plan name. This is the same local→fallback layering the
plan snapshots use, and it is what keeps the fixture suite on one path.

## Steps

1. `modelsdev.py`: `API_URL`, `PROVIDERS`, `refresh()` (writes the
   `data/modelsdev.json` envelope with `meta.fetched_at`), `load()`,
   `tier_records()`, `to_models()` (prices/name/lab/score join).
   RED tests first in `tests/test_modelsdev.py`.
2. `freshness.py`: `MODELSDEV_MAX_AGE` + a `modelsdev` source path.
   `sync.py`: a per-source report line (`modelsdev: …`) and `--force`.
3. `rows.py`: tier pool = `members(modelsdev models or cards, roster)`;
   delete `_synthesize_roster_models` and the synthetic-row plumbing.
   `models.py`: names from the models.dev slice (fixes `iceman` → `-`);
   drop the `is_internal` branch. `availability.py`: delete `is_internal`.
4. Tests: new modelsdev/sync/rows coverage; roster-coverage fixtures gain
   models.dev entries; the junk tests become catalog-authority tests.
5. `.gitignore` gets `data/modelsdev.json`; README records the new
   catalog source; full suite + `just lint`.

## Progress

- 2026-10-26: landed `modelsdev.py` (fetch/slice/load/to_models), the
  `modelsdev` freshness layer (`MODELSDEV_MAX_AGE`, 24h) and the `sync`
  report line. Slice on disk after `sync`: 167 records / 187,890 bytes.
- Roster synthesis and the junk filter are gone from both views:
  `availability.is_internal` deleted (net-zero diff on `availability.py`),
  `_synthesize_roster_models` deleted. Rows come from
  `modelsdev.to_models(records, cards, aliases)` — the record id is the row
  key (what plan records, roster and availability all speak), scores and
  the curated marker join through `canonical_model_id`, bare slug, or a
  curated alias entry. Cards ∩ roster is the fallback when no slice exists.
- Real data after `sync`: `list --source go` 33 lines / 31-of-38 in
  catalog — "Iceman Contributor Free" renders by name, MiniMax-M2.7
  prices at 34¢ instead of free; `minimax-m2.5` is zen-only with a real
  price; `omen-alpha`, `test` and `*-backup` are gone from every view.
- `--all-models` on a tier means the whole slice now (go: 37 records), so
  the audit view also covers models the gateway does not serve today.
- Full suite 700 tests: 1 pre-existing error untouched by this work
  (`PriceModeTest.test_cost_line_names_the_ranking_basis`, prose-pinned
  against the old `EST_NOTE`, broken by commit `9174f7e`). `just lint`
  exit 0.
