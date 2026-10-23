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
- `scripts/update_go_pricing.py` / `scripts/update_zen_pricing.py` fetch,
  parse, validate (go: 10%-tolerance estimate reproduction; zen:
  structural), and merge extend-not-replace into gitignored
  `data/plans/<tier>.json`. `--from` parses a saved copy, `--out`
  selects the target, `--check` validates offline with no network.
- `just plans-update[-zen]` (fetch+validate; exit nonzero writes
  nothing), human confirmation (dropped real? assumptions present?
  limit deltas plausible?), `just plans-freeze[-zen]` (re-check, copy
  over the shipped fallback in `opencode_models/data/plans/`), commit.
  The committed fallback is what the wheel ships and what offline runs
  fall back to.

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
