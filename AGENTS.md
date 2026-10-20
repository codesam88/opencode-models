# Agent Guidelines

## Communication Style

- Stick to facts and established best practice. Do not editorialize or try to be persuasive.
- In assessments and commit messages, reflect uncertainty where it exists. Avoid asserting things as definitive when the evidence is incomplete.
- Skip superlatives and filler ("Clean.", "Perfect.", "This is the right call."). State what happened or what is true and move on.
- Answer questions precisely and avoid jargon. Explain identified problems and conclusions clearly, without extensive prose. Base recommendations on facts and arguments, not convincing language.
- Do not start building from exploratory questions. Present findings and options, then wait for review before implementing.

## Project Orientation

`opencode-rank` is a stdlib-only Python console tool (no dependencies, ever)
that ranks AI models by benchmark score against per-token price. No network
calls except `fetch` (and key-gated reads); everything else runs offline
against `data/`.

**Entry points for a new agent:**

1. **What it does** — `README.md` (usage, scoring arithmetic, data layout). The scoring rationale lives next to the code it governs (`scoring.py`, `ranking.py`); read both together.
2. **What the plans cost** — `LIMITS.md`. Workspace billing reality, verified against the live docs. Treat the doc links as authority.
3. **Security posture** — `SECURITY.md`. What touches the network, key handling (`OPENCODE_API_KEY` env first, `~/.config/opencode-rank/api_key` fallback), reporting channel.
4. **Dev commands** — `justfile` at the repo root. Key recipes: `just test` (unittest), `just check` (tests + entry point + wheel), `just lint` (ruff: lint + format check + complexity gate; dev-only dependency, locked in `uv.lock`), `just audit` (secret-pattern sweep), `just run <command> [args...]` (the console against the checkout dataset, e.g. `just run next`, `just run fetch` — needs `data/` on disk: `fetch` once, or the `local-data` branch). Git hooks live in `.githooks/` (pre-push guards `local-data`; pre-commit runs the audit sweep plus ruff via the project venv); `just bootstrap` enables them with `core.hooksPath` unless you already use your own.
5. **Sources** — `opencode_rank/cli.py` (every subcommand), `fetch.py` + `boards.py` + `arena.py` (network layer, injected `get` for offline tests), `pricing.py` (tier docs, free-tier nominals), `scoring.py` + `ranking.py` (scales, ordering, `next` sequence), `availability.py` + `live.py` (what the plans offer), `tables.py` (aligned console output).
6. **Tests** — `tests/test_<module>.py` mirrors the package; view contracts live in `tests/test_views.py` (a view change starts there). Fixtures are synthetic; the only test that reads the checkout is the label-map existence check.
7. **Curated scores workflow** — `.opencode/skills/backfill-scores/SKILL.md` (finding, verifying, and pinning benchmark numbers via `data/overrides/`).

The `.plan/` directory tracks pending and completed work. Run `plan list` to view the list of issues.

## Project Development Workflow

- Before diving into solutions, take a step back and consider best-practice,
  pros and cons of available options. Clarify ambiguous requirements and trade-offs
  with the user. Verify claims and assumptions against actual code and tests.
- For non-trivial work, establish a bounded change before editing: the intended
  outcome, the files or subsystem in scope, and the checks that will establish
  success. Use the task list and `.plan/` when the work spans multiple steps;
  work systematically and iteratively, delegating bounded items to worker
  sub-agents with written briefs (context, steps, acceptance checks, explicit
  out-of-scope). Workers never commit; the parent verifies the diff and commits.
- Test-driven development is the default: write the failing test first, watch
  it fail for the expected reason, implement minimally, watch it pass. No
  production code without a failing test first. Configuration-only changes are
  the exception.
- When debugging, isolate and reproduce before proposing fixes; never fix by
  assumption. Confirm the fix with a regression test, not a re-run alone.
- Before claiming success, run the applicable automated checks, inspect the
  final diff and status, and report any checks or quality gates that remain
  unavailable. State results with evidence (command output, counts), never
  from memory of an earlier run.
- This package is stdlib-only by design (`dependencies` must stay empty).
  Do not add a dependency without an explicit user decision; a vendored
  helper or a narrower scope is the default answer.
- Prefer repository mechanisms over agent memory. If a recurring concern cannot
  be checked by the existing commands, tests, or linters, record it as a
  tooling/backlog item instead of expanding this file with a one-off reminder.
- Keep implementation, tests, design documentation, and generated artifacts
  aligned in the same change when they describe the same behavior. Do not claim
  a safeguard exists unless its check is implemented and has run.
- When done, provide a summary of the change. Commit in reasonable steps on
  request; do not push unless asked. Commit messages are short imperatives
  naming what changed (`next interleaves free and paid picks 50/50`), never
  with examples or documentation.
- Never write secrets, tokens, or key material into the repo, tests, or
  fixtures (use `oc_sk_test`-shaped dummies in tests). The fetched layers
  under `data/` are gitignored except `data/overrides/`; never commit
  fetched records, and never push the `local-data` branch.

## Task Management (.plan)

Every task lives as a markdown file in `.plan/<slug>.md` with YAML frontmatter. The filename (without `.md`) is the unique key — no numeric ID needed.
Trivial single-fix tasks skip the plan file; say so in one line and do the work.

### Status lifecycle

| status        | meaning                   | when to use                                   |
| ------------- | ------------------------- | --------------------------------------------- |
| `idea`        | aspirational, not settled | explore later, not ready to start             |
| `backlog`     | accepted, deferred        | consider when stepping back to plan next work |
| `open`        | ready                     | actionable, waiting to be picked up           |
| `in-progress` | active                    | currently being worked on                     |
| `blocked`     | stuck                     | note the blocker in the body                  |
| `done`        | complete                  | finished                                      |

### Task Management Workflow

- **Discover work:** `plan list` lists all tasks; prefer priority high with status `in-progress` or `open`.
- **Step back / plan:** consider `backlog` and `idea` items when deciding next big steps.
- **Create:** use `.plan/.TEMPLATE.md` and its contained instructions for a new task file `.plan/<slug>.md`.
- **Update:** set `status` to match reality, and append refinements and decisions while keeping the task concise — problem, goal, implementation steps. Treat a task file as a WIP spec.
- **Complete:** set `status: done` when finished. Do not delete the file.
- **Block:** set `status: blocked` and note the blocker in the body.
- **Validate:** run `plan lint` to check; `plan format` to auto-format. Both ship with the `plan` tool itself (no justfile wrapper).

## Code comments and documentation

- Write inline code comments, concise and conventional: state what the code
  does not already say. Explain a special implementation aspect only where
  user-facing documentation does not already cover it.
- Code and documentation must be self-contained: describe the current state,
  never the progress or plans that led to it (no "new", "now", "recently",
  "TODO for step X", or pointers at plan files).
- `.plan/` files and task notes are short-lived developer scratchpads. They
  may be referenced in git commit messages, but never in code comments or
  documentation.
- Scoring rules, billing realities, and usage instructions belong in
  `README.md` / `LIMITS.md` (user-facing), never only in code comments.
- Curated `data/overrides/` entries always carry a `source` URL and, where
  useful, a dated note on who ran the evaluation — unattributed scores do
  not belong there (see `data/overrides/README.md`).
