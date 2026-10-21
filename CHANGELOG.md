# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Releases are cut from `main` by tagging `vX.Y.Z`; see
CONTRIBUTING.md ("Releasing") for the checklist.

## [Unreleased]

### Changed
- Availability now comes from the inference gateway model lists
  (`/inference/v1/models`, `/inference/go/v1/models`) fetched with a key —
  the Console config API's stale set (all `disabled`, Go empty) only
  annotates. A keyed run with zero Go models prints a warning pointing at
  Console model enablement and personal-key permissions.

### Added
- `models --all` adds known-but-unserved rows plus an `Avail` status
  column (`[x]` served / `[ ]` not served / `[?]` unknown); the default
  view is endpoint + name only. Rows the catalog cannot name are hidden.

## [0.1.0] - 2026-10-21

First release. `opencode-models` is a stdlib-only console tool that ranks
AI models by benchmark score against real per-token price, over the
opencode.ai catalog — no dependencies, everything offline except one
explicit fetch command and optional key-gated reads.
