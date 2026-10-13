"""Command-line interface.

Usage::

    python3 -m opencode_rank fetch            # refresh data/ from opencode.ai
    python3 -m opencode_rank benchmarks       # list benchmarks and coverage
    python3 -m opencode_rank render [...]     # write the Pareto HTML report

Exit codes: 0 success, 1 usage or data error (message on stdout).
"""

from __future__ import annotations

import argparse
import os
import urllib.request
import sys
from dataclasses import replace
from pathlib import Path

from .arena import ARENA_BENCHMARK, ArenaCache, merge_from_file
from .benchmarks import available
from .data import load_models
from .fetch import http_get, refresh
from .boards import (fetch_boards, load_boards, merge_boards,
                    save_boards)
from .overrides import TIER_LABELS, load_curated, off_band_models, source_tier
from .pricing import (FREE_TIER_NOMINAL_RATES, FREE_TIER_SNAPSHOT_NAME,
                     load_free_nominals, load_prices, refresh_free_nominals,
                     refresh_prices)
from .ranking import RECOMMEND_FLOOR, grade_for, next_up, plans_of
from .scoring import (MIN_AXES, _TRACKED, _folded, canonical,
                     combined, derived_scores, scales, tracked)
from .ranking import (
    BAND_FREE,
    BAND_INCLUDED,
    BAND_PAYG,
    build_rows,
    dedupe_providers,
    grade_rows,
    merge_rows,
    order_rows,
    partition_rows,
    ratio,
)
from .render import render_html
from . import arena as arena_mod
from .availability import available_ids, load_available, offered
from . import sources

#: Where the data lives for an installed tool: $OPENCODE_RANK_DATA, else the
#: XDG user-data dir (~/.local/share/opencode-rank). `fetch` populates it and
#: every other command reads it; a repo checkout passes --data ./data.
DATA_ENV = "OPENCODE_RANK_DATA"


def default_data_dir() -> Path:
    """The user data directory: $OPENCODE_RANK_DATA, else XDG."""
    env = os.environ.get(DATA_ENV)
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "opencode-rank"


DEFAULT_DATA = default_data_dir()
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "pareto.html"

#: Representative cost: 20% input, 60% cached read, 20% output.
COST_NOTE = "cost 20% in / 60% cached / 20% out"
BAND_LABELS = (
    (BAND_FREE, "free — use first"),
    (BAND_INCLUDED, "included usage"),
    (BAND_PAYG, "pay-as-you-go"),
)
#: Fixed columns, right-aligned; the model name takes the rest of the line.
#: Costs count cents ($/100) so a cheap line is a readable number.
#:
#: Two layouts, both built around the one score: the default shows the
#: model's score beside its cost and plan, and ``--benchmark X`` ranks on
#: one benchmark and adds that benchmark's tag. The per-axis raw numbers
#: live in `scores`, not here.
AXIS_HEADERS = ("SCORE", "¢/1M", "SCORE/$", "PROV")
SINGLE_HEADERS = ("SCORE", "BENCH", "¢/1M", "SCORE/$", "PROV")

#: Every row is prefixed by its rank marker, outside the column grid and
#: without a header of its own: ++ at 90+, + at 75+, o at 60+, - at 50+,
#: -- below that, · unranked (no score at all). The header line is indented
#: by the same width so the columns still line up.
GRADE_WIDTH = 5
RULE_WIDTH = 60


def _style(text: str) -> str:
    """Bold when talking to a terminal, plain when piped or captured."""
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def _row_line(cells: tuple[str, ...], widths: tuple[int, ...]) -> str:
    return "  ".join(cell.rjust(width) for cell, width in zip(cells, widths))


def _header(widths: tuple[int, ...], axis: bool = True) -> str:
    headings = AXIS_HEADERS if axis else SINGLE_HEADERS
    return _style(_row_line(headings, widths) + "  MODEL")


