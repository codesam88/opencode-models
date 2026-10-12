---
name: backfill-scores
description: Use when an opencode-rank model has no benchmark score, or when asked to find, research or add benchmark numbers — covers where to look, which sources are actually scrapable, how to write a data/overrides JSON file, and how to verify it landed.
---

# Backfilling missing benchmark scores

Some models in the catalog ship with `benchmarks: []` or with scores too weak
to rank on. This is how to find them, source a number, write it down, and
prove it landed.

The rule everything else serves: **a number without a source is not data.**
The loader drops any override entry that has no `source`, so a file without
one is inert — it will not appear, fail, or warn.

## 1. Find what is missing

Coverage per benchmark:

```sh
uv run opencode-rank --data data benchmarks
```

The count next to each name is how many models carry it. The primary axis is
Terminal-Bench 2.x; to list the models that have none of it:

```sh
PYTHONPATH=. uv run python -c "
from pathlib import Path
from opencode_rank.data import load_models
from opencode_rank.ranking import PRIMARY_FAMILY, TB4_FAMILY, _best
models = load_models(Path('data/opencode'))
missing = [m for m in models if not _best(m.scores, PRIMARY_FAMILY,
                                           skip=TB4_FAMILY, versioned=True)]
print(f'{len(missing)} of {len(models)} carry no Terminal-Bench 2.x score')
for m in sorted(missing, key=lambda m: m.model_id):
    print(' ', m.model_id, '|', m.name, '|', list(m.scores) or 'nothing')
"
```

Filter that list down before spending time on it: an unreleased model or a
fine-tune nobody benchmarks will never turn up a number, and a 7B model is
not on Terminal-Bench at all. Prefer models that are **new, strong, and
opencode-exclusive** — those are the ones where a missing score actually
changes the ranking.

## 2. Check what we already fetch first

`data/boards/` holds the official boards, already parsed. If the model is on
one of them, the score is already there and nothing needs writing:

```sh
python3 -c "
import json
for name in ('tbench', 'swebench'):
    for r in json.load(open(f'data/boards/{name}.json')):
        if 'grok' in r['model_name'].lower(): print(r)
"
```

Those two boards are fetched by `opencode-rank fetch` and merged by tier. Do
not hand-copy a number that is already in there.

## 3. Where to look, in order

### Primary for opencode-exclusive and stealth models

**OpenCode's own Reddit and Twitter** — `r/opencode` and the OpenCode
accounts on X. These are where a model appears before it is in any
aggregator: a stealth launch, a "we just shipped X" post, a screenshot of a
leaderboard. They are also the weakest possible source: they are the
vendor's own claim. Cite them as tier 3 unless the post links an
independent run, and say so in the note.

When a model is announced with no evaluation at all, that is a finding, not
a blocker — write a file with no benchmarks? No. Leave it out and say the
model is unbenchmarked, exactly as LongCat-2.5-Preview was.

### Scrapable, verified

| source | how | notes |
|---|---|---|
| **Hugging Face datasets-server** | `https://datasets-server.huggingface.co/rows?dataset=<org>/<name>&config=…&split=…&offset=0&length=100` | public JSON, no auth. Already used: `opencode-rank scores --refresh` reads arena Elo from `openaccess-ai-collective/chatbot-arena-elo-scores` (fields `elo_score`, `chatbot_name`) |
| **Terminal-Bench 4.0 board** | `https://www.tbench.ai/leaderboard/terminal-bench/4.0` | parsed by `fetch`; per row: accuracy ± CI95, agent, reasoning effort, date, rank |
| **SWE-bench** | `https://www.swebench.com/` | parsed by `fetch`; subsets Verified, Multilingual, Multimodal, Lite; `checked` entries only |
| **aider polyglot** | `https://aider.chat/docs/leaderboards/` | a plain `<tr>` table — but its newest entry is GPT-5-era (Aug 2025), so only use it for older models, never for ranking current ones |

### Verified NOT scrapable — do not waste a pass

