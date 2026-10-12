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
from .pricing import load_prices, refresh_prices
from .ranking import RECOMMEND_GRADE, next_up
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
from .ranking import AXES, axis_scores
from .render import render_html
from . import arena as arena_mod
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
#: Two layouts: the default shows every ordering axis's raw score side by
#: side, so a row carries both base scores instead of one merged number and
#: a "which axis" tag. ``--benchmark X`` ranks on one benchmark and shows
#: that single score with its tag.
AXIS_HEADERS = tuple(label for _, label, *_ in AXES) + (
    "¢/1M", "SCORE/$", "PROV")
SINGLE_HEADERS = ("SCORE", "BENCH", "¢/1M", "SCORE/$", "PROV")

#: Every row is prefixed by its rank marker, outside the column grid and
#: without a header of its own: ++ top decile, + next tenth, o the middle,
#: - the bottom fifth of the ordering, · unranked (no score at all). The
#: header line is indented by the same width so the columns still line up.
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
    and `conflicts` can report the boards' numbers replacing self-reports.
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
    for name, count in available(models):
        print(f"{count:4d}  {name}")
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


def _print_conflicts(conflicts, orphans=()) -> int:
    """Override-vs-upstream disagreements, and overrides nothing matched."""
    for c in conflicts:
        verdict = ("override wins"
                   if c.override_wins and c.applied
                   else "override noted, upstream kept")
        print(f"{c.model_id}  {c.benchmark}: "
              f"{c.upstream} [{TIER_LABELS[source_tier(c.upstream_source)]}] "
              f"→ {c.override} [{TIER_LABELS[source_tier(c.override_source)]}]"
              f"  · {verdict}")
        if c.reason:
            print(f"    {c.reason}")
    for model_id in sorted(orphans):
        print(f"{model_id}  override file has no upstream record")
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
    curated = load_curated(data_dir / "opencode", data_dir / "overrides")
    n = _print_conflicts(curated.conflicts, curated.orphans)
    if n:
        print(f"{n} curated override(s) disagree with upstream — "
              f"run `conflicts` to review")
    boards = fetch_boards(get=http_get)
    saved = save_boards(data_dir / "boards", boards)
    rows = sum(len(v) for v in boards.values())
    if saved:
        print(f"saved {rows} board rows from {', '.join(saved)} "
              f"to {data_dir / 'boards'}")
    return 0


def _cmd_scores(args: argparse.Namespace) -> int:
    """Manage the cached arena.ai Elo table.

    Default is offline: read the cache and report its size. ``--refresh``
    is the only online path — it looks up the arena dataset and rewrites
    the cache, so regular runs never hammer any service.
    """
    data_dir = Path(args.data)
    cache = ArenaCache(data_dir / "aiarena")
    if args.refresh:
        scores = arena_mod.lookup_arena_elo(get=http_get)
        cache.save(scores)
        print(f"fetched {len(scores)} arena Elo scores -> "
              f"{data_dir / 'aiarena' / arena_mod.CACHE_NAME}")
    else:
        scores = cache.load()
        print(f"cached arena Elo scores: {len(scores)} (--refresh to look up)")
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
    """The fixed columns of one row, as text.

    *axis* prints the raw score of every ordering axis the row carries —
    Terminal-Bench 2.x, Terminal-Bench 4.0, SWE-Bench — so the viewer sees
    the base scores the ordering is built from. The single-benchmark layout
    instead prints the one score it ranks on plus that benchmark's tag.
    Prices are cents and ratios rounded integers, as elsewhere.
    """
    no_cost = row.free or row.price == 0
    price = "0" if no_cost else f"{round(row.price * 100)}"
    if no_cost:
        value = "free"
    elif row.score is None:
        value = "-"
    else:
        value = f"{round(ratio(row.score, row.price))}"
    if axis:
        scores = tuple("-" if v is None else f"{v:g}"
                       for v in axis_scores(row.model).values())
    else:
        scores = ("-" if row.score is None else f"{row.score:g}",
                  _bench_tag(row.benchmark))
    return scores + (price, value, row.provider)


