"""Official benchmark boards fetched directly, as machine-readable records.

The console's axes used to come from one aggregator's transcription — 93%
vendor self-report. These fetchers go to whoever actually runs each board:
the official Terminal-Bench 4.0 board (out of the board page's own data
payload, with the agent and reasoning effort recorded per row) and
swebench.com's embedded leaderboard JSON. Each row carries its source URL and
tier so the merge can prefer an official number over a self-reported one and
report the disagreement.

Sources deliberately *not* fetched: Artificial Analysis, Vals AI, Scale's
SWE-Atlas board and DeepSWE render their numbers in a browser (probed: app
shells, JS-only tables, no data API) — a stdlib fetch gets labels without
scores. aider's polyglot table *is* static HTML, but its board stops at the
GPT-5 generation (top run Aug 2025, nothing from the Claude 5.x / GPT-6 /
Grok 4.7 era), and diff-editing is already covered by SWE-bench and
Terminal-Bench — stale and redundant, so it is left alone.

tbench.ai *looked* like one of those dead ends — the page renders nothing
without JavaScript — but a Next.js page streams the data it renders *from*,
and that payload is the whole leaderboard. Worth remembering before writing
off a JS-rendered source: check whether the data is embedded before
concluding it is unreachable. (The harness repo's submission files parse
more easily and are what this module fetched first; they turned out to be a
stale, deduplicated copy — 21 files against the board's 35 rows, missing
Grok 4.7 and GPT-6 Astra, one effort per model.)
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .fetch import http_get
from .labelmap import mapped_id

#: Tier of a row that comes from the benchmark's own official board.
TIER_BOARD = 2


@dataclass(frozen=True)
class BoardRow:
    """One benchmark board entry: a model's score on a board's own terms."""

    benchmark: str
    model_name: str
    score: float
    source: str
    tier: int = TIER_BOARD
    model_org: str | None = None
    agent: str | None = None
    effort: str | None = None
    date: str | None = None
    note: str | None = None


def _label(field) -> str | None:
    """A display field is {"url": ..., "label": ...} or a bare string."""
    if isinstance(field, dict):
        return field.get("label")
    return field if isinstance(field, str) else None


def _island_json(html: str, marker: str) -> list:
    """Parse a ``<script type="application/json" id=marker>`` island."""
    start = html.find(marker)
    if start < 0:
        return []
    open_tag = html.index(">", start) + 1
    end = html.index("</script>", open_tag)
    try:
        return json.loads(html[open_tag:end].strip())
    except json.JSONDecodeError:
        return []


def _cells(row_html: str) -> list[str]:
    """The text of a table row's cells, blanks dropped."""
    cells = [
        re.sub(r"<[^>]+>", "", c).strip()
        for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)
    ]
    return [c for c in cells if c]


# --------------------------------------------------------------------------
# Terminal-Bench 4.0 — the official board, out of the board page's payload.
#
# The page renders nothing without JavaScript, which is what made it look
# like a dead end; but Next.js ships the dehydrated data the page renders
# *from*, and that payload holds the whole leaderboard: the schema and every
# row, with each reasoning effort ranked separately.
#
# The harness repo's leaderboard/submissions/ looks like the same data in
# plain JSON, and was the first thing fetched here — it is a stale,
# deduplicated copy: one submission per model at one effort, missing rows
# the board already has (Grok 4.7, GPT-6 Astra). The page is the authority.
# --------------------------------------------------------------------------

TB_BENCHMARK = "Terminal-Bench 4.0"
TB_PAGE = "https://www.tbench.ai/leaderboard/terminal-bench/4.0"

#: Next.js streams the page's data as ``self.__next_f.push([1, "…"])`` with
#: the payload JSON-escaped inside a JS string literal.
_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,\s*"(.*?)"\]\)', re.S)


def _flight_stream(html: str) -> str:
    """The page's RSC payload, unescaped.

    The stream arrives as several ``push`` calls and a boundary can land in
    the middle of an escape sequence, so the raw bodies are joined *before*
    unescaping — unescaping each chunk on its own throws the chunk (and
    everything after it) away.
    """
    bodies = _FLIGHT.findall(html or "")
    if not bodies:
        return ""
    try:
        return json.loads(f'"{"".join(bodies)}"')
    except json.JSONDecodeError:
        return ""


