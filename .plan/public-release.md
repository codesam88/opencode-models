---
status: done
type: chore
priority: high
area: devops
---

## Notes

Public-release hardening, decided 2026-10-20. Decisions (same day, with
maintainer — they supersede the plan below where they differ): the
email stays everywhere (`codesam` name is fine too) — LICENSE,
commit identities, pyproject contact unchanged, no history rewrite.
README contact paths are GitHub issues + private security advisories
only. The gitleaks pre-commit hook needs its own toolchain
(golang/docker image) or a system binary — fails the local-and-cheap
test, so the local audit-grep hook stands in and CI keeps running real
gitleaks.

## Steps

- [x] 1. ~~LICENSE identity + history rewrite.~~ DROPPED by decision:
  email and `codesam` name stay as-is everywhere; no rewrite, no
  force-push.
- [x] 2. README due-diligence paragraph on benchmark data (GitHub-only
  contact paths). Added as "### Data due diligence" under ## Data.
- [x] 3. Client-side git hooks, no new installs (`.githooks/`, wired via
  `just bootstrap` without clobbering an existing `core.hooksPath`):
  `pre-push` refuses `refs/heads/local-data`; `pre-commit` runs the
  secret-pattern sweep and `ruff check` + `ruff format --check` via the
  project's own venv, with a graceful skip when ruff is unavailable.
  Verified: pre-commit passes on a clean tree, fails on planted token
  fixtures (a fake GitHub PAT and a fake AWS key); pre-push refuses a
  `local-data` push and allows normal branches (scratch-repo end-to-end
  run).

## Open questions — all settled 2026-10-20 (see Notes above for answers)

- Q1: commit identities — settled: leave unchanged.
- Q2: `pyproject.toml` author contact — settled: leave unchanged.
- Q3: due-diligence paragraph contact path — settled: GitHub issues +
  private security advisories.

## Verification (revised — no rewrite, no force-push anywhere)

- `just lint`, `just test` green on a fresh checkout after `just bootstrap`.
- Hooks fire on trial runs: `pre-commit` blocks a planted secret-pattern
  file and fails a planted lint violation; `pre-push` refuses a
  `local-data` push and allows a normal branch push.
- `git config core.hooksPath` untouched when already set to something
  else (bootstrap must not clobber an existing hooks setup).

## Remaining scope (landed after 2026-10-20 — verified 2026-10-24)

- [x] Release process: tag-triggered publish workflow
  (`.github/workflows/release.yml`, tag `v*` + version-match gate +
  trusted publishing); single-sourced version (`pyproject.toml`
  `version`, User-Agent via `__version__`); `CHANGELOG.md` with
  `Unreleased` + `0.1.0`; `CONTRIBUTING.md` "Releasing" checklist.
  First PyPI publish + trusted-publisher setup remain external.
- [x] Repo hygiene: `CONTRIBUTING.md` only (no issue/PR templates,
  no CODE_OF_CONDUCT per decision).
- [ ] GitHub push protection + secret scanning at public-launch time
  (external; local grep + CI gitleaks stay as the pre-launch layers).
- [ ] Source-site Terms of Service review (deferred counsel work;
  README due-diligence note is the interim stance).