def _widths(rows, axis: bool = True) -> tuple[int, ...]:
    """Widest cell per column across the whole view, header included."""
    headings = AXIS_HEADERS if axis else SINGLE_HEADERS
    return tuple(
        max([len(heading)] + [len(cells[i]) for cells in map(_cells, rows)])
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


def _rows_for(data_dir: Path, selected: list[str], price: str):
    """Build, merge and dedupe the rows for *selected* providers.

    Shared by `list` and `next` so a recommendation can never disagree with
    the ranking it was drawn from.
    """
    models, curated, _ = _load(data_dir)
    groups, listed, counts, tier_slugs = [], [], {}, {}
    covered: set[str] = set()
    for provider in selected:
        if provider == "-":
            pool = [m for m in models if m.model_id not in covered]
            free_ids = sources.members(models, None)[1]
        else:
            tier_slugs[provider] = sources.load_source(provider)
            pool, free_ids = sources.members(models, tier_slugs[provider])
            counts[provider] = len(pool)
            covered |= {m.model_id for m in pool}
        listed += pool
        groups.append(build_rows(pool, _entries_for(provider, data_dir),
                                 free_ids, axis=price, provider=provider))
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
        data_dir, selected, args.price)
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
        order_note = ("Terminal-Bench first, SWE-Bench and TB 4.0 fill in")
    # the rank marker rides on position in the whole ordering, so a row in
    # the cost or worst-score table still shows how it ranked overall
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
    print("GRADE: ++ top 10% · + next 10% · o middle · - bottom 20% · "
          "· unranked (position in the whole ordering, not per table)")

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

    _section(f"worst score (bottom 30%) · {len(worst)} lines", widths, axis)
    for row in worst:
        _print_row(row, widths, curated.curated, axis)

    _section(f"highest cost (top 30%) · {len(pricey)} lines", widths, axis)
    for row in pricey:
        _print_row(row, widths, curated.curated, axis)
    return 0


#: Why a pick is in the sequence, in the reader's terms.
NEXT_REASONS = {
    "free": "free — costs nothing, so only the score matters",
    "included": "in your subscription — $0 until this model's own monthly "
                "allowance runs out",
    "payg": "billed from the Zen balance",
    "catalog": "in the catalog only — not carried by the zen or go tier",
}


def _next_reason(row) -> str:
    if row.provider not in ("zen", "go"):
        return NEXT_REASONS["catalog"]
    return NEXT_REASONS["included" if row.band == "included"
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
        return _fail(f"no {RECOMMEND_GRADE} model has a benchmark score in "
                     f"{data_dir} — nothing to recommend yet")

    width = max(len(r.name) for r in picks)
    print(f"next {len(picks)} to use, in order · free first by score, then "
          f"best score per ¢ · Zen and Go alternated where both can supply one")
    print(f"eligible: {RECOMMEND_GRADE} only — the top decile of the same "
          f"ordering the list view shows")
    print()
    print(f"  {'#':<2} {'PLAN':<5} {'SCORE':>6} {'¢/1M':>5} {'SCORE/$':>7}  "
          f"{'MODEL':<{width}}  WHY")
    for i, row in enumerate(picks, 1):
        cells = _cells(row)
        price, value = cells[-3], cells[-2]   # _cells ends (price, value, prov)
        print(f"  {i:<2} {row.provider:<5} {row.score:>6g} {price:>5} "
              f"{value:>7}  {row.name:<{width}}  {_next_reason(row)}")
    print()
    print("free models are rate-limited by a cap OpenCode does not publish, "
          "so free is not unlimited;")
    print("included models cost $0 until their own allowance runs out, then "
          "draw the Zen balance if that is enabled;")
    print("this ignores how much of each allowance is left — see LIMITS.md.")
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
    sub.add_parser("conflicts",
                   help="curated overrides disagreeing with upstream")

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
    p_scores.add_argument("--refresh", action="store_true",
                          help="look up arena.ai and rewrite the cache")

    args = parser.parse_args(argv)
    return {
        "fetch": _cmd_fetch,
        "conflicts": _cmd_conflicts,
        "benchmarks": _cmd_benchmarks,
        "render": _cmd_render,
        "list": _cmd_list,
        "next": _cmd_next,
        "scores": _cmd_scores,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
