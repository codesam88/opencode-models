---
status: in-progress
type: feature
priority: high
area: cli
---

## Notes

Status 2026-10-24: plan opened; interview settled (move + purify per
command, argparse stays in `cli.py`, tracked here, `sync` first).
Overrides the architecture plan's "never as a standalone rewrite"
verdict for this one task — recorded here, not re-litigated there:
the mitigation is golden-output tests locked before each move,
one command per commit, byte-identical output gates, workers never
commit.

## Problem

`cli.py` (1,668 lines) holds all nine subcommands plus ~40 helpers
across three jobs: argument parsing, business decisions, and
formatting/`print`. No command is unit-testable, every edit risks
layout regressions, and the per-command renderer work the
architecture wants has no seam to attach to.

## Strategy

Per-command modules under `opencode_models/commands/`; `cli.py`
shrinks to top-level parser + dispatch + `_fail`. Each command moves
with its logic purified from its rendering in the same step (move +
purify, per 2026-10-24 interview): business decisions return plain
result objects, rendering is pure functions of those results,
`print` happens once at the command edge. Argparse stays in `cli.py`
(one place for the CLI surface; modules expose `run(args)`).
Shared table/style primitives move once, with the first command
that needs them, into `commands/render.py`.

Rejected: full Application/Presentation split now (unreviewable
big-bang diff); shared-render-first (designs the abstraction before
its consumers move).

Standing rules: golden-output test locked before each move;
byte-identical stdout + exit code after (no behavior change planned);
`just test` + `just lint` green every step; one command per commit;
workers never commit.

## Steps

- [ ] 0. Safety net: audit per-command golden coverage in
  `tests/test_cli.py` (`_run` on synthetic data dirs); add missing
  golden tests (stdout + exit code) for every subcommand. Done when
  each of the nine commands has a locked golden test.
- [ ] 1. `sync` → `commands/sync.py` (thinnest; delegates to
  `sync_mod` already). Golden test first, then move.
- [ ] 2. `auth` → `commands/auth.py`. Purify: separate key
  verification (service) from prompt/report.
- [ ] 3. `overrides` → `commands/overrides.py`. Creates
  `commands/render.py` with the shared conflict-table rendering
  (`_print_conflicts` pure core; `fetch` reuses it in step 6).
- [ ] 4. `models` → `commands/models.py` (display-name helpers move
  with it).
- [ ] 5. `scores` → `commands/scores.py` (`_score_columns`,
  `_score_cells` → render).
- [ ] 6. `fetch` → `commands/fetch.py` (largest I/O surface;
  progress/report helpers move with it).
- [ ] 7. `usage` → `commands/usage.py` (polling/history pure cores
  vs window tables → render).
- [ ] 8. `list` + `next` → `commands/list.py` + `commands/next.py`
  together (shared row pipeline via `rows_for`, shared
  `_cells`/`_widths`/`_select` → render; `RowsResult` already the
  shared result object).
- [ ] 9. Close: `cli.py` holds parser + dispatch + `_fail` only;
  update architecture plan Further-steps (renderer item done),
  `docs/architecture.md` only if a layer name changed.

## Decisions (settled 2026-10-24)

- Move + purify per command (not mechanical-only).
- Argparse stays in `cli.py`.
- Tracked here, not folded into architecture-refactoring.
- `sync` first.

## Progress (newest first)

- 2026-10-24, step 1 done: `sync` → `commands/sync.py` (`run(args,
  get=None)`, transport injected by `cli.py` dispatch so the
  `cli.http_get` patch points keep working). Golden `sync --force` /
  `--check` CLI tests passed unchanged; full suite 608 green, lint
  clean.

- 2026-10-24, step 2 done: `auth` → `commands/auth.py` with purified
  `verify_key(key, get=None)` (decision as `VerifyResult`, no
  printing) covered by `tests/test_auth_command.py`; `run` keeps
  prompt/save/report. Shared `fail()` moved to `commands/__init__`,
  `cli._fail` now re-exports it. Patch targets moved
  (`cli.fetch_served/live` → `commands.auth.*`); golden auth CLI
  tests passed unchanged. Full suite 611 green, lint clean.

- 2026-10-24, step 3 done: `overrides` → `commands/overrides.py`
  (decision + report printing at the edge; conflict-table pure core
  in new `commands/render.py`: `conflict_rows`/`format_conflicts`,
  plus `print_conflicts` shim for fetch until step 6 and `bold_head`
  moved with its `_style` dependency). `cli` keeps `CONFLICT_HEADERS`
  and `_print_conflicts` as re-exports for existing tests/fetch.
  Two over-eager block cuts during the move deleted neighboring
  helpers — caught by diff review, reverted, redone surgically; the
  final diff is exactly the moved code (+4 import lines). Golden
  `overrides` CLI tests passed unchanged. Full suite 615 green
  (incl. new `tests/test_render.py`), lint clean.

- 2026-10-24, step 4 done: `models` → `commands/models.py`
  (delegated to worker; parent-reviewed). Decision functions
  `_roster`/`_rows` vs formatting `_summary`/`_table`, single print;
  helpers moved verbatim; `cli` keeps header re-exports for tests.
  Three golden tests added first (failure path, no-token notice
  suppression, header/warning pass-through), then move. Full suite
  618 green, lint clean.

- 2026-10-24, step 5 done: `scores` → `commands/scores.py`.
  Decision functions (`_columns`, `_select`, `_ranked`, `_counts`,
  `_grid`) vs pure rendering (`_cells`, `_render_table`,
  `_footnotes`), single print at the edge; `_column_label` moved to
  `commands/render.py` (test re-exports kept in `cli`). Unit tests
  (`tests/test_scores_command.py`: column coverage, blank-not-zero)
  failed first on the missing module, then on a real scoring rule
  (single-carrier benchmarks have no spread, so fixtures gained a
  third model) — both genuine REDs. Ruff removed 11 newly-dead
  cli imports (scores was the last user of `load_catalog`,
  `derived_scores`, `combined`, `scales`, `grade_for` in `cli`).
  Golden `scores` tests passed unchanged. Full suite 620 green,
  lint clean.

- 2026-10-24, step 6 done: `fetch` → `commands/fetch.py`
  (verbatim move; progress prints intentionally stream — buffering a
  minutes-long fetch would hide stalls. Transport injectable as
  `run(args, get=None)`, `cli` passes `http_get`, so the existing
  `cli.http_get` patch points keep working: first attempt bound the
  transport at import and hung the suite on real network; fixed to
  the `sync`-step pattern, no test edits). `_model_tick` moved with
  it. Golden fetch CLI tests passed unchanged. Full suite 620
  green, lint clean.
