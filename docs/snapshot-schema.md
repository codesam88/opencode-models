# Plan snapshot schema (`data/plans/<tier>.json`)

The file format for per-tier pricing snapshots: one file per tier
(`go.json` with Go/Go Plus via per-plan-keyed `limits` /
`requests_per_month`; `zen.json` standalone, no windows). Written by
`sync` (`go/zen_pricing.refresh`), read by the row pipeline, `usage`,
and the offline fallback chain.

## The rule

**Writers are strict, readers are tolerant.** `refresh` output carries
exactly the shape below — asserted by `tests/test_unified_pricing.py`
(exact key sets on envelope, records, and tiers). Every reader ignores
unknown keys and coerces absent dimensions, so an old install survives
a newer file. Never add a reader that rejects an unknown key, and never
emit a key without a consumer named here.

## Envelope

`meta{source, fetched_at, schema_rev, plans}` + `models{slug: record}`
(Go adds `windows` and `peak`). `schema_rev` is an int, bumped on any
reader-visible breaking change; additive fields never bump it.
`page_sha256` is emitted on every refresh as the silent-rewrite
detector; nothing checks it yet — wiring the check into
`sync --check` belongs to the snapshot-automation idea.

## Peak validity (Go only)

`peak` is the normalized time-validity rule for time-of-day tier rates,
or null when the page publishes no peak note. Shape:
`{slugs, windows[[start, end]...], weekdays[Mon...], tz, raw}` —
windows are half-open `[start, end)` UTC `HH:MM`; `raw` is the source
paragraph. Read by the row pipeline (`rows_for`): Off-Peak/Peak tier
lines keep only the line valid now (`--all` shows both); unknown or
empty windows fail safe to the more expensive Peak line. Request-size
tiers (`≤/>N tokens`) are never time-filtered.

## Record

| key                  | value                                              | read by                              |
| -------------------- | -------------------------------------------------- | ------------------------------------ |
| `name`               | plan display name                                  | row display (designation lines)      |
| `free`               | bool                                               | row banding, gate skips              |
| `tiers`              | list of `{label, in, out, cached}` per priced line  | ranking, `usage`, validation gate    |
| `limits`             | per-plan monthly $ caps, or null when uncapped     | allowance, `usage`                   |
| `requests_per_month` | per-plan published estimates, or null when none    | validation gate, `usage`             |
| `assumptions`        | per-request token mix, or null when unpublished    | `cost_per_request`, validation gate  |
| `dropped`            | true once the page stops listing the slug          | validation skips, retention          |
| `first_seen`         | first refresh that carried the slug                | merge (never-delete accounting)      |

Absent dimensions are null — bare `null` (Zen) or a per-plan map of
nulls normalized to bare `null` on write (Go free tiers). Readers treat
both as "no data", never as zero: null-cached input bills at the input
rate, null limits mean uncapped.

Missing `assumptions` are never backfilled into the file: views substitute the plan median at render time and footnote which models used it.

## Deliberately not stored

`cached_write`, `deprecated`, `deprecation_date`, `endpoint`,
`sdk_package`, `notes` were emitted but read by nothing; dropped
2026-10-24. Re-add any of them only with a consumer named in the table
above. Deprecation stays Zen-parsed at fetch time (the "still has a
pricing row" diagnostic); stored deprecation marking waits for a
triggering need.
