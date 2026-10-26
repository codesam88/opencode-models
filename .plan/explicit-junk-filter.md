---
status: done
type: feature
priority: medium
area: backend
---

The filter above was superseded the same day: the go/zen views now take
their rows from the models.dev catalog slice, which carries neither
`test` nor `*-backup`, so both gates went away with the synthesis path
they guarded. See `.plan/modelsdev-catalog.md`.

## Notes

The filter that keeps internal endpoints out of `list` / `next` / `models`
was implicit and acted through two unrelated predicates:

- `rows.py:_docs_known_slugs` — a roster slug with no catalog record only
  renders when that tier's plan snapshot knows it ("junk filter").
- `commands/models.py:_catalog_known` — a slug renders only when a source
  can name it (catalog record or either tier's plan snapshot).

Both side effects were "hide `test` and `*-backup`", but they also hid every
served slug the docs had not caught up with: on go today
`iceman-contributor-free`, `omen-alpha`, `minimax-m2.5`, `minimax-m2.7`;
`omen-alpha` and `iceman-contributor-free` are invisible in `models` too.

Decision (2026-10-26, maintainer): make the filter explicit —
`availability.is_internal` covers `test` and `*-backup` only. Everything
else a tier serves is shown, documented or not.

Steps:

1. RED: unit tests for `is_internal`; roster-coverage test that a served
   slug with no docs record and no catalog record renders in `list` and
   `models`; re-scope `test_models_hides_live_only_rows_the_docs_do_not_name`
   to internal ids only.
2. GREEN: swap both gates for `is_internal`, drop `_docs_known_slugs` and
   `_catalog_known`.
3. Update the README line claiming unnamed rows are never shown.
4. Full suite + `just lint`; report the observed before/after on the real
   data dir (new go rows and how they are priced) before any commit.

Known consequence to decide later: a synthesized row has no plan record, so
it prices from the catalog record, which does not exist — it lands in the
free band. That affects `minimax-m2.5` / `minimax-m2.7` (paid on zen) in
the go view.