Artificial Analysis (per-model pages are 4MB app shells, no data API), Vals
AI, Scale's SWE-Atlas, DeepSWE, LiveBench, the Epoch AI benchmarking
dashboard, and the Open LLM Leaderboard HF space: all render their numbers in
a browser. **Check whether the data is embedded before writing a source off**
— tbench.ai looks exactly like one of these and is not, because a Next.js
page streams the data it renders from. Look for `self.__next_f`,
`<script type="application/json">`, or a `_next/data` route before giving up.

## 4. When a source is protected: ask, precisely

When the only remaining source is one a scraper cannot reach, hand the user a
request they can answer in one sitting. Vague asks ("can you find benchmarks
for X?") come back with nothing. Give them:

```
Model:  <model id>  (<display name>)
Looking for:
  - Terminal-Bench 2.1 (pass@1)  — the primary axis
  - Terminal-Bench 4.0 (pass@1)  — its own scale, ranked separately
  - SWE-Bench Verified (% resolved)
What makes a number usable:
  - which harness/agent ran it (Claude Code, Codex, mini-swe-agent, …)
  - reasoning effort, if the board distinguishes it
  - the date, and the CI or trial count if published
  - the exact URL to the run, not a screenshot
Why it matters: it is the only axis this model is ranked on.
```

A bare score with no harness is worth far less than the ranking suggests —
Terminal-Bench 2.x and 4.0 are different scales, and a run on a bespoke
scaffold is not comparable with a standardized board.

## 5. Write the file

`data/overrides/<lab>__<model>.json`, matching the upstream record filename.
Format and full rules are in `data/overrides/README.md`; the short version:

```json
{
  "model": {
    "id": "stepfun/step-5-preview",
    "note": "why this model needs an overlay, in one or two sentences",
    "benchmarks": [
      {
        "name": "Terminal-Bench",
        "version": "2.1",
        "score": 85.0,
        "metric": "pass@1",
        "source": "https://…",
        "note": "who ran it, when, on what scaffold, and any caveat"
      }
    ]
  }
}
```

Rules that are enforced, not advisory:

- **`source` is required.** The loader drops an entry without one.
- **`id` must name its lab** (`lab/model`), matching the catalog.
- **The key is (`name`, `version`)**, the same key the catalog uses — a
  benchmark with no version lands on its own axis.
- **Do not add a model file for a model that has no upstream record.** The
  entry would sit there matching nothing.
- Nothing ever writes here. `fetch` refuses to touch `data/overrides/`.

Source tiers, which decide whether your value applies:

| tier | kind | example |
|---|---|---|
| 1 | independent evaluator, one harness | artificialanalysis.ai |
| 2 | official board, scaffold recorded per row | tbench.ai, swebench.com |
| 3 | vendor post, card, aggregator | x.ai, opencode posts, benchmarklist.com |

A tier-1 override replaces a tier-3 upstream number. A tier-3 claim against
an independent upstream is **recorded but not applied** — it shows up in
`conflicts` and the upstream value stays. Choose the source honestly; the
tier decides the outcome anyway.

## 6. Verify

```sh
just test                                    # guard tests read the real files
uv run opencode-rank --data data conflicts   # did it apply, or just get noted?
uv run opencode-rank --data data list --benchmark "Terminal-Bench 2.1"
```

What to look for:

1. `just test` — the committed overrides are checked for a `source` on every
   entry and a lab-qualified id. A file that fails here is not loadable.
2. `conflicts` — `override wins` means your value is in the view;
   `override noted, upstream kept` means your source was tiered *below* the
   one already there, so nothing changed. That is the tier policy working,
   not a bug.
3. `list --benchmark "Terminal-Bench 2.1"` — the model should appear, with
   `*` beside its name marking it curated.

If a model you expected to be picked does not appear, the most likely cause
is the tier: a vendor number cannot displace an independent one.

## Etiquette

One sequential pass per source, real `User-Agent: opencode-rank/0.1`, 30s
timeouts. No credentials, no telemetry, no scraping a source that asks not
to be scraped. A board that publishes a JSON API is meant to be read; a
social feed is meant to be read by a person.