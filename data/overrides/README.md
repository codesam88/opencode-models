# Overrides — curated scores layered over the fetched records

One JSON per model, named like the upstream record files
(`<lab>__<model>.json`), holding **only** the fields we vouched for —
never a full copy, so a file can only conflict with upstream where we
deliberately touched something.

```json
{"model": {
  "id": "stepfun/step-5-preview",
  "note": "optional identity / provenance note about the model itself",
  "benchmarks": [
    {"name": "Terminal-Bench", "version": "4.0", "score": 33.3,
     "metric": "success rate",
     "source": "https://…",
     "note": "who ran it, when captured, caveats"}
  ]
}}
```

Rules:

1. **Every score carries a `source` URL** and, where useful, a `note` with
   the capture date, who ran the evaluation, and any caveat. Scores without
   attribution do not belong here.
2. `benchmarks` entries are keyed by (`name`, `version`) — the same key the
   catalog uses — so an override adds a benchmark upstream lacks, or pins a
   value upstream also carries.
3. `fetch` writes **only** the upstream records; nothing ever writes here.
   When upstream drifts away from a pinned value, the override still wins
   the view and the disagreement is reported (`opencode_rank conflicts`).
   To accept upstream, delete the entry.
4. Sources are tiered by comparability (`overrides.py:source_tier`), and a
   pinned value only applies when its source is at least as good as
   upstream's:

   | tier | kind | examples |
   |---|---|---|
   | 1 | independent evaluator running every model on one harness | artificialanalysis.ai |
   | 2 | official benchmark board (standardized tasks, scaffold recorded per row) | tbench.ai, swebench.com |
   | 3 | vendor model card / launch post, aggregators without methodology | x.ai, anthropic.com, huggingface.co model cards |

   A tier-1 override replaces a tier-3 upstream number (Grok 4.7's
   Terminal-Bench 4.0: AA's 33 over x.ai's 37.6); a tier-3 claim against
   independent upstream is recorded and shown by `conflicts` but not
   applied. Aggregator numbers are fair game **when labelled as such** —
   see the Step 5 Terminal-Bench 2.1 entry, which is flagged because it is
   absent from the official tbench.ai 2.1 leaderboard.

## Currently empty — and that is the intent

The four files this directory held are gone. `fetch` now pulls the boards
themselves (Terminal-Bench 4.0 from its own board page, SWE-bench from
swebench.com), which is where provenance should come from: a number with a
source and a date that refreshes itself, rather than a value someone typed
in and has to remember to revisit.

What removing them costs, measured by diffing every model's scores with and
without the files:

- **Grok 4.7** — the pin to Artificial Analysis's 33.0 is replaced by the
  official board's own 37.58. The two disagree by more than the board's
  CI95, so this is a real evaluator disagreement, not a correction; the
  board's number is the one a reader expects to see.
- **Grok 4.6** — Terminal-Bench 2.1 (88.4) has no fetched equivalent, since
  the board we can read is 4.0 only. Without the pin the model ranks on its
  4.0 value alone (20.3) and reads as far weaker than it is. This is the
  real gap: a 2.1 leaderboard worth fetching would close it properly.
- **Step 5 and Ling 3.1 Flash** — the catalog ships both with
  `benchmarks: []`, so every value they had came from these files. They now
  show no axis and rank as unranked. Their numbers were also the weakest
  provenance available (BenchmarkList/BenchLM, tier 3).

The layer stays: `overrides.py` still parses this directory, applies the
tier policy and reports disagreements, and the loader refuses any entry
without a `source` — an untierable value cannot be pinned. Adding a file
back needs no code, only a good reason.

LongCat-2.5-Preview is deliberately absent, as it was before: no
attributable scores exist yet (even opencode's own pages say "no comparable
benchmark").
