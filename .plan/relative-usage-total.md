---
status: done
type: feature
priority: high
area: backend
---

## Notes

The Console window percent tracks the sum of per-model relative usage (each
model's spend divided by its own window cap), not max-model or total-spend /
total-caps. Live 2026-11-20 check: API monthly 100 with status `rate-limited`,
raw sum of relative percents 99.13% (100% when the displayed rounded cells
are summed), max-model 45.88%, spend-share 16.79%.

Scope: add `sum_relative` to `compare_window_percent` (with tests), show the
raw sum as a total row in the `usage` Month section (with tests), and replace
the "opaque percent" claims in `LIMITS.md` and `docs/model-go-plan.md` with
the verified rule. No plan-file or commit changes by workers.

2026-11-20: all three items implemented. Further live checks showed weekly
43.59% vs API 39 and rolling 62.83% vs API 38, so the rule is documented and
coded as verified at the monthly cap only; shorter windows stay open.
`just check` (757 tests, ruff, vermin pre-existing warning, zizmor, build) and
`just audit` pass. Working tree uncommitted.
