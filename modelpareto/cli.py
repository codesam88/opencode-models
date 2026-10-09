"""Command-line interface.

Usage::

    python3 -m modelpareto fetch            # refresh data/ from opencode.ai
    python3 -m modelpareto benchmarks       # list benchmarks and coverage
    python3 -m modelpareto render [...]     # write the Pareto HTML report

Exit codes: 0 success, 1 usage or data error (message on stdout).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from .arena import ARENA_BENCHMARK, ArenaCache, merge_from_file
from .benchmarks import available
from .data import load_models
from .fetch import http_get, refresh
from .pricing import load_prices, refresh_prices
from .ranking import (
    BAND_FREE,
    BAND_INCLUDED,
    BAND_PAYG,
    build_rows,
    dedupe_providers,
    merge_rows,
    order_rows,
    partition_rows,
    ratio,
)
from .render import render_html
from . import arena as arena_mod
from . import sources

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "pareto.html"

#: Representative cost: 20% input, 60% cached read, 20% output.
COST_NOTE = "cost 20% in / 60% cached / 20% out"
BAND_LABELS = (
    (BAND_FREE, "free — use first"),
    (BAND_INCLUDED, "included usage"),
    (BAND_PAYG, "pay-as-you-go"),
)
#: Fixed columns, right-aligned; the model name takes the rest of the line.
HEADERS = ("SCORE", "BENCH", "$/1M", "SCORE/$", "PROV")
RULE_WIDTH = 60


def _style(text: str) -> str:
    """Bold when talking to a terminal, plain when piped or captured."""
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def _row_line(cells: tuple[str, ...], widths: tuple[int, ...]) -> str:
    return "  ".join(cell.rjust(width) for cell, width in zip(cells, widths))


def _header(widths: tuple[int, ...]) -> str:
    return _style(_row_line(HEADERS, widths) + "  MODEL")


def _section(title: str, widths: tuple[int, ...]) -> None:
    """Open a table: blank line, rule-wrapped title, highlighted header."""
    print()
    print(f"── {title} " + "─" * max(4, RULE_WIDTH - len(title)))
    print(_header(widths))


def _fail(msg: str) -> int:
    print(msg)
    return 1


def _cmd_benchmarks(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")
    for name, count in available(merge_from_file(load_models(data_dir), data_dir)):
        print(f"{count:4d}  {name}")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")
    models = merge_from_file(load_models(data_dir), data_dir)
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


def _cmd_fetch(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    saved = refresh(data_dir, get=http_get)
    print(f"saved {len(saved)} model files to {data_dir}")
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
    return 0


def _cmd_scores(args: argparse.Namespace) -> int:
    """Manage the cached arena.ai Elo table.

    Default is offline: read the cache and report its size. ``--refresh``
    is the only online path — it looks up the arena dataset and rewrites
    the cache, so regular runs never hammer any service.
    """
    data_dir = Path(args.data)
    cache = ArenaCache(data_dir)
    if args.refresh:
        scores = arena_mod.lookup_arena_elo(get=http_get)
        cache.save(scores)
        print(f"fetched {len(scores)} arena Elo scores -> "
              f"{data_dir / arena_mod.CACHE_NAME}")
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
    if folded.startswith("terminal-bench"):
        return "TB"
    if folded.startswith("swe-bench"):
        return "SWE"
    return benchmark[:5]


def _cells(row) -> tuple[str, str, str, str, str]:
    """The five fixed columns of one row, as text."""
    # a free line is free whatever price it was re-priced to for ranking
    no_cost = row.free or row.price == 0
    price = "0" if no_cost else f"{row.price:g}"
    if no_cost:
        value = "free"
    elif row.score is None:
        value = "-"
    else:
        value = f"{ratio(row.score, row.price):.1f}"
    score = "-" if row.score is None else f"{row.score:g}"
    return score, _bench_tag(row.benchmark), price, value, row.provider


def _widths(rows) -> tuple[int, ...]:
    """Widest cell per column across the whole view, header included."""
    return tuple(
        max([len(heading)] + [len(cells[i]) for cells in map(_cells, rows)])
        for i, heading in enumerate(HEADERS)
    )


def _print_row(row, widths: tuple[int, ...]) -> None:
    print(_row_line(_cells(row), widths) + f"  {row.name}")


def _cmd_list(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")

    # providers: zen and go, plus `-` for catalog models in neither tier
    tiers = sources.available_sources()
    source = args.source
    if source is None:
        selected, scope = list(tiers), "tiers"      # both, merged
    elif source == "all":
        selected, scope = list(tiers) + ["-"], "all"
    elif source in tiers:
        selected, scope = [source], "tier"
    else:
        return _fail(f"unknown source {source!r} — available: "
                     f"{', '.join(tiers)}, all")

    models = merge_from_file(load_models(data_dir), data_dir)
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
                                 free_ids, axis=args.price, provider=provider))
    if not listed:
        return _fail(f"no model of {source or 'zen+go'} is in {data_dir} "
                     f"— run `fetch`")
    # one line per (model, variant, score, cost) before the culls see them
    rows = dedupe_providers(merge_rows(*groups))

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
        order_note = "Terminal-Bench first, SWE-Bench fills in"
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

    main, worst, pricey = partition_rows(rows)
    # one set of column widths for the whole view, so the three tables line
    # up with each other too
    widths = _widths(rows)

    _section(f"best value · {len(main)} lines", widths)
    for band, label in BAND_LABELS:
        group = [r for r in main if r.band == band]
        if group:
            print(f"  {label} · {len(group)}")
            for row in group:
                _print_row(row, widths)

    _section(f"worst score (bottom 30%) · {len(worst)} lines", widths)
    for row in worst:
        _print_row(row, widths)

    _section(f"highest cost (top 30%) · {len(pricey)} lines", widths)
    for row in pricey:
        _print_row(row, widths)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="modelpareto",
        description="Price/score Pareto frontier of models from opencode.ai/data",
    )
    parser.add_argument("--data", default=str(DEFAULT_DATA),
                        help="data directory (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fetch", help="refresh data from opencode.ai")

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
                             "Terminal-Bench (first) with SWE-Bench")

    p_scores = sub.add_parser(
        "scores", help="manage cached arena.ai Elo scores (offline unless --refresh)"
    )
    p_scores.add_argument("--refresh", action="store_true",
                          help="look up arena.ai and rewrite the cache")

    args = parser.parse_args(argv)
    return {
        "fetch": _cmd_fetch,
        "benchmarks": _cmd_benchmarks,
        "render": _cmd_render,
        "list": _cmd_list,
        "scores": _cmd_scores,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
