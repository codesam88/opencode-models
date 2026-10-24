---
status: idea
type: feature
priority: medium
area: devops
---

## Notes

Automate the plan-snapshot refresh for both plans, end to end. Split out
of `model-go-allowance.md` step 4 (2026-10-23); that step keeps the
manual workflow as the interim answer.

Where things stand today (all manual, all working):
- `just plans-sync` (unconditional refresh of both plans via
  `sync --force`: fetch, parse, validate, extend-not-replace merge
  into gitignored `data/plans/<tier>.json`), human confirmation
  (dropped real? assumptions present? limit deltas plausible?),
  `just plans-freeze` (`plans-check` re-validates offline, then copies
  over the shipped fallback in `opencode_models/data/plans/`), commit.
  The committed fallback is what the wheel ships and what offline runs
  fall back to. The old `scripts/update_*.py` are retired; the package
  modules own the logic behind the sync surface.

Open leaning 2026-10-24: automation may be unnecessary — manual
refresh as part of releases (download + freeze when cutting a
release, plus on-demand when a page change is suspected) could be the
permanent answer. Snapshots already self-report age, and lazy sync
keeps read paths fresh without any cron. This idea stays open only
until that call is made: either close it (manual-at-release wins) or
promote it (automation gaps below become requirements).

What automation would add (the gaps listed in the parent step):
- `page_sha256` in snapshot meta, so a silent page rewrite is detectable
  even when parsing still succeeds.
- Per-model limit deltas in the report, so the human (or reviewer)
  sees cap moves at a glance.
- A weekly cron Action: runs both updates; gate failure = red workflow,
  no PR; diff vs the shipped fallback = PR body; merge = promote
  (i.e. the freeze step becomes landing the PR).
- Staleness display in views (snapshot age is already shown in some
  headers; decide what "stale" means per tier and where it surfaces).

Open questions for the maintainer before this leaves idea:
- Publish target: keep committing the fallback into the repo, or ship a
  versioned asset (e.g. GitHub release) that clients pull? The manual
  flow assumes committed fallbacks.
- Cadence and ownership: weekly cron, or on-demand? Who merges the
  refresh PRs, and what counts as suspicious enough to block on?
- Zen has no limits/estimates, so its gate stays structural while Go's
  stays arithmetic — one workflow with two gates, or two workflows?