def _section(title: str, widths: tuple[int, ...],
             axis: bool = True) -> None:
    """Open a table: blank line, rule-wrapped title, highlighted header."""
    print()
    print(f"── {title} " + "─" * max(4, RULE_WIDTH - len(title)))
    print(" " * GRADE_WIDTH + _header(widths, axis))


def _fail(msg: str) -> int:
    print(msg)
    return 1


def _load(data_dir: Path):
    """Upstream records, arena scores, curated overlays and official boards.

    Returns the models, the curated layer (which models carry overrides), and
    the board-vs-registry disagreements — so the console can mark curated rows
    and `overrides` can report the boards' numbers replacing self-reports.
    """
    curated = load_curated(data_dir / "opencode", data_dir / "overrides")
    models = merge_from_file(curated.models, data_dir / "aiarena")
    models, board_conflicts = merge_boards(models, load_boards(data_dir / "boards"))
    return models, curated, board_conflicts


def _cmd_benchmarks(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")
    models, _, _ = _load(data_dir)
    # marked, because this listing is where a new benchmark's case is argued
    # from: coverage is what earns a place in the scoring equation
    for name, count in available(models):
        print(f"{count:4d}  {'scored' if tracked(name) else '      '}  {name}")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")
    models, _, _ = _load(data_dir)
    choices = available(models)
    if not choices:
        return _fail(f"no benchmark scores found in {data_dir} — run `fetch`")
    benchmark = args.benchmark or choices[0][0]
    if benchmark not in dict(choices):
        listing = ", ".join(f"{n} ({c})" for n, c in choices[:10])
        return _fail(
            f"unknown benchmark {benchmark!r} — available: {listing}"
        )
    out = Path(args.out)
    out.write_text(render_html(models, benchmark))
    print(f"wrote {out} ({benchmark}, {len(models)} models scanned)")
    return 0


#: How much of a board row's provenance the NOTE column carries.
_NOTE_WIDTH = 52

#: Source tiers, short enough to sit beside a number without dominating the
#: column. The long names are in the footer rather than in every row.
_TIER_SHORT = {1: "indep", 2: "board", 3: "self"}


def _tier_word(source) -> str:
    return _TIER_SHORT[source_tier(source)]


def _note_text(c) -> str:
    """A conflict's provenance, trimmed to one line.

    The board rows carry the run that produced the number - agent, effort,
    rank, CI and trial count - and that is the part worth reading, so it
    goes in the table rather than on a second line under it. A longer note
    is cut: the full record is in ``data/boards/``.
    """
    text = " ".join((c.reason or "").split())
    return text if len(text) <= _NOTE_WIDTH else text[:_NOTE_WIDTH - 1] + "…"


def _print_conflicts(conflicts, orphans=()) -> int:
    """Disagreements between two sources, as a table with the value used.

    Three numbers per row would be one too many, so the two reported values
    carry their source tier and USED says which of them is in the view.
    The arrow used to run upstream → override, which read as if the console
    had taken the second number even where it kept the first.
    """
    rows = []
    for c in conflicts:
        rows.append((
            c.model_id, c.benchmark,
            f"{c.upstream:g} {_tier_word(c.upstream_source)}",
            f"{c.override:g} {_tier_word(c.override_source)}",
            f"{c.used:g}", _note_text(c)))
    orphans = [(model_id, "", "", "", "", "override file has no upstream record")
               for model_id in sorted(orphans)]
    rows += orphans
    if not rows:
        return 0
    headings = ("MODEL", "BENCHMARK", "CATALOG", "REPORTED", "USED", "NOTE")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headings)]
    print("  " + "  ".join(h.ljust(w) for h, w in zip(headings, widths)).rstrip())
    for row in rows:
        print("  " + "  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())
    return len(conflicts) + len(orphans)


def _cmd_conflicts(args: argparse.Namespace) -> int:
    """Curated overrides that disagree with upstream, ranked by provenance.

    Each disagreement shows both values with the tier of their source, and
    whether the override applies (its source is at least as good as
    upstream's) or is merely recorded. To accept upstream, delete the
    entry in data/overrides/.
    """
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")
    models, curated, board_conflicts = _load(data_dir)
    n = _print_conflicts(curated.conflicts, curated.orphans)
    if board_conflicts:
        print("— official boards —")
        _print_conflicts(board_conflicts)
    for mid, s21, s40, resid in off_band_models(curated.models):
        print(f"{mid}  Terminal-Bench 2.1 {s21} vs 4.0 {s40} — "
              f"{resid:+.0f} off the 2.1→4.0 band, check the run")
    print(f"{len(curated.curated)} curated models · {n} curated "
          f"disagreements · {len(board_conflicts)} from official boards")
    print("tiers: self = vendor self-report · board = official board · "
          "indep = independent evaluator")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    saved = refresh(data_dir, get=http_get)
    print(f"saved {len(saved)} model files to {data_dir / 'opencode'}")
    try:
        prices = refresh_prices(data_dir / "tier_prices", get=http_get)
    except (OSError, ValueError) as exc:  # docs down must not fail the fetch
        print(f"warning: tier pricing docs not refreshed ({exc})")
    else:
        if prices:
            print(f"saved {', '.join(prices)} to {data_dir / 'tier_prices'}")
        else:
            print("warning: tier pricing docs parsed to nothing, "
                  "keeping existing snapshots")
    try:
        elo = arena_mod.lookup_arena_elo(get=http_get)
    except (OSError, ValueError) as exc:  # one dead source must not stop the rest
        print(f"warning: arena Elo not refreshed ({exc})")
    else:
        cache = ArenaCache(data_dir / "aiarena")
        cache.save(elo)
        print(f"saved {len(elo)} arena Elo scores to "
              f"{data_dir / 'aiarena' / arena_mod.CACHE_NAME}")
    key = os.environ.get("OPENCODE_API_KEY")
    if not key:
        print("free-tier nominals not refreshed (no OPENCODE_API_KEY) — "
              "keeping the existing snapshot")
    else:
        def _usage_get(url: str) -> str:
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {key}",
                              "User-Agent": "opencode-rank/0.1"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")

        try:
            free_slugs = {e["model_id"] for prices in
                          load_prices(data_dir / "tier_prices").values()
                          for e in prices if e.free and e.model_id}
            nominals = refresh_free_nominals(data_dir / "usage",
                                             data_dir / "opencode",
                                             _usage_get, free_slugs)
        except (OSError, ValueError) as exc:
            print(f"warning: free-tier nominals not refreshed ({exc})")
        else:
            print(f"saved {len(nominals)} free-tier nominals to "
                  f"{data_dir / 'usage' / FREE_TIER_SNAPSHOT_NAME}"
                  if nominals is not None else
                  "warning: free-tier nominals not refreshed "
                  "(anchor model absent from the export)")
    curated = load_curated(data_dir / "opencode", data_dir / "overrides")
    n = _print_conflicts(curated.conflicts, curated.orphans)
    if n:
        print(f"{n} curated override(s) disagree with upstream — "
              f"run `overrides` to review")
    boards = fetch_boards(get=http_get)
    saved = save_boards(data_dir / "boards", boards)
    rows = sum(len(v) for v in boards.values())
    if saved:
        print(f"saved {rows} board rows from {', '.join(saved)} "
              f"to {data_dir / 'boards'}")
    return 0


def _column_label(name: str) -> str:
    """The short name a benchmark column carries: "Terminal Bench 2.1" → "TB 2.1".

    Column headers have to fit beside each other, and there are enough
    benchmarks to score on that the full names run past any terminal width.
    """
    if name == "LMArena Elo":
        return "ARENA"
    for family, short in (("Terminal Bench", "TB"), ("SWE Bench", "SWE")):
        if name.startswith(family):
            return short + name[len(family):]
    return name


def _score_columns(models, table: dict) -> list[tuple[str, str, str]]:
    """The benchmarks worth a column, as ``(key, header, full name)``.

    One column per benchmark we score on, in the order ``scoring._TRACKED``
    lists them — the axis the console ranks on first — and only where the
    catalog actually has numbers. Two registries spelling one benchmark
    differently share a column.
    """
    spellings: dict[str, set[str]] = {}
    for model in models:
        for name in derived_scores(model, table):
            spellings.setdefault(canonical(name), set()).add(name)
    columns = []
    for key in _TRACKED:
        folded = _folded(key)
        if folded not in spellings:
            continue
        # prefer the hyphenated spelling for the full name: it is how the
        # benchmark is written everywhere else, including in the README
        name = sorted(spellings[folded],
                      key=lambda n: (n.count("-") == 0, n))[0]
        columns.append((folded, _column_label(folded), name))
    return columns


def _score_cells(model, table: dict, columns: list[tuple[str, str, str]]) -> list[str]:
    """One cell per column: each raw number with where it lands, best first.

    The raw number is what the evaluator published; the derived number is
    where that sits on the benchmark's 0-100 scale across this catalog.
    Both are shown, because a score without its position in the field says
    nothing on its own. A column the model was not measured on is blank
    rather than zero — zero would read as "worst in the catalog".
    """
    derived = derived_scores(model, table)
    cells = []
    for key, _label, _name in columns:
        parts = sorted(((name, derived[name]) for name in derived
                        if canonical(name) == key),
                       key=lambda part: (-part[1], part[0]))
        cells.append(" ".join(f"{model.scores[n]:g}→{d:.0f}" for n, d in parts))
    return cells


def _cmd_scores(args: argparse.Namespace) -> int:
    """Every model's score, one column per benchmark we score on.

    Each cell holds the raw number and where it lands on that benchmark's
    0-100 scale, because the raw numbers do not compare across benchmarks.
    How a model's score is derived from those cells is documented rather
    than printed: see the README's "How a model's score is computed" and
    ``scoring.py``. This command only reads: `fetch` owns every network call,
    including the arena Elo cache.
    """
    data_dir = Path(args.data)
    models, _curated, _boards = _load(data_dir)
    table = scales(models)
    on_offer = available_ids(data_dir)
    shown = (models if getattr(args, "all_models", False) or not on_offer
             else [m for m in models if offered(m.model_id, on_offer)])
    ranked = sorted((m for m in shown if m.scores),
                    key=lambda m: -(combined(derived_scores(m, table)) or 0))
    unscored = [m for m in shown if not m.scores]
    columns = _score_columns(models, table)

    # four counts, because "models with scores" used to mean "models with
    # any benchmark" - which overstated the scored field by a third - and
    # "on one benchmark" used to fold in models with no scored benchmark
    # at all
    counts = {}
    for m in ranked:
        counts.setdefault(len(derived_scores(m, table)), []).append(m)
    scored = [m for m in ranked if combined(derived_scores(m, table)) is not None]
    one = counts.get(1, [])
    zerod = [m for m in ranked if not derived_scores(m, table)]
    print(f"{len(models)} models · {len(columns)} benchmarks scored on · "
          f"{len(scored)} with scores · {len(one)} on one benchmark · "
          f"{len(zerod)} with none scored · {len(unscored)} without any")
    print()
    width = max([len(m.name) for m in models] or [4])
    cells = [_score_cells(m, table, columns) for m in ranked]
    # the same markers as `list`, over the models shown here: rank position
    # in this ordering, · where there is no score to rank
    marks = {id(m): grade_for(combined(derived_scores(m, table)))
             for m in ranked}
    widths = [max([len(label)] + [len(row[i]) for row in cells if row[i]] or [0])
              for i, (_key, label, _n) in enumerate(columns)]
    print("GRADE: ++ ≥90 · + ≥75 · o 60–75 · - 50–60 · -- <50 · "
          "· unranked")
    print((f"  {'MODEL':<{width}} {'GRADE':>{GRADE_WIDTH}} {'SCORE':>6} "
           f"{'AXES':>5}  "
           + "  ".join(f"{label:<{w}}" for (_k, label, _n), w
                       in zip(columns, widths))).rstrip())
    for model, row in zip(ranked, cells):
        derived = derived_scores(model, table)
        score = combined(derived)
        print((f"  {model.name:<{width}} "
               f"{marks.get(id(model), '·'):>{GRADE_WIDTH}} "
               f"{(f'{score:.0f}' if score is not None else '—'):>6} "
               f"{len(derived):>5}  "
               + "  ".join(f"{cell:<{w}}" for cell, w in zip(row, widths))
               ).rstrip())
    legend = [f"{label} = {name}" for _key, label, name in columns
              if label != name]
    if legend:
        print()
        print("  " + " · ".join(legend))
    if unscored:
        print(f"  {len(unscored)} with no benchmarks: "
              + ", ".join(sorted(m.name for m in unscored)[:10]))
    print("  how a score is derived: README, "
          "\"How a model's score is computed\"")
    return 0


def _entries_for(source: str, data_dir: Path):
    """Documented pricing lines for a tier (Go first: interchangeable, and
    its rates are the ones with included usage attached)."""
    prices = load_prices(data_dir / "tier_prices")
    if source in prices:
        return prices[source]
    if source == "all":
        return prices.get("go", []) + prices.get("zen", [])
    return []


def _bench_tag(benchmark: str | None) -> str:
    if not benchmark:
        return "-"
    folded = benchmark.lower().replace(" ", "-")
    if folded.startswith("terminal-bench-4"):
        return "TB4"                     # harder scale, ranked on its own
    if folded.startswith("terminal-bench"):
        return "TB"
    if folded.startswith("swe-bench"):
        return "SWE"
    return benchmark[:5]


def _cells(row, axis: bool = True) -> tuple[str, ...]:
    """The fixed columns of one row, as text: score, cost, value, plan.

    The score is the model's combined 0-100 number, rounded for the column;
    the value is score per dollar ("free" where there is no cost, "-" with
    no score) - the best-value table orders on it, so the column that
    explains the order stays printed. *axis* is kept for the
    single-benchmark layout, which adds that benchmark's tag. Prices are
    cents, as elsewhere.
    """
    no_cost = row.free or row.price == 0
    price = "0" if no_cost else f"{round(row.price * 100)}"
    score = "-" if row.score is None else f"{round(row.score)}"
    if axis:
        return (score, price, _pick_value(row), row.provider)
    return (score, _bench_tag(row.benchmark), price, _pick_value(row),
            row.provider)


def _widths(rows, axis: bool = True) -> tuple[int, ...]:
    """Widest cell per column across the whole view, header included."""
    headings = AXIS_HEADERS if axis else SINGLE_HEADERS
    return tuple(
        max([len(heading)] + [len(cells[i])
                              for cells in (_cells(r, axis) for r in rows)])
        for i, heading in enumerate(headings)
    )


def _print_row(row, widths: tuple[int, ...], curated: set[str] = frozenset(),
               axis: bool = True) -> None:
    name = f"{row.name} *" if row.model.model_id in curated else row.name
    print(f"{row.grade:>{GRADE_WIDTH - 1}} "
          + _row_line(_cells(row, axis), widths) + f"  {name}")


def _select(data_dir: Path, source: str | None,
            price: str = "weighted"):
    """Which providers a view covers, or None if the name is not one.

    Providers are zen and go, plus ``-`` for catalog models in neither tier.
    """
    tiers = sources.available_sources()
    if source is None:
        return list(tiers), "tiers"                   # both, merged
    if source == "all":
        return list(tiers) + ["-"], "all"
    if source in tiers:
        return [source], "tier"
    return None


def _rows_for(data_dir: Path, selected: list[str], price: str,
              all_models: bool = False):
    """Build, merge and dedupe the rows for *selected* providers.

    Shared by `list` and `next` so a recommendation can never disagree with
    the ranking it was drawn from.

    Unless *all_models*, a row is a model the plans actually offer: the
    provider's own snapshot is the record, and a model it has dropped is
    neither listed nor scored. The scales are still built over the whole
    catalog, so a score does not move when a plan changes what it sells.
    """
    models, curated, _ = _load(data_dir)
    on_offer = available_ids(data_dir)
    # the fetched free-tier nominals win per model; the committed table is
    # only the offline fallback for whatever the snapshot does not cover
    snapshot = load_free_nominals(data_dir)
    free_rates = ({**FREE_TIER_NOMINAL_RATES, **snapshot} if snapshot
                  else None)
    # one scale for the whole catalog: per-tier scales would make the same
    # score mean two different things in the merged view
    table = scales(models)
    groups, listed, counts, tier_slugs = [], [], {}, {}
    covered: set[str] = set()
    for provider in selected:
        if provider == "-":
            pool = [m for m in models if m.model_id not in covered]
            free_ids = sources.members(models, None)[1]
        else:
            tier_slugs[provider] = sources.load_source(provider)
            pool, free_ids = sources.members(models, tier_slugs[provider])
            on_offer = load_available(data_dir).get(provider)
            if not all_models and on_offer is not None:
                pool = [m for m in pool if offered(m.model_id, on_offer)]
                free_ids &= {m.model_id for m in pool}
            counts[provider] = len(pool)
            covered |= {m.model_id for m in pool}
        listed += pool
        groups.append(build_rows(pool, _entries_for(provider, data_dir),
                                 free_ids, axis=price, provider=provider,
                                 table=table, free_rates=free_rates))
    rows = dedupe_providers(merge_rows(*groups))
    return rows, listed, curated, counts, tier_slugs, models


def _cmd_list(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")

    source = args.source
    chosen = _select(data_dir, source)
    if chosen is None:
        return _fail(f"unknown source {source!r} — available: "
                     f"{', '.join(sources.available_sources())}, all")
    selected, scope = chosen

    rows, listed, curated, counts, tier_slugs, models = _rows_for(
        data_dir, selected, args.price, getattr(args, "all_models", False))
    if not listed:
        return _fail(f"no model of {source or 'zen+go'} is in {data_dir} "
                     f"— run `fetch`")
    # one line per (model, variant, score, cost) before the culls see them
    if args.benchmark:
        wanted = args.benchmark
        if wanted not in dict(available(listed)):
            listing = ", ".join(f"{n} ({c})"
                                for n, c in list(available(listed))[:10])
            return _fail(f"unknown benchmark {wanted!r} — available: {listing}")
        rows = [replace(r, score=r.model.scores[wanted], benchmark=wanted)
                for r in rows if wanted in r.model.scores]
        rows.sort(key=lambda r: (-ratio(r.score, r.price), -r.score))
        order_note = f"benchmark {wanted} · sorted by score per $"
    else:
        rows = order_rows(rows)
        order_note = ("ranked by score per $, best value first")
    # the marker reads the score itself, so a row in the cost or
    # worst-score table shows the same grade it earned in best value
    rows = grade_rows(rows)
    # the layout: every axis side by side, or the single ranked benchmark
    axis = not args.benchmark
    if not rows:
        return _fail(f"no {source or 'zen+go'} model in {data_dir} "
                     f"has benchmark scores")

    if scope == "tiers":
        head = (f"zen+go: {counts.get('zen', 0)} zen + "
                f"{counts.get('go', 0)} go models")
    elif scope == "tier":
        head = (f"{source}: {counts[source]}/{len(tier_slugs[source])} "
                f"models in catalog")
    else:
        head = f"all: {len(models)} models in catalog"
    print(f"{head} · {len(rows)} lines · {order_note} · {COST_NOTE}")
    if not getattr(args, "all_models", False):
        plans = "Go/Zen plans" if source in (None, "all") else f"{source} plan"
        print(f"Only models available right now on the {plans} — "
              f"full list with --all-models.")
    print("GRADE: ++ ≥90 · + ≥75 · o 60–75 · - 50–60 · -- <50 · "
          "· unranked")

    # the split always happens — the cull is what keeps the worst-scoring
    # and priciest models out of the view — only the reporting is optional
    main, worst, pricey = partition_rows(rows)
    # one set of column widths for the whole view, so the three tables line
    # up with each other too
    widths = _widths(rows, axis)

    _section(f"best value · {len(main)} lines", widths, axis)
    for band, label in BAND_LABELS:
        group = [r for r in main if r.band == band]
        if group:
            print(f"  {label} · {len(group)}")
            for row in group:
                _print_row(row, widths, curated.curated, axis)

    if args.tables == "all":
        _section(f"worst score (bottom 30%) · {len(worst)} lines", widths,
                 axis)
        for row in worst:
            _print_row(row, widths, curated.curated, axis)

        _section(f"highest cost (top 30%) · {len(pricey)} lines", widths,
                 axis)
        for row in pricey:
            _print_row(row, widths, curated.curated, axis)
    return 0


#: How each band is named in the sequence. The rules behind these words —
#: what an allowance costs, when a balance is drawn down, what "free" does
#: not mean — are written down once in LIMITS.md rather than restated on
#: every run.
NEXT_BANDS = {"free": "free", "included": "included", "payg": "payg",
              "catalog": "catalog"}


def _pick_value(row) -> str:
    """A pick's score per dollar: "free" for free lines, "-" with no score."""
    if row.free or row.price == 0:
        return "free"
    if row.score is None:
        return "-"
    return f"{round(ratio(row.score, row.price))}"


def _endpoint(row) -> str:
    """A pick as an API identifier: provider prefix plus the plan-local id.

    Display names are not callable, so the view shows what goes in the
    request instead: ``opencode-go/step-5-preview-free``. The prefix follows
    the plans on the line, preferring Go where both list it; the id is the
    tier's own entry where documented, else the catalog slug. Variants and
    free tags are display concerns and stay out of it.
    """
    plans = plans_of(row)
    prefix = "opencode-go" if "go" in plans else "opencode"
    if row.entry is not None and row.entry.model_id:
        return f"{prefix}/{row.entry.model_id}"
    return f"{prefix}/{row.model.model_id.split('/')[-1]}"


def _next_band(row) -> str:
    # a row sold by both tiers is labelled "go,zen", so membership decides,
    # not equality with a single plan name
    if not {"zen", "go"} & plans_of(row):
        return NEXT_BANDS["catalog"]
    return NEXT_BANDS["included" if row.band == "included"
                      else ("free" if row.free else "payg")]


def _cmd_next(args: argparse.Namespace) -> int:
    """A short ordered sequence: free models by score, then best value."""
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")
    chosen = _select(data_dir, args.source)
    if chosen is None:
        return _fail(f"unknown source {args.source!r} — available: "
                     f"{', '.join(sources.available_sources())}, all")
    rows, listed, _curated, _counts, _slugs, _models = _rows_for(
        data_dir, chosen[0], args.price)
    if not listed:
        return _fail(f"no model of {args.source or 'zen+go'} is in {data_dir} "
                     f"— run `fetch` first")
    picks = next_up(grade_rows(order_rows(rows)), count=args.count)
    if not picks:
        return _fail(f"no model of {args.source or 'zen+go'} scores on "
                     f"{MIN_AXES}+ benchmarks in {data_dir} — nothing to "
                     f"recommend yet")

    width = max(len(_endpoint(r)) for r in picks)
    print(f"Next {len(picks)} recommended models, "
          f">{RECOMMEND_FLOOR:.0%} overall score, sorted by best value/$, "
          f"alternating Zen/Go plans.")
    print()
    print(f"  {'#':<2} {'PLAN':<5} {'SCORE':>6} {'¢/1M':>5} {'SCORE/$':>7}  "
          f"{'ENDPOINT':<{width}}  BAND")
    for i, row in enumerate(picks, 1):
        _score, price, value, _prov = _cells(row)
        print(f"  {i:<2} {row.provider:<5} {row.score:>6g} {price:>5} "
              f"{value:>7}  {_endpoint(row):<{width}}  {_next_band(row)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="opencode-rank",
        description="Price/score Pareto frontier of models from opencode.ai/data",
    )
    parser.add_argument("--data", default=str(DEFAULT_DATA),
                        help="data directory (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fetch", help="refresh data from opencode.ai")
    sub.add_parser("overrides",
                   help="every benchmark number more than one source "
                        "reports, and which value the console keeps")

    sub.add_parser("benchmarks", help="list benchmarks with model coverage")

    p_render = sub.add_parser("render", help="write the HTML report")
    p_render.add_argument("--benchmark",
                          help="benchmark to plot (default: best coverage)")
    p_render.add_argument("--out", default=str(DEFAULT_OUT),
                          help="output HTML path (default: %(default)s)")

    p_list = sub.add_parser(
        "list", help="three tables of Console models across providers "
                     "(zen/go): best value (free/included/pay-as-you-go), "
                     "worst score, highest cost"
    )
    p_list.add_argument("--source",
                        help="provider to list: zen or go (default: both, "
                             "merged) — 'all' adds the catalog models that "
                             "are in neither tier")
    p_list.add_argument("--price", choices=("weighted", "input", "output"),
                        default="weighted",
                        help="cost of a line (default: weighted 20%% in, "
                             "60%% cached, 20%% out)")
    p_list.add_argument("--tables", choices=("best", "all"), default="best",
                        help="best: print only the best-value table. all: "
                             "also print the worst-score and highest-cost "
                             "tables. Either way the cull happens — those "
                             "rows are out of best value (default: %(default)s)")
    p_list.add_argument("--all-models", "--all", dest="all_models",
                        action="store_true",
                        help="include models the plans no longer offer; the "
                             "default shows only what a plan currently lists")
    p_list.add_argument("--benchmark",
                        help="rank on one benchmark instead of combining "
                             "Terminal-Bench 2.x (first) with SWE-Bench and "
                             "Terminal-Bench 4.0")

    p_next = sub.add_parser(
        "next", help="the next few models to use, in order: free by score, "
                     "then best score per dollar"
    )
    p_next.add_argument("--count", type=int, default=4,
                        help="how many to recommend (default: %(default)s)")
    p_next.add_argument("--source", help="provider to draw from: zen or go "
                                         "(default: both, merged)")
    p_next.add_argument("--price", choices=("weighted", "input", "output"),
                        default="weighted",
                        help="cost of a line (default: weighted 20%% in, "
                             "60%% cached, 20%% out)")

    p_scores = sub.add_parser(
        "scores", help="manage cached arena.ai Elo scores (offline unless --refresh)"
    )
    p_scores.add_argument("--all-models", "--all", dest="all_models",
                          action="store_true",
                          help="include models the plans no longer offer; the "
                               "default shows only what a plan currently lists")

    args = parser.parse_args(argv)
    return {
        "fetch": _cmd_fetch,
        "overrides": _cmd_conflicts,
        "benchmarks": _cmd_benchmarks,
        "render": _cmd_render,
        "list": _cmd_list,
        "next": _cmd_next,
        "scores": _cmd_scores,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
