"""Command-line interface.

Usage::

    python3 -m opencode_models fetch            # refresh data/ from opencode.ai

Exit codes: 0 success, 1 usage or data error (message on stdout).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import __version__, availability, go_pricing, zen_pricing
from . import arena as arena_mod
from . import sync as sync_mod
from .allowance import WINDOW_FRACTIONS, cost_per_request
from .arena import ArenaCache
from .auth import load_key
from .benchmarks import available
from .boards import fetch_boards, save_boards
from .catalog import curated_layer
from .commands import fail as _fail
from .commands.auth import run as _cmd_auth
from .commands.models import MODELS_ALL_HEADERS, MODELS_HEADERS  # noqa: F401 (test compat)
from .commands.models import run as _cmd_models
from .commands.overrides import run as _cmd_conflicts
from .commands.render import CONFLICT_HEADERS  # noqa: F401 (test compat; moves with fetch)
from .commands.render import bold_head as _bold_head
from .commands.render import column_label as _column_label  # noqa: F401 (test compat)
from .commands.render import print_conflicts as _print_conflicts
from .commands.render import style as _style
from .commands.scores import SCORES_STATIC_HEADERS  # noqa: F401 (test compat)
from .commands.scores import run as _cmd_scores
from .commands.sync import run as _cmd_sync
from .fetch import SITEMAP_URL, http_get, refresh, urls_from_sitemap
from .live import api_get as _api_get
from .live import fetch_live, fetch_served, merge_served, save_live
from .pricing import (
    USAGE_EXPORT_URL,
)
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


#: Usage sections: dollars first, request details after. "Spent" carries
#: its 3-space indent so the label sits over the amounts.
USAGE_LEFT_HEADERS = ("Model", "   Spent", "Limit")
USAGE_RIGHT_HEADERS = ("Reqs", "In", "Out", "Cached", "¢/req", "Est.")


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


#: How often the long model-file walk reports: one line per file would bury
#: the per-source announcements, so every fiftieth file plus the last one.
_FETCH_TICK = 50


def _model_tick(done: int, total: int, name: str) -> None:
    if done % _FETCH_TICK == 0 or done == total:
        print(f"  opencode.ai/data: {done}/{total} model files ...")


def _cmd_fetch(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    key = load_key()
    only: set[str] | None = None
    if args.all_models:
        print("fetching the full catalog (--all)")
    elif key:
        print("fetch live model availability (inference lists + config) ...")
        try:
            served = fetch_served(_api_get(key))
            known = fetch_live(_api_get(key))
        except (OSError, ValueError, KeyError) as exc:
            print(
                f"warning: live model list unreachable ({exc}) — "
                f"downloading the full catalog"
            )
        else:
            saved_live = save_live(data_dir, merge_served(served, known))
            print(f"saved {', '.join(saved_live)} to {data_dir / 'available'}")
            only = set().union(*served.values())
            if not only:
                print("warning: no models served — using last known availability")
                only = None
    if only is None and not args.all_models:
        only = availability.available_ids(data_dir)
    if only is not None:
        print(
            f"{len(only)} models offered — downloading those "
            f"(--all for the full catalog)"
        )
    print("fetch model records from opencode.ai/data ...")
    # One extra sitemap GET per fetch run (vs. refresh doing it internally)
    # is accepted: it lets us distinguish a real fetch failure from an
    # `only` filter that matches nothing, without changing refresh's API.
    try:
        urls_from_sitemap(http_get(SITEMAP_URL))
    except (OSError, ValueError, SyntaxError) as exc:  # sitemap down must not traceback
        print(
            f"warning: model catalog not refreshed ({exc}) — keeping existing records"
        )
    else:
        saved = refresh(data_dir, get=http_get, progress=_model_tick, only=only)
        print(f"saved {len(saved)} model files to {data_dir / 'opencode'}")
    print("fetch plan pricing from opencode.ai docs ...")
    for module, label in ((go_pricing, "go"), (zen_pricing, "zen")):
        try:
            saved = module.refresh(data_dir, get=http_get)
        except (OSError, ValueError) as exc:  # docs down must not fail the fetch
            print(f"warning: {label} pricing docs not refreshed ({exc})")
        else:
            if saved:
                print(f"saved {', '.join(saved)} to {data_dir / 'plans'}")
            else:
                print(
                    "warning: plan pricing docs parsed to nothing, "
                    "keeping existing snapshots"
                )
    print("fetch arena Elo from datasets-server.huggingface.co ...")
    try:
        elo = arena_mod.lookup_arena_elo(get=http_get)
    except (OSError, ValueError) as exc:  # one dead source must not stop the rest
        print(f"warning: arena Elo not refreshed ({exc})")
    else:
        cache = ArenaCache(data_dir / "aiarena")
        cache.save(elo)
        print(
            f"saved {len(elo)} arena Elo scores to "
            f"{data_dir / 'aiarena' / arena_mod.CACHE_NAME}"
        )
    curated, _merge_warnings = curated_layer(data_dir)
    n = _print_conflicts(curated.conflicts, curated.orphans, curated.manual_orphans)
    if n:
        print(
            f"{n} curated override(s) disagree with upstream — "
            f"run `overrides` to review"
        )
    boards = fetch_boards(
        get=http_get, progress=lambda done, total, desc: print(f"fetch {desc} ...")
    )
    for name, result in boards.items():
        if result is None:
            print(f"warning: {name} board not refreshed — keeping existing records")
    saved = save_boards(
        data_dir / "boards", {n: r for n, r in boards.items() if r is not None}
    )
    rows = sum(len(v) for v in boards.values() if v)
    if saved:
        print(
            f"saved {rows} board rows from {', '.join(saved)} to {data_dir / 'boards'}"
        )
    return 0


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
#: credit balance in the header line (optional: person-only, a service
#: key gets a 403 and the balance reads n/a).
GO_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
GO_BILLING_URL = "https://opencode.ai/console/api/billing/status"

#: Append-only poll history under ``<data>/usage/``: one JSON line per
#: successful window poll, pruned to the newest few.
GO_HISTORY_NAME = "go_windows.jsonl"
GO_HISTORY_MAX_LINES = 200

#: Nominal window lengths — the page publishes the fractions (20/50/100%
#: of the monthly limit), not the clock, so a window's spend starts at
#: ``resetsAt`` minus this length. The 720h month is a 30-day
#: approximation of the subscription-anchored month: no API exposes the
#: true billing period, so a 28/31-day month skews the Month LIMIT
#: slightly. The weekly 168h is exact (Monday 00:00 UTC resets).
GO_WINDOW_HOURS = {"rolling": 5.0, "weekly": 168.0, "monthly": 720.0}


def _parse_iso(value):
    """An ISO-8601 timestamp as aware UTC, or None when unparseable."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_text(past: datetime, now: datetime) -> str:
    """How long ago *past* was, coarse: 59m, 23h, then days."""
    seconds = max(0.0, (now - past).total_seconds())
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _poll_reason(exc: BaseException) -> str:
    """Short text for a failed poll: the HTTP code when there is one."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError) and exc.reason:
        return str(exc.reason)
    if isinstance(exc, KeyError):
        return f"missing {exc}"
    return type(exc).__name__


def _fetch_go_usage(get):
    """(windows, error): the three API windows, or why they are absent."""
    try:
        usage = json.loads(get(GO_USAGE_URL))["usage"]
        windows = {}
        for name in WINDOW_FRACTIONS:
            window = dict(usage[name])
            window["percent"]  # KeyError: an incomplete payload degrades
            windows[name] = window
    except (OSError, ValueError, KeyError) as exc:
        return None, _poll_reason(exc)
    return windows, None


def _parse_export_rows(text: str) -> list[dict]:
    """The usage export as numeric rows; malformed lines are skipped.

    Same CSV shape the free-tier refresh reads (pricing.py): one line
    per UTC day x model. ``cost_micro_cents`` is literal micro-cents
    ($ = /1e8) — the scale the tokens x page-price spot-check confirms.
    """
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            rows.append(
                {
                    "day": date.fromisoformat(row["day"]),
                    "provider": (row.get("provider") or "").strip(),
                    "model": (row.get("model") or "").strip(),
                    "requests": int(row.get("requests") or 0),
                    "input_tokens": int(row.get("input_tokens") or 0),
                    "output_tokens": int(row.get("output_tokens") or 0),
                    "cache_read_tokens": int(row.get("cache_read_tokens") or 0),
                    "cache_write_tokens": int(row.get("cache_write_5m_tokens") or 0)
                    + int(row.get("cache_write_1h_tokens") or 0),
                    "cost": int(row.get("cost_micro_cents") or 0) / 1e8,
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def _fetch_go_export(get):
    """(rows, error): parsed trailing-30d export rows, or the failure."""
    try:
        rows = _parse_export_rows(get(USAGE_EXPORT_URL))
    except (OSError, ValueError) as exc:
        return None, _poll_reason(exc)
    return rows, None


def _fetch_go_balance(get):
    """(dollars, error): the Zen credit balance, or why it is absent.

    The route is person-only: a 403 here is expected with a service key
    and the caller degrades to a footnote instead of failing.
    """
    try:
        balance = json.loads(get(GO_BILLING_URL))["balanceMicroCents"]
        return int(balance) / 1e8, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, _poll_reason(exc)


def _usage_history_path(data_dir: Path) -> Path:
    return Path(data_dir) / "usage" / GO_HISTORY_NAME


def _read_usage_history(data_dir: Path) -> list[dict]:
    """Past polls, oldest first; lines that are not usable are skipped."""
    try:
        lines = _usage_history_path(data_dir).read_text().splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if all(window in record for window in WINDOW_FRACTIONS):
            records.append(record)
    return records


def _append_usage_history(data_dir: Path, windows: dict) -> dict:
    """Record one successful poll, pruning the file to the last few."""
    record = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **windows,
    }
    records = (_read_usage_history(data_dir) + [record])[-GO_HISTORY_MAX_LINES:]
    path = _usage_history_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return record


def _window_start(resets_at, window: str, first_day):
    """(first day counted, clamped, true start): where spend starts.

    The export is trailing 30d, so a window that opened before its first
    day is only partially observable — spend counts from the export's
    first day, clamped, and the caller marks it partial in the section
    header rather than silently under-counting.
    """
    resets = _parse_iso(resets_at)
    if resets is None:
        return first_day, first_day is not None, None
    start = (resets - timedelta(hours=GO_WINDOW_HOURS[window])).date()
    if first_day is None or start >= first_day:
        return start, False, start
    return first_day, True, start


def _window_totals(rows: list[dict], start_day) -> dict:
    """``{model: {spend, requests, tokens, rows}}`` for Go traffic since a day.

    Only ``opencode-go`` rows enter the Go allowance: Zen traffic shares
    model ids but draws on the balance, not the plan's dollar windows.
    Token fields (input/cache/output) feed the per-window mix columns.
    """
    totals = {}
    for row in rows:
        if row["provider"] != "opencode-go" or row["day"] < start_day:
            continue
        acc = totals.setdefault(
            row["model"],
            {
                "spend": 0.0,
                "requests": 0,
                "input_tokens": 0,
                "cache_tokens": 0,
                "output_tokens": 0,
                "rows": [],
            },
        )
        acc["spend"] += row["cost"]
        acc["requests"] += row["requests"]
        acc["input_tokens"] += row.get("input_tokens", 0)
        acc["cache_tokens"] += row.get("cache_read_tokens", 0) + row.get(
            "cache_write_tokens", 0
        )
        acc["output_tokens"] += row.get("output_tokens", 0)
        acc["rows"].append(row)
    return totals


def _window_section_header(window: str, resets_at, now, partial: bool = False) -> str:
    """`Week (~3 days since Monday 10-19 → resets 10-26 00:00 UTC)` header.

    The start is ``resetsAt`` minus the nominal window length (the page
    publishes fractions, not the clock); elapsed days are ``now`` minus
    that start. All timestamps are UTC (API, export days and ``now``);
    the reset time carries the UTC marker so it is never read as local.
    Unparseable resets fall back to ``→?``.
    """
    title = "Week" if window == "weekly" else "Month"
    resets = _parse_iso(resets_at)
    reset_text = resets.strftime("%m-%d %H:%M UTC") if resets else "?"
    if resets is None:
        return f"{title} (resets →?)"
    start = resets - timedelta(hours=GO_WINDOW_HOURS[window])
    elapsed = max(0, int((now - start).total_seconds() // 86400))
    if window == "weekly":
        since = f"{start.strftime('%A')} {start.strftime('%m-%d')}"
    else:
        since = start.strftime("%m-%d")
    tail = " · partial" if partial else ""
    return f"{title} (~{elapsed} days since {since} → resets {reset_text}{tail})"


def _window_model_rows(
    models: dict, plan: str, window: str, totals: dict
) -> list[dict]:
    """One row per model with window spend: reqs, token mix, rates, caps.

    Token shares are shares of tokens (in/cached/out over their sum).
    ``cap`` is this model's window cap in dollars (``None`` for free or
    cap-less models); ``use`` is spend over the cap; ``est`` is the
    page-based $/req estimate (``None`` without tiers+assumptions);
    ``off`` is measured avg over the estimate (``None`` when the
    estimate is missing); rows sort by spend descending.
    """
    rows = []
    for slug, acc in totals.items():
        reqs = acc.get("requests", 0)
        spend = acc.get("spend", 0.0)
        if reqs <= 0 and spend <= 0:
            continue
        tin = acc.get("input_tokens", 0)
        tcache = acc.get("cache_tokens", 0)
        tout = acc.get("output_tokens", 0)
        tsum = tin + tcache + tout
        if tsum > 0:
            pin, pcached, pout = (
                100.0 * tin / tsum,
                100.0 * tcache / tsum,
                100.0 * tout / tsum,
            )
        else:
            pin = pcached = pout = 0.0
        rec = models.get(slug, {})
        limit = (rec.get("limits") or {}).get(plan)
        est = cost_per_request(rec.get("tiers") or [], rec.get("assumptions"))
        avg = spend / reqs if reqs else 0.0
        off = avg / est if est else None
        if rec.get("free") or limit is None:
            use = None
            cap = None
        else:
            cap = WINDOW_FRACTIONS[window] * limit
            use = spend / cap if cap else 0.0
        rows.append(
            {
                "slug": slug,
                "reqs": reqs,
                "pin": pin,
                "pcached": pcached,
                "pout": pout,
                "avg": avg,
                "spend": spend,
                "use": use,
                "cap": cap,
                "est": est,
                "off": off,
            }
        )
    rows.sort(key=lambda r: (-r["spend"], -r["reqs"], r["slug"]))
    return rows


def _format_spent(row: dict) -> str:
    """``$cost (pct%)``, left-aligned with sub-$10 amounts space-padded.

    The leading space on amounts under $10 lines the decimals up with
    wider amounts (``$10.80`` vs `` $3.39``); free rows read `` $0.00``.
    """
    if row["use"] is None:
        return f"${row['spend']:.2f}" if row["spend"] >= 10 else f" ${row['spend']:.2f}"
    pct = f" ({row['use'] * 100:.0f}%)"
    if row["spend"] >= 10:
        return f"${row['spend']:.2f}" + pct
    return f" ${row['spend']:.2f}" + pct


def _format_avg(row: dict) -> str:
    """Measured ¢/req, or a dash when nothing metered was spent."""
    if not row["spend"]:
        return "-"
    return f"{row['avg'] * 100:.2f}"


def _format_off(row: dict) -> str:
    """Page-estimated ¢/req, or a dash without tiers+assumptions."""
    est = row["est"]
    if not est:
        return "-"
    return f"{est * 100:.2f}"


def _cmd_usage(args: argparse.Namespace) -> int:
    """Per-window Go allowance view: Week then Month sections."""
    data_dir = Path(args.data)
    plan = "go_plus" if args.go_pro else "go"
    _status, _msgs, _age = sync_mod.refresh_plan(data_dir, "go")
    if _status != "fresh":
        for _m in _msgs:
            print(_m)
    try:
        snapshot, origin = availability.load_plan_snapshot(data_dir)
    except (OSError, ValueError) as exc:
        return _fail(f"plan snapshot unreadable ({exc})")
    models = snapshot.get("models") or {}
    now = datetime.now(timezone.utc)
    history = _read_usage_history(data_dir)
    key = load_key()
    get = _api_get(key) if key else None
    if get:
        usage, usage_error = _fetch_go_usage(get)
    else:
        usage, usage_error = None, "no OPENCODE_API_KEY"
    if usage is not None:
        record = _append_usage_history(data_dir, usage)
    else:
        if not history:
            return _fail("no usage data — set OPENCODE_API_KEY (`auth`) and retry")
        record = history[-1]
        if usage_error is not None:
            print(f"warning: window API unavailable ({usage_error}) — using last poll")
    if get:
        rows_export, export_error = _fetch_go_export(get)
        if export_error is not None:
            print(f"warning: usage export unavailable ({export_error})")
    else:
        rows_export, export_error = None, None
    balance = None
    balance_error = None
    if get:
        balance, balance_error = _fetch_go_balance(get)
        if balance_error is not None:
            print(f"warning: balance unavailable ({balance_error})")
    first_day = min((r["day"] for r in rows_export or []), default=None)
    starts = {
        w: _window_start((record.get(w) or {}).get("resetsAt"), w, first_day)
        for w in ("weekly", "monthly")
    }
    spend = {
        w: _window_totals(rows_export or [], starts[w][0])
        for w in ("weekly", "monthly")
    }

    fetched = _parse_iso((snapshot.get("meta") or {}).get("fetched_at"))
    snapshot_age = _age_text(fetched, now) if fetched else "?"
    label = "Go Plus" if plan == "go_plus" else "Go"
    balance_text = f"${balance:,.2f}" if balance is not None else "n/a"
    print(
        f"{label} plan · snapshot {snapshot_age} old ({origin}) · "
        f"balance {balance_text}"
    )

    # one width set fits both tables so the Week and Month grids line up
    # with each other, the way the list view's tables do
    left_headers = USAGE_LEFT_HEADERS
    right_headers = USAGE_RIGHT_HEADERS
    left_aligns = ("<", "<", ">")
    right_aligns = (">", ">", ">", ">", ">", "<")
    sections = []
    for window in ("weekly", "monthly"):
        rows = _window_model_rows(models, plan, window, spend[window])
        left_rows, right_rows = [], []
        for row in rows:
            if row["use"] is None:
                limit_text = "-"
            else:
                limit_text = f"${row['cap']:.2f}"
            left_rows.append([row["slug"], _format_spent(row), limit_text])
            right_rows.append(
                [
                    f"{row['reqs']:,}",
                    f"{row['pin']:.0f}%",
                    f"{row['pout']:.0f}%",
                    f"{row['pcached']:.0f}%",
                    _format_avg(row),
                    _format_off(row),
                ]
            )
        title = _window_section_header(
            window,
            (record.get(window) or {}).get("resetsAt"),
            now,
            partial=starts[window][1],
        )
        sections.append((f"{title} · {len(left_rows)} models", left_rows, right_rows))
    left_widths = column_widths(
        left_headers, [line for _, left, _ in sections for line in left]
    )
    right_widths = column_widths(
        right_headers, [line for _, _, right in sections for line in right]
    )
    for title, left_rows, right_rows in sections:
        print()
        print(_rule(title))
        print(
            "  "
            + _style(
                format_row(left_headers, left_widths, left_aligns, gap=" ").rstrip()
                + "   "
                + format_row(right_headers, right_widths, right_aligns, gap=" ")
            )
        )
        for left, right in zip(left_rows, right_rows, strict=True):
            print(
                "  "
                + format_row(left, left_widths, left_aligns, gap=" ").rstrip()
                + "   "
                + format_row(right, right_widths, right_aligns, gap=" ")
            )

    print()
    print("¢/req: measured average from reported usage (spend ÷ requests)")
    print("Est.: estimated average ¢/req from opencode.ai page assumptions")

    return 0


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
        "fetch": _cmd_fetch,
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
