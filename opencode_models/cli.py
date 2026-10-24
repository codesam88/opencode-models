"""Command-line interface.

Usage::

    python3 -m opencode_models fetch            # refresh data/ from opencode.ai

Exit codes: 0 success, 1 usage or data error (message on stdout).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__, availability
from . import sync as sync_mod  # noqa: F401 (test compat: freezes lazy refresh)
from .benchmarks import available
from .commands import fail as _fail
from .commands.auth import run as _cmd_auth
from .commands.fetch import run as _cmd_fetch
from .commands.models import MODELS_ALL_HEADERS, MODELS_HEADERS  # noqa: F401 (test compat)
from .commands.models import run as _cmd_models
from .commands.overrides import run as _cmd_conflicts
from .commands.render import CONFLICT_HEADERS  # noqa: F401 (test compat; moves with fetch)
from .commands.render import bold_head as _bold_head
from .commands.render import column_label as _column_label  # noqa: F401 (test compat)
from .commands.render import print_conflicts as _print_conflicts  # noqa: F401 (test compat)
from .commands.render import style as _style
from .commands.scores import SCORES_STATIC_HEADERS  # noqa: F401 (test compat)
from .commands.scores import run as _cmd_scores
from .commands.sync import run as _cmd_sync
from .commands.usage import (  # noqa: F401 (test compat)  # noqa: F401 (test compat)
    GO_BILLING_URL,
    GO_HISTORY_NAME,
    GO_USAGE_URL,
    USAGE_LEFT_HEADERS,
    USAGE_RIGHT_HEADERS,
)
from .commands.usage import run as _cmd_usage
from .fetch import http_get
from .live import api_get as _api_get  # noqa: F401 (test compat)
from .pricing import USAGE_EXPORT_URL  # noqa: F401 (test compat)
from .ranking import (
    BAND_FREE,
    BAND_INCLUDED,
    BAND_PAYG,
    RECOMMEND_FLOOR,
    by_score_per_price,
    grade_rows,
    next_up,
    order_rows,
    partition_rows,
    plans_of,
    ratio,
)
from .rows import rows_for
from .scoring import (
    MIN_AXES,
)
from .tables import column_widths, format_row, render

#: Where the data lives for an installed tool: $OPENCODE_MODELS_DATA, else the
#: XDG user-data dir (~/.local/share/opencode-models). `fetch` populates it and
#: every other command reads it; a repo checkout passes --data ./data.
DATA_ENV = "OPENCODE_MODELS_DATA"


def default_data_dir() -> Path:
    """The user data directory: $OPENCODE_MODELS_DATA, else XDG."""
    env = os.environ.get(DATA_ENV)
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "opencode-models"


DEFAULT_DATA = default_data_dir()

#: Fixed cost base: 20% input, 60% cached read, 20% output.
FIX_NOTE = "Pricing and score/$ rank based on fixed 20/60/20 tokens per request"
#: `est` cost base: the Go snapshot's page-estimated ¢/req; Zen lines and
#: snapshot-missing models fall back to the fix.
EST_NOTE = (
    "Pricing and score/$ rank based on page-estimated ¢/req from the Go "
    "snapshot (zen + missing fall back to fix)"
)


def _price_note(price: str) -> str:
    """The cost-basis note for the headline line, per `--cost` mode."""
    if price == "est":
        return EST_NOTE
    if price == "input":
        return "Pricing and score/$ rank based on input $/1M rate"
    if price == "output":
        return "Pricing and score/$ rank based on output $/1M rate"
    return FIX_NOTE


BAND_LABELS = (
    (BAND_FREE, "free — use first"),
    (BAND_INCLUDED, "included usage"),
    (BAND_PAYG, "pay-as-you-go"),
)
#: Two layouts, both built around the one score: the default shows the
#: model's score beside its cost and plan, and ``--benchmark X`` ranks on
#: one benchmark and adds that benchmark's tag. The per-axis raw numbers
#: live in `scores`, not here. Header units follow the `--cost` mode
#: (``¢/req`` for fix/est, ``¢/1m`` for input/output); see `_header`.

#: Every row is prefixed by its rank marker, outside the column grid and
#: without a header of its own: ++ at 90+, + at 75+, o at 60+, - at 50+,
#: -- below that, · unranked (no score at all). The header line is indented
#: by the same width so the columns still line up.
GRADE_WIDTH = 5
RULE_WIDTH = 60


def _row_line(cells: tuple[str, ...], widths: tuple[int, ...]) -> str:
    return format_row(cells, widths)


def _cost_unit(price: str) -> str:
    """The cost column's unit: ``fix`` and ``est`` count cents per request.

    ``input`` and ``output`` show raw per-1M-token rates instead.
    """
    return "¢/req" if price in ("fix", "est") else "¢/1m"


def _headings(axis: bool, price: str) -> tuple[str, ...]:
    """Column headings, title-cased; the unit follows the `--cost` mode."""
    unit = _cost_unit(price)
    if axis:
        return ("Score", unit, "Score/$", "Prov")
    return ("Score", "Bench", unit, "Score/$", "Prov")


def _next_headings(price: str) -> tuple[str, ...]:
    """The `next` grid headings; the unit follows the `--cost` mode."""
    return ("#", "Plan", "Score", _cost_unit(price), "Score/$", "Endpoint", "Band")


def _header(widths: tuple[int, ...], axis: bool = True, price: str = "est") -> str:
    """One grid header line, plain text: bolding happens at the print site."""
    return format_row(_headings(axis, price), widths) + "  Model"


def _rule(title: str) -> str:
    """A section title with a closing rule, like the list view tables."""
    return f"── {title} " + "─" * max(4, RULE_WIDTH - len(title))


def _section(
    title: str, widths: tuple[int, ...], axis: bool = True, price: str = "est"
) -> None:
    """Open a table: blank line, rule-wrapped title, then its column header.

    Each ── section is its own table, so it carries its own header below
    the rule; band labels and other intermediate headers live inside the
    table, under that header.
    """
    print()
    print(_rule(title))
    print(" " * GRADE_WIDTH + _style(_header(widths, axis, price)))


def _bench_tag(benchmark: str | None) -> str:
    if not benchmark:
        return "-"
    folded = benchmark.lower().replace(" ", "-")
    if folded.startswith("terminal-bench-4"):
        return "TB4"  # harder scale, ranked on its own
    if folded.startswith("terminal-bench"):
        return "TB"
    if folded.startswith("swe-bench"):
        return "SWE"
    return benchmark[:5]


def _cells(row, axis: bool = True) -> tuple[str, ...]:
    """The fixed columns of one row, as text: score, cost, value, plan.

    The score is the model's combined 0-100 number, rounded for the column;
    the value is score per dollar — free lines are metered at $0 and read
    "-" for score/$, sorting ahead of every cost-based line ("free" only
    where a
    non-free line prices exactly zero, "-" with no score). The best-value
    table orders on it, so the column that explains the order stays
    printed. *axis* is kept for the single-benchmark layout, which adds
    that benchmark's tag.
    Prices are cents, as elsewhere.
    """
    no_cost = row.free or row.price == 0
    price = "0" if no_cost else f"{round(row.price * 100)}"
    score = "-" if row.score is None else f"{round(row.score)}"
    if axis:
        return (score, price, _pick_value(row), row.provider)
    return (score, _bench_tag(row.benchmark), price, _pick_value(row), row.provider)


def _widths(rows, axis: bool = True, price: str = "est") -> tuple[int, ...]:
    """Widest cell per column across the whole view, header included."""
    return tuple(column_widths(_headings(axis, price), [_cells(r, axis) for r in rows]))


def _print_row(row, widths: tuple[int, ...], axis: bool = True) -> None:
    print(
        f"{row.grade:>{GRADE_WIDTH - 1}} "
        + _row_line(_cells(row, axis), widths)
        + f"  {row.name}"
    )


def _select(data_dir: Path, source: str | None, price: str = "est"):
    """Which providers a view covers, or None if the name is not one.

    Providers are zen and go, plus ``-`` for catalog models in neither tier.
    """
    tiers = ["go", "zen"]
    if source is None:
        return list(tiers), "tiers"  # both, merged
    if source == "all":
        return list(tiers) + ["-"], "all"
    if source in tiers:
        return [source], "tier"
    return None


def _current_offered(
    data_dir: Path,
    bypass: bool = False,
) -> set[str] | None:
    """Model ids served right now, or None for no restriction.

    Thin wrapper over :func:`availability.current_offered` (the availability
    service): it renders the returned warnings and returns the offered ids.
    *bypass* (`--all`) skips every filter.
    """
    result = availability.current_offered(Path(data_dir), bypass=bypass)
    for warning in result.warnings:
        print(warning)
    return result.offered


def _cmd_list(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")

    source = args.source
    chosen = _select(data_dir, source)
    if chosen is None:
        return _fail(f"unknown source {source!r} — available: go, zen, all")
    selected, scope = chosen

    _result = rows_for(
        data_dir, selected, args.cost, getattr(args, "all_models", False)
    )
    for _w in _result.warnings:
        print(_w)
    rows, listed, _curated, counts, tier_slugs, models = (
        _result.rows,
        _result.listed,
        _result.curated,
        _result.counts,
        _result.tier_slugs,
        _result.models,
    )
    if not listed:
        return _fail(f"no model of {source or 'zen+go'} is in {data_dir} — run `fetch`")
    # one line per (model, variant, score, cost) before the culls see them
    if args.benchmark:
        wanted = args.benchmark
        if wanted not in dict(available(listed)):
            listing = ", ".join(f"{n} ({c})" for n, c in list(available(listed))[:10])
            return _fail(f"unknown benchmark {wanted!r} — available: {listing}")
        rows = [
            replace(r, score=r.model.scores[wanted], benchmark=wanted)
            for r in rows
            if wanted in r.model.scores
        ]
        proxies = [
            replace(r.model, price_input=r.price, scores={wanted: r.score})
            for r in rows
        ]
        ranked = by_score_per_price(proxies, wanted, price_field="price_input")
        by_proxy = {id(p): r for p, r in zip(proxies, rows, strict=True)}
        rows = [by_proxy[id(m)] for m in ranked]
        order_note = f"benchmark {wanted} · sorted by score per $"
    else:
        rows = order_rows(rows)
        order_note = "ranked by score per $, best value first"
    # the marker reads the score itself, so a row in the cost or
    # worst-score table shows the same grade it earned in best value
    rows = grade_rows(rows)
    # the layout: every axis side by side, or the single ranked benchmark
    axis = not args.benchmark
    if not rows:
        return _fail(
            f"no {source or 'zen+go'} model in {data_dir} has benchmark scores"
        )

    if scope == "tiers":
        head = f"zen+go: {counts.get('zen', 0)} zen + {counts.get('go', 0)} go models"
    elif scope == "tier":
        head = f"{source}: {counts[source]}/{len(tier_slugs[source])} models in catalog"
    else:
        head = f"all: {len(models)} models in catalog"
    print(f"{head} · {len(rows)} lines · {order_note}")
    print(_price_note(args.cost))
    if not getattr(args, "all_models", False):
        plans = "Go/Zen plans" if source in (None, "all") else f"{source} plan"
        print(
            f"Only models available right now on the {plans} - "
            f"full list with --all-models."
        )
    print("GRADE: ++ ≥90, + ≥75, o 60–75, - 50–60, -- <50, · insufficient data")

    # the split always happens — the cull is what keeps the worst-scoring
    # and priciest models out of the view — only the reporting is optional
    main, worst, pricey = partition_rows(rows)
    # one set of column widths for the whole view, so the three tables line
    # up with each other too
    widths = _widths(rows, axis, args.cost)
    _section(f"best value · {len(main)} lines", widths, axis, args.cost)
    for band, label in BAND_LABELS:
        group = [r for r in main if r.band == band]
        if group:
            print(f"{label} · {len(group)}")
            for row in group:
                _print_row(row, widths, axis)

    if args.tables == "all":
        _section(
            f"worst score (bottom 30%) · {len(worst)} lines", widths, axis, args.cost
        )
        for row in worst:
            _print_row(row, widths, axis)

        _section(
            f"highest cost (top 30%) · {len(pricey)} lines", widths, axis, args.cost
        )
        for row in pricey:
            _print_row(row, widths, axis)
    return 0


#: How each band is named in the sequence. The rules behind these words —
#: what an allowance costs, when a balance is drawn down, what "free" does
#: not mean — are written down once in LIMITS.md rather than restated on
#: every run.
NEXT_BANDS = {
    "free": "free",
    "included": "included",
    "payg": "payg",
    "catalog": "catalog",
}


def _pick_value(row) -> str:
    """A pick's score per dollar, free lines included.

    Free lines are really free (metered $0, throttled separately), so
    they read "-" for score/$ and sort ahead of every cost-based line
    ($0 is cheaper than any price); "-" is also what an unscored line
    reads, told apart by the 0 cost beside it.
    """
    if row.score is None:
        return "-"
    if row.free:
        return "-"
    if row.price == 0:
        return "free"
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
    if row.endpoint_id:
        return f"{prefix}/{row.endpoint_id}"
    if row.entry is not None and row.entry.model_id:
        return f"{prefix}/{row.entry.model_id}"
    return f"{prefix}/{row.model.model_id.split('/')[-1]}"


def _next_band(row) -> str:
    # a row sold by both tiers is labelled "go,zen", so membership decides,
    # not equality with a single plan name
    if not {"zen", "go"} & plans_of(row):
        return NEXT_BANDS["catalog"]
    return NEXT_BANDS[
        "included" if row.band == "included" else ("free" if row.free else "payg")
    ]


#: The `next` grid, declared: one text column per field, measured at
#: print time so a wide provider (`go,zen`) or a long score widens its
#: column instead of shifting the rest of the row. The cost header comes
#: from `_cost_unit` (``¢/req`` for fix/est, ``¢/1m`` for input/output).
NEXT_ALIGNS = ("<", "<", ">", ">", ">", "<", "<")
NEXT_MIN_WIDTHS = (2, 5, 6, 5, 7, 0, 0)


def _next_table(picks: list, price: str = "est") -> str:
    """The picks as an aligned grid: position, plan, score, cost, value,
    endpoint, band. The cost unit follows the `--cost` mode.

    Plain text always: bolding for terminals happens at the `print` call,
    so the returned grid never depends on the caller's tty.
    """
    headers = _next_headings(price)
    rows = []
    for i, row in enumerate(picks, 1):
        _score, cost, value, _prov = _cells(row)
        rows.append(
            [
                str(i),
                row.provider,
                f"{round(row.score)}",
                cost,
                value,
                _endpoint(row),
                _next_band(row),
            ]
        )
    return render(
        headers,
        rows,
        aligns=NEXT_ALIGNS,
        min_widths=NEXT_MIN_WIDTHS,
        indent="  ",
        gap=" ",
    )


def _cmd_next(args: argparse.Namespace) -> int:
    """A short ordered sequence: free and paid picks interleaved band by band."""
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")
    chosen = _select(data_dir, args.source)
    if chosen is None:
        return _fail(f"unknown source {args.source!r} — available: go, zen, all")
    _result = rows_for(
        data_dir, chosen[0], args.cost, getattr(args, "all_models", False)
    )
    for _w in _result.warnings:
        print(_w)
    rows, listed, _curated, _counts, _slugs, _models = (
        _result.rows,
        _result.listed,
        _result.curated,
        _result.counts,
        _result.tier_slugs,
        _result.models,
    )
    if not listed:
        return _fail(
            f"no model of {args.source or 'zen+go'} is in {data_dir} "
            f"— run `fetch` first"
        )
    picks = next_up(grade_rows(order_rows(rows)), count=args.count)
    if not picks:
        return _fail(
            f"no model of {args.source or 'zen+go'} scores on "
            f"{MIN_AXES}+ benchmarks in {data_dir} — nothing to "
            f"recommend yet"
        )

    print(
        f"Next {len(picks)} recommended models, "
        f">{RECOMMEND_FLOOR:.0%} overall score, free and paid picks "
        f"interleaved, alternating Zen/Go plans."
    )
    print()
    print(_bold_head(_next_table(picks, args.cost)))
    return 0


#: `usage`: read-only Go allowance view. Two live sources: the window
#: API (required; the poll history stands in when it fails) and the
#: usage export (drives the per-model Week/Month sections), plus the
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="opencode-models",
        description="Ranked console of AI models: benchmark scores against real per-token prices",
    )
    parser.add_argument(
        "--data",
        default=str(DEFAULT_DATA),
        help="data directory (default: %(default)s)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fetch", help="refresh data from opencode.ai").add_argument(
        "--all",
        dest="all_models",
        action="store_true",
        help="download the full model catalog, not just the models the "
        "plans offer (kept records are never deleted either way)",
    )
    sync_parser = sub.add_parser(
        "sync", help="refresh stale data sources (availability ~4h, plans ~8h)"
    )
    sync_flags = sync_parser.add_mutually_exclusive_group()
    sync_flags.add_argument(
        "--force",
        action="store_true",
        help="refresh every T2 source regardless of age",
    )
    sync_flags.add_argument(
        "--check",
        action="store_true",
        help="validate the local plan snapshots offline; no network, no writes",
    )

    sub.add_parser(
        "models",
        help="the models the plans offer right now, endpoints "
        "and names (--all adds API status); live from the inference "
        "gateway with a key, else the last fetch snapshots",
    ).add_argument(
        "--all",
        dest="all_models",
        action="store_true",
        help="also show known-but-not-served models, marked [ ], plus the "
        "API status column ([x] served, [?] unknown)",
    )
    sub.add_parser(
        "auth",
        help="save the Console service-account key "
        "to an owner-only file (prompted, never "
        "echoed); verifies it live when it can",
    )
    sub.add_parser(
        "overrides",
        help="every benchmark number more than one source "
        "reports, and which value the console keeps",
    )

    p_list = sub.add_parser(
        "list",
        help="three tables of Console models across providers "
        "(zen/go): best value (free/included/pay-as-you-go), "
        "worst score, highest cost",
    )
    p_list.add_argument(
        "--source",
        help="provider to list: zen or go (default: both, "
        "merged) — 'all' adds the catalog models that "
        "are in neither tier",
    )
    p_list.add_argument(
        "--cost",
        choices=("fix", "input", "output", "est"),
        default="est",
        help="cost of a line (default: %(default)s): est page-estimated "
        "¢/req from the Go snapshot, zen + missing fall back to fix; "
        "fix 20%% in, 60%% cached, 20%% out; input/output rate only",
    )
    p_list.add_argument(
        "--tables",
        choices=("best", "all"),
        default="best",
        help="best: print only the best-value table. all: "
        "also print the worst-score and highest-cost "
        "tables. Either way the cull happens — those "
        "rows are out of best value (default: %(default)s)",
    )
    p_list.add_argument(
        "--all-models",
        "--all",
        dest="all_models",
        action="store_true",
        help="include models the plans no longer offer; the "
        "default shows only what a plan currently lists",
    )
    p_list.add_argument(
        "--benchmark",
        help="rank on one benchmark instead of combining "
        "Terminal-Bench 2.x (first) with SWE-Bench and "
        "Terminal-Bench 4.0",
    )

    p_next = sub.add_parser(
        "next",
        help="the next few models to use, in order: free and paid "
        "picks interleaved, best score then best score per dollar",
    )
    p_next.add_argument(
        "--count",
        type=int,
        default=6,
        help="how many to recommend (default: %(default)s)",
    )
    p_next.add_argument(
        "--source", help="provider to draw from: zen or go (default: both, merged)"
    )
    p_next.add_argument(
        "--cost",
        choices=("fix", "input", "output", "est"),
        default="est",
        help="cost of a line (default: %(default)s): est page-estimated "
        "¢/req from the Go snapshot, zen + missing fall back to fix; "
        "fix 20%% in, 60%% cached, 20%% out; input/output rate only",
    )
    p_next.add_argument(
        "--all-models",
        "--all",
        dest="all_models",
        action="store_true",
        help="include models the plans no longer offer; the "
        "default shows only what a plan currently lists",
    )

    sub.add_parser(
        "usage",
        help="Go plan allowance, read-only: API window %% vs export "
        "spend per model against the page snapshot "
        "(needs OPENCODE_API_KEY; degrades per source)",
    ).add_argument(
        "--go-pro",
        dest="go_pro",
        action="store_true",
        help="read Go Plus limits (the $40/mo plan); the default is Go ($10/mo)",
    )

    p_scores = sub.add_parser("scores", help="manage cached arena.ai Elo scores")
    p_scores.add_argument(
        "--all-models",
        "--all",
        dest="all_models",
        action="store_true",
        help="include models the plans no longer offer; the "
        "default shows only what a plan currently lists",
    )

    args = parser.parse_args(argv)
    return {
        "fetch": lambda args: _cmd_fetch(args, get=http_get),
        "sync": lambda args: _cmd_sync(args, get=http_get),
        "models": _cmd_models,
        "auth": _cmd_auth,
        "overrides": _cmd_conflicts,
        "list": _cmd_list,
        "next": _cmd_next,
        "scores": _cmd_scores,
        "usage": _cmd_usage,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
