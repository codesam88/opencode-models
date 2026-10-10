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

Current files:

- `stepfun__step-5-preview.json` — AA/BenchLM scores for a model the
  catalog ships with `benchmarks: []`.
- `xai__grok-4-6.json` — AA's Terminal-Bench 2.1 (88.4) fills the primary
  axis upstream lacks entirely; its 4.0 value is kept but re-attributed to
  the official tbench.ai board. Without this, 4.6 ranked on the 4.0
  fill-in axis only and looked far weaker than models it beats.
- `xai__grok-4-7.json` — Terminal-Bench 4.0 pinned to AA's independent 33
  over x.ai's own 37.6 (a live `conflicts` entry).
- `inclusionai__ling-3-1-flash.json` — BenchmarkList/BenchLM captures for a
  model the catalog ships with `benchmarks: []`.
- `inclusionai__ling-3-1-flash.json` — BenchmarkList/BenchLM scores;
  also records that inclusionAI is Ant Group's AGI initiative.
- LongCat-2.5-Preview is deliberately absent: no attributable scores exist
  yet (even opencode's own pages say "no comparable benchmark").
