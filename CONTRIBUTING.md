# Contributing

Small, focused changes with tests. The bar is: the full suite passes,
the lint gate passes, and a reviewer can see *why* from the diff alone.

## Workflow

1. Write the failing test first, watch it fail for the right reason, then
   implement the smallest change that turns it green (TDD — no production
   code without a failing test first).
2. Run the checks before committing:
   - `just test` — full unittest suite, must be green
   - `just lint` — ruff lint, format check, and the complexity gate
   - Fix with `uv run ruff check --fix ...` / `uv run ruff format ...`
     where the fix is mechanical; by hand where judgment is involved.
3. Keep commits small with short imperative summaries
   (`add X`, `fix Y`, not essays). Don't push unless asked.

## Rules that are load-bearing, not stylistic

- **Stdlib only.** `dependencies` in `pyproject.toml` stays empty: the
  installed tool must resolve nothing. Tooling lives in the locked `dev`
  dependency group (`uv add --dev ...`), never in the install path.
- **No secrets in the repo, ever.** Test keys are obvious dummies
  (`oc_sk_test`-shaped). Real keys live in the environment or
  `~/.config/opencode-models/api_key`, never in code, fixtures, or docs.
- **Fetched data is never committed.** Only hand-vouched curation goes in
  (`data/overrides/`); everything `fetch` downloads stays gitignored.
- **Console output is a contract.** Column changes go through
  `tests/test_views.py` first — if the view moves, that file says so on
  purpose, not by accident.

## Curating benchmark numbers

`data/overrides/` holds hand-pinned scores, one JSON per model, keyed by
benchmark name and version. Every number carries a `source` URL and,
where useful, a dated note on who ran the evaluation and any caveats;
unattributed scores do not belong here. Sources are tiered by
comparability (independent evaluator > official board > vendor
self-report), and a pinned value only applies when its source is at
least as good as upstream's. Full rules and the reasoning behind them
live in `data/overrides/README.md` — read it before adding a file.

## Reporting problems

- Bugs and questions: open a GitHub issue with steps to reproduce and
  the output of `opencode-models --help` (plus `just test` status if relevant).
- Sensitive matters (including data-takedown requests under the
  [data due-diligence note](README.md#data-due-diligence)): use a private
  security advisory, not a public issue. See SECURITY.md.

## Releasing

Releases are cut from `main` by tagging `vX.Y.Z`, which triggers the
`release` workflow (build via `uv build`, publish to PyPI via trusted
publishing — no stored credentials). One-time setup on PyPI first: register
the project name and add this repo as a trusted publisher. Checklist:

1. Curate `CHANGELOG.md`: move the `Unreleased` section to a versioned
   `## [X.Y.Z] - YYYY-MM-DD` section, written from `git log` (curated
   prose, not auto-generated).
2. Bump `version` in `pyproject.toml` (`uv lock` updates itself, and the
   `--version` flag plus every User-Agent follow automatically).
3. Commit, then tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
   The workflow verifies the tag matches `pyproject.toml` before building.
