---
name: backfill-scores
description: Use when an opencode-rank model has only one scored benchmark and is therefore unranked, or when asked to find, research or add benchmark numbers — covers where to look, which sources are actually scrapable, how to write a data/overrides JSON file, and how to verify it landed.
---

# Backfilling missing benchmark scores

A model needs two of the four scored benchmarks to carry a score at all
(`scoring.MIN_AXES`), so the models worth chasing are the ones sitting on
one. This is how to find them, source a number, write it down, and prove it
landed.

The rule everything else serves: **a number without a source is not data.**
The loader drops any override entry that has no `source`, so a file without
one is inert — it will not appear, fail, or warn.

## 1. Find what is missing

The target list is models that are **listed in a Zen or Go plan** and carry
**one** scored benchmark. Both filters matter:

- A model no plan lists is a model about to be unlisted. A number for it
  changes nothing, because nothing recommends it.
- A model with no scored benchmark at all is a different problem (nothing to
  pair with, and usually nothing published). A model with *one* is a missing
  pair away from being ranked.

```sh
PYTHONPATH=. uv run python -c "
from pathlib import Path
from opencode_rank.cli import _load
from opencode_rank.scoring import scales, derived_scores, canonical, _TRACKED
from opencode_rank.availability import load_available, offered
from opencode_rank import sources

models, *_ = _load(Path('data'))
table = scales(models)
# availability is the provider's own snapshot, not the committed slug lists:
# a model a plan has dropped is not worth a number
offered = {m.model_id for tier, ids in load_available(Path('data')).items()
           if ids for m in sources.members(models, ids)[0]}
for m in models:
    derived = derived_scores(m, table)
    plan = next((t for t, ids in load_available(Path('data')).items()
                 if ids and offered(m.model_id, ids)), None)
    if not plan or len(derived) != 1:
        continue
    has = canonical(next(iter(derived)))
    missing = [b.replace('Terminal-Bench ', 'TB ').replace('SWE-Bench ', 'SWE ')
               for b in _TRACKED if canonical(b) != has]
    print(f'{plan:4} {m.model_id:34} has {has:20} missing {\", \".join(missing)}')
"
```

**Skip old and superseded models.** A nano, a mini, a two-generations-back
release, and anything the provider is about to retire will not change a
recommendation. Chase the new frontier and the opencode-exclusive ones first;
those are where a second axis moves a row.

Coverage per benchmark, for a wider view:

```sh
uv run opencode-rank --data data benchmarks
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

**Checked and empty for the thin models (2026-10).** Of the 26 Zen/Go models
sitting on exactly one axis, neither board holds a second scored axis for any
of them: `tbench.json` carries only Terminal-Bench 4.0 rows we already have,
and the one SWE-bench row that matches (Kimi K2.5) is Multilingual, which is
not scored on. Do not re-run this check hoping for a different answer — it
only changes when the boards themselves gain rows, and `fetch` reports that.

## 3. Where to look, in order

### Vendor pages, and what they actually publish

Vendor launch pages **are** scrapable — `curl -sL` with a real User-Agent
returns 200–300 KB of HTML — but for new frontier models they publish
Terminal-Bench 4.0 and little else. Checked on Anthropic's Haiku 5.5 and
Sonnet 5.5 pages and xAI's Grok 4.7 post: all three carry Terminal-Bench 4.0,
none carries SWE-bench or Terminal-Bench 2.1.

Normalise before concluding a page lacks a benchmark: vendor HTML writes
"SWE‑bench" with U+2011, "SWE-bench™" with a trademark, and non-breaking
spaces. A plain-space search misses all three and reports a false negative.

```sh
python3 - <<'EOF'
import re, subprocess, unicodedata
html = subprocess.run(["curl", "-sL", "--max-time", "30",
                       "-H", "User-Agent: opencode-rank/0.1", URL],
                      capture_output=True, text=True).stdout
for a, b in (("\u2011", "-"), ("\u00a0", " "), ("\u2122", ""), ("\u00ae", "")):
    html = html.replace(a, b)
html = unicodedata.normalize("NFKC", html)
print(sorted({m.group(0) for m in
              re.finditer(r"(terminal[\s-]*bench|swe[\s-]*bench)[^<>\n]{0,30}",
                          html, re.I)}))
EOF
```

This is the structural finding behind the thin-model list: the axis vendors
publish is Terminal-Bench 4.0, and it is the one axis a model can be alone
on. A model announced today will usually have exactly one scored axis.

### Hugging Face model cards

Public and reliable to read, and they carry the metrics a lab chose to
publish. Kimi K3's card has Terminal-Bench 2.1 (88.3) in an HTML results
table — the benchmark name is in one `<td>`, the numbers in the next ones, so
parse the table rather than searching for a "name: value" pattern. MiMo-V2.5's
card carries no scored axis at all. A 29-byte card means the repo name is
wrong (`Entry not found`), not that the model is unbenchmarked — search
`https://huggingface.co/api/models?search=<name>` for the real id.

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
| **Hugging Face datasets-server** | `https://datasets-server.huggingface.co/rows?dataset=<org>/<name>&config=…&split=…&offset=0&length=100` | public JSON, no auth. Already used: `opencode-rank fetch` reads arena Elo from `openaccess-ai-collective/chatbot-arena-elo-scores` (fields `elo_score`, `chatbot_name`) |
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
for X?") come back with nothing. Give them, per model, **only the axes it
lacks** — asking for a number the catalog already holds wastes their time and
yours:

```
Model:  moonshotai/kimi-k3  (Kimi K3)  — Zen
Has:     Terminal-Bench 2.1  88.3
Looking for (one of these is enough to make it rankable):
  - SWE-Bench Verified (% resolved), or SWE-Bench Pro
  - Terminal-Bench 4.0 (pass@1)
What makes a number usable:
  - which harness/agent ran it (Claude Code, Codex, mini-swe-agent, …)
  - reasoning effort, if the board distinguishes it
  - the date, and the CI or trial count if published
  - the exact URL to the run, not a screenshot
Why it matters: one axis is not a score (scoring.MIN_AXES), so this model is
unranked and never recommended, however strong that one number is.
```

A bare score with no harness is worth far less than the ranking suggests —
Terminal-Bench 2.x and 4.0 are different scales, and a run on a bespoke
scaffold is not comparable with a standardized board.

Batch the ask: one message listing the models, each with the single axis it
most needs, beats 26 separate ones. Prioritise by how strong the model
already looks on its one axis — a model at 88 on Terminal-Bench 2.1 that
cannot be ranked is the most expensive gap in the console.

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