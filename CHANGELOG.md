# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Releases are cut from `main` by tagging `vX.Y.Z`; see
CONTRIBUTING.md ("Releasing") for the checklist.

## [Unreleased]

## [0.1.2] - 2026-10-26

### Changed
- **models.dev is the go/zen catalog.** `sync` pulls its two opencode
  providers (`opencode-go`, `opencode`) into `data/modelsdev.json`, and
  `list`/`next` render those records intersected with what a plan serves:
  names, prices and limits come from there, benchmark scores still join
  from the model cards. A served model the slice does not carry is not
  listed — which is also what keeps the gateway's `test` and `*-backup`
  ids out, so no id filter remains. Without the slice (fresh clone,
  offline) the cards decide as before; `list --all-models` on a tier now
  shows that tier's whole catalog.
- **`--cost est` prices every line from its own token mix** — the model's
  published in/cached/out mix, else the **Go plan's median** for a line
  that publishes none, marked `~`. That covers zen lines (zen publishes
  no mixes) and models no plan record prices; the fix rate applies only
  where no mix is published anywhere. Zen and go are now on the same
  basis, so score/$ compares across them.
- **Each labelled variant prices from its own tier** — peak/off-peak and
  ≤/>100K, ≤/>200K, ≤/>272K lines carry their own rates instead of
  inheriting the base tier's.
- `¢/req` keeps up to two decimals, so a fraction of a cent survives
  (`0.03`, `6.12` instead of `0`); `¢/1m` under `input`/`output` stays
  whole cents.
- **Failures warn and keep going**: a sitemap or board fetch that fails,
  an unreachable usage fallback and a model without published token
  assumptions now warn and use the last data instead of stopping the run
  or quietly vanishing from the view.
- **Designation twins merge**: a plan designation and the model it
  designates are one row with a shared score (the plan's own label still
  names the priced line); an overlay keyed to a designation that no longer
  matches is reported as an orphan instead of silently dropped.
- **Refactored since 0.1.1** — internal, no behaviour beyond the above:
  - `list` and `next` share one row pipeline, and catalog composition is
    a single pure entry point, so a recommendation cannot disagree with
    the ranking it was drawn from.
  - commands live in `opencode_models/commands/` with rendering split
    from decision: `sync`, `auth`, `overrides`, `models`, `scores`,
    `fetch`, `usage`, `list`, `next`.
  - one unified plan snapshot per tier (`plans/<tier>.json`) replaces the
    old `tier_prices` layer, with a documented schema contract and
    readers that tolerate legacy envelopes.
  - availability, snapshot freshness, curation and pricing moved out of
    the commands into services; the old pricing scripts are package
    modules.
  - `sync` owns staleness for the T2 layers and read commands refresh a
    stale snapshot lazily on their own.

### Added
- **Plan identity**: `--plan go|go-plus`, or a preference saved by
  `auth --plan`, decides whose allowance every view reads; `usage` hints
  Plus when measured spend exceeds the Go cap.
- **Markers**: `list` flags median-estimated (`~`) and curated (`*`) rows
  and explains the symbols in a legend line.
- `sync` reports a `modelsdev:` source line and refetches the slice about
  daily (`MODELSDEV_MAX_AGE`, 24h).

## [0.1.1] - 2026-10-23

### Changed
- Availability now comes from the inference gateway model lists
  (`/inference/v1/models`, `/inference/go/v1/models`) fetched with a key —
  the Console config API's stale set (all `disabled`, Go empty) only
  annotates. A keyed run with zero Go models prints a warning pointing at
  Console model enablement and personal-key permissions.
- `--price` is `--cost` now, defaulting to `est`, and the ruled table
  headings are title-cased.
- `usage` is rebuilt around weekly and monthly allowance windows, and
  free lines price at $0 (the nominal-rate machinery is gone).
- Tier rosters resolve from the plan snapshots instead of hand-maintained
  lists, and a plan's `-free` / `-contributor` designation folds onto its
  base model.

### Added
- `models --all` adds known-but-unserved rows plus an `Avail` status
  column (`[x]` served / `[ ]` not served / `[?]` unknown); the default
  view is endpoint + name only. Rows the catalog cannot name are hidden.
- Validated Go and zen plan pricing snapshots ship in the package as the
  offline fallback.
- A curated alias map (`data/aliases.json`) points roster slugs at their
  catalog records, and `data/manual/` holds tracked catalog records merged
  under the fetched ones.

## [0.1.0] - 2026-10-21

First release. `opencode-models` is a stdlib-only console tool that ranks
AI models by benchmark score against real per-token price, over the
opencode.ai catalog — no dependencies, everything offline except one
explicit fetch command and optional key-gated reads.
