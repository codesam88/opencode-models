# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Releases are cut from `main` by tagging `vX.Y.Z`; see
CONTRIBUTING.md ("Releasing") for the checklist.

## [Unreleased]

### Changed
- Project renamed `opencode-rank` → `opencode-models` (distribution,
  console binary, importable package, and the XDG config/data dirs)
  ahead of the first public release.

### Added
- `opencode-models --version` reports the installed package version, and
  the same version is sent as the HTTP `User-Agent` on every request
  (previously a hardcoded `0.1` that drifted from the package version).
- `opencode-models models`: lists the models each plan offers right now as
  endpoints beside display names — live from the Console config API with
  `OPENCODE_API_KEY`, else the last `fetch` snapshots (recorded on the way).
- `opencode-models auth`: saves the Console service-account key to an
  owner-only file instead of requiring it in the environment every time.
- `next` interleaves free and paid picks and defaults to 6 recommendations.
- `list` shows nominal score-per-dollar for free lines instead of the word
  `free`, so free models compare against each other.
- `scores` header `SWE Verified` shortened to `SWE Vrfd`; the `AXES` column
  removed (the counts line above the table already covers it).
- Shared table renderer (`tables.py`): measured columns that widen instead
  of breaking, with width tripwire tests per view.

### Fixed
- Keyed `fetch` no longer crashes on tier-pricing free rows.
- `--source all` no longer leaks dropped models under the `-` provider.
- Alias twins (e.g. `muse-spark-1.3` / `-contributor`) take one `next` slot.
- Corrupt or missing caches/snapshots degrade to "unknown" instead of
  tracebacks (arena cache, tier snapshots, live snapshots).
- A losing curated override no longer relabels the kept value's source.
- Unclosed HTTP error responses no longer warn at GC time.

### Security
- CI runs gitleaks secret scanning on push and PR, plus a Zizmor
  workflow audit (SARIF upload off: code scanning is not enabled).
- `just lint` gates ruff lint, formatting, and a complexity ceiling in CI.
