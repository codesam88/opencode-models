---
status: done
type: feature
priority: high
area: backend
---

## Notes (newest first)

- 2026-10-30: done. Worker implementation reviewed, one real defect fixed by parent (unresolvable peak note raised → broke whole Go refresh; hardened to unknown+warning with RED-first test, extracted `_find_peak` for the complexity gate). 751 suite green, ruff clean, real-data verified (fail-safe Peak default, both under `--all`, `next` inherits). `docs/snapshot-schema.md` updated for the `peak` block. Left staged-uncommitted; leftovers: real `sync` to populate `peak`, `plans-freeze` promotion. Note: concurrent commit 1d979e4 (DeepSeek 1017 override, another session) touches different files — no interaction, suite green with it.
- 2026-10-30: created from peak-window investigation. Page publishes one scoped note (DeepSeek V4.1 Flash / V4 Pro only); only those two slugs carry Off-Peak/Peak tiers.

## Problem

Go tier prices for `deepseek-v4-pro` and `deepseek-v4.1-flash` come in
Off-Peak/Peak variants, and the console lists both lines with no notion
of which rate applies now — so a user reading `list`/`next` during peak
hours sees (and plans around) the cheap line. The docs page states the
window (01:00–04:00 and 06:00–10:00 UTC, Mon–Fri peak; all else
off-peak), but nothing extracts or applies it.

## Solution strategy

Extract once, filter everywhere: extend the Go docs parser to capture
the peak note into the plans snapshot as normalized validity windows,
then filter Off-Peak/Peak tier lines to the currently-valid one in the
shared row pipeline (so `list` and `next` inherit together). Unknown
windows fail safe to the more expensive (Peak) line — never the cheap
one. `--all` shows both lines. Token-window tiers (`≤/>N tokens`) are
size-dependent and untouched.

Rejected: filtering in each command separately (drifts — `rows_for` is
the choke point); failing open to both lines on unknown (shows a cheap
rate that may not apply); a separate `--tiers` flag (user specified
`--all`).

## Implementation steps

1. **Parser + snapshot** (`opencode_models/go_pricing.py`, tests in
   `tests/test_go_pricing.py`): capture the DeepSeek peak note
   paragraph; normalize to windows + weekday set + UTC + resolved
   slugs (via the Endpoints table) + raw note; store in the snapshot
   (new top-level block, old snapshots read as unknown). Done when new
   parser tests pass on a saved fixture and `sync --check` still gates.
   Leaves out: any other note paragraphs (Beam/Iceman/Argon untouched).
2. **Validity filter** (new pure helper, e.g. in `go_pricing.py` or a
   small module; unit-tested with injected UTC clock): windows +
   weekday + now → active tier (`Peak`/`Off-Peak`); unknown windows →
   `Peak` (fail safe). Boundary cases (window edges, weekend, UTC
   midnight) covered. Done when boundary tests pass.
3. **Pipeline wiring** (`opencode_models/rows.py`, tests in
   `tests/test_rows.py`): after tier rows build, drop the non-valid
   Off-Peak/Peak line per model unless `all_models`; `≤/>N` lines
   never filtered. Done when `list` shows one DeepSeek line by
   default, both under `--all`, and suite passes.
4. **Verify**: `just test`, `just lint`, real-data `list` vs `list
   --all` vs `next` at a known UTC time, shipped-fallback promotion
   notes (`plans-freeze` is a separate curation step, not this task).
   Done when evidence recorded here.

Out of scope: Zen (no peak concept), token-window tiers, `plans-freeze`
promotion, live Gateway/Console changes.
