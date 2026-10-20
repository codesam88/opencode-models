---
status: in-progress
type: feature
priority: high
area: cli
---

## Notes

`fetch` dies with a traceback when the catalog sitemap is unreachable, and
`list` / `next` / `models` silently use arbitrarily old snapshots. Two
fixes, both in console output behavior, no ranking changes:

1. Catalog fetch failure degrades to a warning (`keeping existing records`),
   same shape as the existing per-source warnings. Exit code stays 0.
2. `list`, `next`, and the `models` snapshot path warn per tier when a
   `tier_prices/<tier>.json` snapshot is older than 7 days
   (`STALE_AFTER_DAYS`), by file mtime. Missing files stay silent (unknown
   is already reported as unknown).

Out of scope: exit-code changes, auto-refresh, freshness of `data/available`
(keyed runs re-fetch it live), docs beyond one README line.