def _first_object(text: str, start: int) -> str:
    """The balanced ``{…}`` beginning at *start*, or "" when unbalanced."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""


def tbench_board(html: str) -> dict | None:
    """The dehydrated leaderboard the page was rendered from.

    Looks for the query data holding a Terminal-Bench board: an object with
    a ``leaderboard`` and its ``rows``. A page without one (an error page, a
    redesign, JS-only) yields None rather than an empty board, so a broken
    fetch is visible instead of quietly emptying the axis.
    """
    payload = _flight_stream(html)
    if not payload:
        return None
    needle = payload.find('{"leaderboard"')
    while needle != -1:
        blob = _first_object(payload, needle)
        if blob:
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and isinstance(data.get("rows"), list):
                return data
        needle = payload.find('{"leaderboard"', needle + 1)
    return None


def tbench_row(entry: dict) -> BoardRow | None:
    """One board row: score, model, agent, effort, date, rank and CI95.

    A row without an accuracy is skipped rather than guessed at — the board
    would have no score for it either.
    """
    metrics = entry.get("metrics") or {}
    accuracy = metrics.get("accuracy")
    if not isinstance(accuracy, (int, float)):
        return None
    meta = entry.get("metadata") or {}
    model_name = _label(meta.get("model_display"))
    if not model_name:
        return None
    bits = []
    if entry.get("rank"):
        bits.append(f"rank {entry['rank']}")
    if meta.get("date"):
        bits.append(meta["date"])
    if metrics.get("accuracy_ci95_half_width"):
        bits.append(f"±{metrics['accuracy_ci95_half_width']} CI95")
    if metrics.get("n_trials"):
        bits.append(f"{metrics['n_trials']} trials")
    return BoardRow(
        benchmark=TB_BENCHMARK,
        model_name=model_name,
        score=float(accuracy),
        source=TB_PAGE,
        tier=TIER_BOARD,
        model_org=_label(meta.get("model_org")),
        agent=_label(meta.get("agent_display")),
        effort=meta.get("reasoning_effort"),
        date=meta.get("date"),
        note=", ".join(bits) or None,
    )


def tbench_page_rows(html: str) -> list[BoardRow]:
    """Every row of the official Terminal-Bench 4.0 board in *html*."""
    data = tbench_board(html)
    if data is None:
        return []
    rows = [tbench_row(entry) for entry in data["rows"] if isinstance(entry, dict)]
    return [r for r in rows if r is not None]


def fetch_tbench(get=http_get) -> list[BoardRow]:
    """Fetch the official Terminal-Bench 4.0 board."""
    return tbench_page_rows(get(TB_PAGE))


# --------------------------------------------------------------------------
# SWE-bench — the official site embeds its whole board as a JSON island.
# --------------------------------------------------------------------------

SWE_URL = "https://www.swebench.com/"
#: The subsets that are benchmark families; "Test" is their internal subset.
SWE_SUBSETS = ("Verified", "Multilingual", "Multimodal", "Lite")
_SWE_MARKER = 'id="leaderboard-data"'


def swebench_rows(html: str) -> list[BoardRow]:
    """Board rows from swebench.com's embedded leaderboard JSON.

    Only *checked* entries count: the board is submissions plus their
    verification status, and an unchecked submission is a claim. Rows where
    the model itself took system-level actions are kept but noted — an
    os-model run is not the same task as a patch-submitting agent.
    """
    rows: list[BoardRow] = []
    for subset in _island_json(html, _SWE_MARKER):
        name = subset.get("name")
        if name not in SWE_SUBSETS:
            continue
        for entry in subset.get("results") or []:
            score = entry.get("resolved")
            if not entry.get("checked") or not isinstance(score, (int, float)):
                continue
            notes = []
            if entry.get("os_model") or entry.get("os_system"):
                notes.append("os-model/system run")
            rows.append(
                BoardRow(
                    benchmark=f"SWE-Bench {name}",
                    model_name=entry.get("model_display") or "",
                    score=float(score),
                    source=SWE_URL,
                    tier=TIER_BOARD,
                    model_org=entry.get("model_org"),
                    agent=entry.get("agent"),
                    date=entry.get("date"),
                    note=", ".join(notes) or None,
                )
            )
    return rows


def fetch_swebench(get=http_get) -> list[BoardRow]:
    """Fetch swebench.com's official leaderboards."""
    return swebench_rows(get(SWE_URL))


