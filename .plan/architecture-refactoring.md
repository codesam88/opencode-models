---
status: open
type: decision
priority: high
area: backend
---

## Notes

Define the target high-level architecture for opencode-models and capture it
as `docs/architecture.md`. Today the code is ad hoc: output rendering is
mixed with commands (`cli.py` ~1900 lines owns parsing, business decisions,
formatting, and `print` per command), commands are mixed with function
(`_rows_for`, roster synthesis, cost-base selection, grading live next to
argparse), and function rests on several sources with ad hoc aggregation
rules and no lifecycle management (catalog, tier prices, plan snapshots,
live gateway lists, arena cache, boards, curated overlays — each with its
own freshness/fallback/merge rule decided at the call site).

The deliverable is the architecture document only — no implementation, no
refactor, no code changes. It inventories implemented functionality, fits it
into a consistent layering (ingestion → storage → pure domain processing →
typed per-command use cases → presentation), and states per component its
contract and what must change to get there. No implementation steps, effort
estimates, or sequencing.

## Deep-review pass (before writing)

- Inventory every module under `opencode_models/` (all ~20: `fetch`,
  `pricing`, `boards`+`labelmap`, `arena`, `live`, `availability`, `models`,
  `overrides`, `identity`, `scoring`, `ranking`, `allowance`, `tables`,
  `seed`, `pareto`, `benchmarks`, `auth`, `cli`) plus
  `scripts/update_go_pricing.py`: responsibility, inputs/outputs, I/O
  performed, callers.
- Inventory all 8 commands (`list`, `next`, `scores`, `models`,
  `overrides`, `fetch`, `usage`, `auth`): which pipeline each runs, where
  pipelines agree by convention (`_rows_for`) vs by interface, where each
  re-derives availability/naming/cost.
- Map every `data/` layer (`opencode/`, `tier_prices/`, `boards/`,
  `aiarena/`, `available/`, `plans/`, `usage/`, `overrides/`, `manual/`) +
  shipped fallbacks (`opencode_models/data/plans/`) + `local-data` branch:
  writer, reader(s), freshness rule, fallback position, retention rule.
- Record dead/semi-dead code found (`pareto.frontier` has no caller; `seed`
  mixes install-time deploy with runtime paths) as explicit keep-or-delete
  decisions for the document.
- Read `README.md` "How it works", `LIMITS.md`, `data/overrides/README.md`,
  and the `.plan/` backlog so the document aligns with documented semantics
  rather than redefining them.

## Document shape (target)

- `docs/architecture.md`: problem statement (the three mixings), full
  functionality inventory table, target layering with one-way dependencies,
  per-component contract + what-must-change, target data-flow with
  invariants (scales over full catalog, never-delete records, upstream
  never edited / curation as overlay, ingestion writes / views read),
  structural test strategy (synthetic-input domain suites, synthetic-tree
  storage tests, injected-transport ingestion tests, fake-store use-case
  tests, golden-output presentation tests).
- Out of scope for the document: implementation steps, sequencing, effort,
  API signatures beyond input/output contracts.

## Acceptance

- `docs/architecture.md` exists and covers every module, command, and data
  layer above — reviewer can check each off against the tree.
- Each component states its architectural contract and direction of change;
  no detailed implementation steps or effort included.
- Key structural points captured: ingestion separate from processing;
  domain pure and synthetic-testable; rendering separate from commands;
  commands behind typed interfaces to domain logic.
- `plan lint` passes for this file.