def normalize(text: str) -> Counter:
    """A comparable token multiset: lowercase, punctuation split, counts kept.

    "Opus 5.5" and "opus-5-5" both become ``{"opus": 1, "5": 2}`` — counts
    matter, or 5.5 and 5 are the same model. "Claude 4.6 Opus" and
    "claude-opus-4-6" also meet, despite the word order.
    """
    return Counter(t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t)


def _id_tokens(model_id: str) -> Counter:
    return normalize(model_id.replace("/", " "))


#: opencode marks the canonical row of a model with "(latest)" and keeps the
#: dated snapshot beside it. The marker says nothing about which model it is.
LATEST = re.compile(r"\blatest\b", re.I)


def _name_tokens(model) -> Counter:
    """A model's display name as comparable tokens, marker dropped."""
    return normalize(LATEST.sub(" ", model.name))


def match_models(row: BoardRow, models) -> list[str]:
    """Every catalog row the board row is about.

    Boards and the catalog name the same models differently, so this is a
    mapping rather than a comparison, in two steps.

    First the catalog's own naming: a row whose display name says the same
    thing, once the "(latest)" marker is dropped. That keeps "Claude 4
    Sonnet" on Claude Sonnet 4 instead of reaching for 4.6, and recognises
    ``claude-sonnet-4-0`` and ``claude-sonnet-4-20250514`` as the one model
    they are — ids that share no shape.

    Failing that, tokens shared with the id, where the id's prefix is the
    provider and is compared against the row's org: a lab that matches beats
    one that does not, even at the cost of an extra token.

    More than one result means the catalog holds the same model twice, which
    is not ambiguity. Empty means the candidates are genuinely different
    models — a different version, or the same name under a different
    provider. A wrong merge is worse than a missing one.
    """
    want = normalize(row.model_name)
    if not want:
        return []
    _CATALOG[0] = list(models)

    # a decision someone already made for this service wins outright
    recorded = mapped_id(row.source, row.model_name)
    if recorded:
        hit = [m for m in models if m.model_id == recorded]
        if hit:
            return _all_rows_of(hit)

    org = normalize(row.model_org or "")

    by_name = [m for m in models if _name_tokens(m) == want]
    if by_name:
        return _one_model(by_name)

    scored: list[tuple[int, int, object]] = []
    for model in models:
        tokens = _id_tokens(model.model_id) | _name_tokens(model)
        if want - tokens:  # a token (or count) the model lacks
            continue
        lab_miss = 0 if org and (org - normalize(model.lab)) == Counter() else 1
        scored.append((len(tokens - want), lab_miss, model))
    if not scored:
        return []
    scored.sort(key=lambda t: (t[0], t[1]))
    best = scored[0][:2]
    return _one_model([m for extra, lab, m in scored if (extra, lab) == best])


def _all_rows_of(model: list) -> list[str]:
    """Every catalog row of the model, not just the canonical one.

    A recorded mapping names the canonical id; the dated snapshot beside it
    is the same model and gets the same score.
    """
    if not model:
        return []
    target = model[0]
    same = [
        m
        for m in _CATALOG[0]
        if m.lab == target.lab and _name_tokens(m) == _name_tokens(target)
    ]
    return sorted(
        (m.model_id for m in same or model),
        key=lambda c: (0 if LATEST.search(c) else 1, c),
    )


#: the catalog currently being matched against, so _all_rows_of can find the
#: snapshot rows beside the one a mapping names
_CATALOG: list[list] = [[]]


def _one_model(candidates: list) -> list[str]:
    """The ids of *candidates* when they are one model, else nothing.

    They must agree on provider and on name — the prefix is what tells
    ``anthropic/claude-sonnet-5-5`` from ``otherproxy/claude-sonnet-5-5``.
    The "(latest)" row sorts first: it is the canonical id, and the one an
    override file is named after.
    """
    if len({c.lab for c in candidates}) > 1:
        return []
    # Counter is unhashable, so compare the token sets as sorted items
    if len({tuple(sorted(_name_tokens(c).items())) for c in candidates}) > 1:
        return []
    return sorted(
        (c.model_id for c in candidates),
        key=lambda c: (0 if LATEST.search(c) else 1, c),
    )


def match_model(row: BoardRow, models) -> str | None:
    """The one catalog model a board row is about, or None when ambiguous.

    See :func:`match_models`, which returns every row when the catalog holds
    the same model twice.
    """
    found = match_models(row, models)
    return found[0] if found else None


def merge_boards(models, rows: list[BoardRow]):
    """Apply official board rows onto models, preferring the better source.

    A board row replaces a model's score only when its source is *strictly*
    better tiered than what the model already carries: an official board
    beats a vendor's own number (Opus 5.5's claimed 66.4 on TB 4.0 yields to
    the board's 64.8), but it does not displace an independent run. Every
    disagreement is returned as a conflict so `conflicts` can show it.

    Returns the models and the conflicts; models with no board row are
    returned unchanged.
    """
    from .overrides import Conflict, source_tier

    by_model: dict[str, list[BoardRow]] = {}
    for row in rows:
        # every row of the model, not one of them: the catalog keeps a dated
        # snapshot beside the canonical row and a board does not know that
        for model_id in match_models(row, models):
            by_model.setdefault(model_id, []).append(row)

    conflicts: list[Conflict] = []
    out = []
    for model in models:
        rows_for_model = by_model.get(model.model_id)
        if not rows_for_model:
            out.append(model)
            continue
        scores = dict(model.scores)
        sources = dict(model.sources)
        for row in sorted(rows_for_model, key=lambda r: -r.score):
            key = row.benchmark
            # a board row applies only when its source is strictly better
            # tiered than what the model carries — the board never
            # displaces an independent run, and never a same-tier number
            better = key not in scores or source_tier(row.source) < source_tier(
                sources.get(key)
            )
            if key in scores and scores[key] != row.score:
                conflicts.append(
                    replace(
                        Conflict(
                            model_id=model.model_id,
                            benchmark=key,
                            upstream=scores[key],
                            override=row.score,
                            upstream_source=sources.get(key),
                            override_source=row.source,
                            reason=(
                                f"official board ({row.agent or 'board'}"
                                + (f", {row.effort}" if row.effort else "")
                                + (f", {row.note}" if row.note else "")
                                + ")"
                            ),
                        ),
                        applied=better,
                    )
                )
                if not better:
                    continue
            scores[key] = row.score
            sources[key] = row.source
        out.append(replace(model, scores=scores, sources=sources))
    conflicts.sort(key=lambda c: (c.model_id, c.benchmark))
    return out, conflicts


def fetch_boards(get=http_get, progress=None) -> dict[str, list[BoardRow]]:
    """Fetch every board source, keyed by source name.

    A source that fails is skipped, not fatal: the boards are independent
    of each other and of the registry, so one dead source leaves the rest.
    *progress* is an optional ``(done, total, description)`` callback,
    fired before each source is attempted.
    """
    out = {}
    sources = list(SOURCES)
    for done, (name, fetch, description) in enumerate(sources, 1):
        if progress is not None:
            progress(done, len(sources), description)
        try:
            out[name] = fetch(get)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return out


def save_boards(directory, boards: dict[str, list[BoardRow]]) -> list[str]:
    """Write each source's rows to ``<dir>/<name>.json``."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    saved = []
    for name, rows in boards.items():
        path = directory / f"{name}.json"
        path.write_text(json.dumps([asdict(r) for r in rows], indent=1))
        saved.append(path.name)
    return saved


def load_boards(directory) -> list[BoardRow]:
    """Every cached board row from ``<dir>/*.json``; absent is not an error."""
    rows: list[BoardRow] = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for row in payload if isinstance(payload, list) else []:
            try:
                rows.append(BoardRow(**row))
            except TypeError:
                continue
    return rows


#: Every board source, in the order `fetch --boards` walks them.
SOURCES = (
    ("tbench", fetch_tbench, "Terminal-Bench 4.0 board from www.tbench.ai"),
    ("swebench", fetch_swebench, "SWE-bench boards from www.swebench.com"),
)
