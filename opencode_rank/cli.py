"""Command-line interface.

Usage::

    python3 -m opencode_rank fetch            # refresh data/ from opencode.ai

Exit codes: 0 success, 1 usage or data error (message on stdout).
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from dataclasses import replace
from pathlib import Path

from . import arena as arena_mod
from . import sources
from .arena import ArenaCache, merge_from_file
from .auth import load_key, save_key
from .availability import available_ids, load_available, offered
from .benchmarks import available
from .boards import fetch_boards, load_boards, merge_boards, save_boards
from .data import load_models
from .fetch import http_get, refresh
from .live import LIVE_CONFIG_URL, fetch_live, save_live
from .overrides import load_curated, off_band_models, source_tier
from .pricing import (
    FREE_TIER_NOMINAL_RATES,
    FREE_TIER_SNAPSHOT_NAME,
    load_free_nominals,
    load_prices,
    refresh_free_nominals,
    refresh_prices,
)
from .ranking import (
    BAND_FREE,
    BAND_INCLUDED,
    BAND_PAYG,
    RECOMMEND_FLOOR,
    build_rows,
    dedupe_providers,
    grade_for,
    grade_rows,
    merge_rows,
    next_up,
    order_rows,
    partition_rows,
    plans_of,
    ratio,
)
from .scoring import (
    _TRACKED,
    MIN_AXES,
    _folded,
    canonical,
    combined,
    derived_scores,
    scales,
)
from .tables import column_widths, format_row, render

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
    return format_row(cells, widths)


def _header(widths: tuple[int, ...], axis: bool = True) -> str:
    headings = AXIS_HEADERS if axis else SINGLE_HEADERS
    return _style(format_row(headings, widths) + "  MODEL")


def _section(title: str, widths: tuple[int, ...], axis: bool = True) -> None:
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
    return text if len(text) <= _NOTE_WIDTH else text[: _NOTE_WIDTH - 1] + "…"


def _print_conflicts(conflicts, orphans=()) -> int:
    """Disagreements between two sources, as a table with the value used.

    Three numbers per row would be one too many, so the two reported values
    carry their source tier and USED says which of them is in the view.
    The arrow used to run upstream → override, which read as if the console
    had taken the second number even where it kept the first.
    """
    rows = []
    for c in conflicts:
        rows.append(
            (
                c.model_id,
                c.benchmark,
                f"{c.upstream:g} {_tier_word(c.upstream_source)}",
                f"{c.override:g} {_tier_word(c.override_source)}",
                f"{c.used:g}",
                _note_text(c),
            )
        )
    orphans = [
        (model_id, "", "", "", "", "override file has no upstream record")
        for model_id in sorted(orphans)
    ]
    rows += orphans
    if not rows:
        return 0
    headings = ("MODEL", "BENCHMARK", "CATALOG", "REPORTED", "USED", "NOTE")
    print(render(headings, rows, aligns="<", indent="  "))
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
        print(
            f"{mid}  Terminal-Bench 2.1 {s21} vs 4.0 {s40} — "
            f"{resid:+.0f} off the 2.1→4.0 band, check the run"
        )
    print(
        f"{len(curated.curated)} curated models · {n} curated "
        f"disagreements · {len(board_conflicts)} from official boards"
    )
    print(
        "tiers: self = vendor self-report · board = official board · "
        "indep = independent evaluator"
    )
    return 0


#: How often the long model-file walk reports: one line per file would bury
#: the per-source announcements, so every fiftieth file plus the last one.
_FETCH_TICK = 50


def _model_tick(done: int, total: int, name: str) -> None:
    if done % _FETCH_TICK == 0 or done == total:
        print(f"  opencode.ai/data: {done}/{total} model files ...")


def _api_get(key: str):
    """Authed transport for the key-gated Console API (30s per request)."""

    def get(url: str) -> str:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "User-Agent": "opencode-rank/0.1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            exc.close()  # see http_get: callers never read error bodies
            raise

    return get


def _cmd_auth(args: argparse.Namespace) -> int:
    """Ask for the Console service-account key and save it owner-only.

    The prompt never echoes, the key is never printed back, and the file
    is created (or tightened) to owner-only access. The saved key is then
    verified against the live config API — a rejection fails the command
    but keeps the file, so an offline first run is not punished.
    """
    import getpass

    if not sys.stdin.isatty():
        return _fail(
            "auth needs an interactive terminal — pipe a key via "
            "OPENCODE_API_KEY instead"
        )
    key = getpass.getpass("OPENCODE_API_KEY: ").strip()
    if not key:
        return _fail("no key entered — nothing saved")
    path = save_key(key)
    print(f"saved to {path} (owner-only)")
    try:
        live = fetch_live(_api_get(key))
    except (OSError, ValueError, KeyError) as exc:
        print(f"warning: key saved but not verified ({exc})")
        return 0
    count = len(set().union(*live.values()))
    print(
        f"verified live: {count} models across "
        f"{len([t for t in live.values() if t])} tiers"
    )
    return 0


def _display_names(data_dir: Path) -> dict[str, str]:
    """Display names by slug or id, from the catalog records on disk.

    Absent catalog, absent names: callers fall back to the slug itself.
    """
    names = {}
    for model in load_models(Path(data_dir) / "opencode"):
        names[model.model_id] = model.name
        names[model.model_id.split("/")[-1]] = model.name
    return names


def _model_name(names: dict[str, str], slug: str) -> str:
    """The catalog's display name for a tier slug, or the slug itself.

    Tier pages list free variants under their own slug
    (``step-5-preview-free``) while the catalog carries the base model,
    so the ``-free`` suffix reaches back to it.
    """
    if slug in names:
        return names[slug]
    if slug.endswith("-free") and slug[: -len("-free")] in names:
        return names[slug[: -len("-free")]]
    return slug


def _cmd_models(args: argparse.Namespace) -> int:
    """The models the plans offer right now, per tier.

    Live from the Console config API when ``OPENCODE_API_KEY`` is set —
    recorded to ``available/`` on the way, so `list` and `next` unlist
    the dropped models immediately. Else the snapshots the last ``fetch``
    left behind, read-only.
    """
    data_dir = Path(args.data)
    key = load_key()
    tiers: dict[str, set[str]] | None = None
    if key:
        try:
            tiers = fetch_live(_api_get(key))
        except (OSError, ValueError, KeyError) as exc:
            print(
                f"warning: live config unreachable ({exc}) — showing the last snapshots"
            )
            tiers = None
        else:
            save_live(data_dir, tiers)
            origin = (
                f"live from {LIVE_CONFIG_URL} (recorded to {data_dir / 'available'})"
            )
    if tiers is None:
        known = load_available(data_dir)
        if all(ids is None for ids in known.values()):
            return _fail(
                f"no availability known in {data_dir} — run `fetch` "
                f"first (with OPENCODE_API_KEY for the live set)"
            )
        tiers = {t: ids for t, ids in known.items() if ids is not None}
        origin = f"snapshots in {data_dir} (run `fetch` to refresh)"
        unknown = sorted(t for t, ids in known.items() if ids is None)
    else:
        unknown = []
    names = _display_names(data_dir)
    by_slug: dict[str, str] = {}
    for tier in sorted(tiers):
        for slug in sorted(tiers[tier]):
            if slug not in by_slug or tier == "go":
                by_slug[slug] = tier
    rows = []
    for slug, tier in by_slug.items():
        endpoint = f"opencode-go/{slug}" if tier == "go" else f"opencode/{slug}"
        name = _model_name(names, slug)
        # the endpoint already carries the slug: a missing display name
        # reads "—" (as a missing score does), not the slug twice
        rows.append((endpoint, name if name != slug else "—"))
    rows.sort(key=lambda row: (row[1].lower(), row[0]))
    counts = " + ".join(f"{len(tiers[t])} {t}" for t in sorted(tiers))
    print(origin)
    print(
        f"{len(by_slug)} models · {counts}"
        + (f" · {', '.join(unknown)} unknown" if unknown else "")
    )
    print(render(["ENDPOINT", "MODEL"], rows, aligns=("<", "<"), indent="  "))
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    key = load_key()
    only: set[str] | None = None
    if args.all_models:
        print("fetching the full catalog (--all)")
    elif key:
        print(f"fetch live model set from {LIVE_CONFIG_URL} ...")
        try:
            live = fetch_live(_api_get(key))
        except (OSError, ValueError, KeyError) as exc:
            print(
                f"warning: live config unreachable ({exc}) — "
                f"downloading the full catalog"
            )
        else:
            saved_live = save_live(data_dir, live)
            print(f"saved {', '.join(saved_live)} to {data_dir / 'available'}")
            only = set().union(*live.values()) or set()
    if only is None and not args.all_models:
        only = available_ids(data_dir)
    if only is not None:
        print(
            f"{len(only)} models offered — downloading those "
            f"(--all for the full catalog)"
        )
    print("fetch model records from opencode.ai/data ...")
    saved = refresh(data_dir, get=http_get, progress=_model_tick, only=only)
    print(f"saved {len(saved)} model files to {data_dir / 'opencode'}")
    print("fetch tier pricing from opencode.ai/docs ...")
    try:
        prices = refresh_prices(data_dir / "tier_prices", get=http_get)
    except (OSError, ValueError) as exc:  # docs down must not fail the fetch
        print(f"warning: tier pricing docs not refreshed ({exc})")
    else:
        if prices:
            print(f"saved {', '.join(prices)} to {data_dir / 'tier_prices'}")
        else:
            print(
                "warning: tier pricing docs parsed to nothing, "
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
    if not key:
        print(
            "free-tier nominals not refreshed (no OPENCODE_API_KEY) — "
            "keeping the existing snapshot"
        )
    else:
        try:
            free_slugs = {
                e.model_id
                for prices in load_prices(data_dir / "tier_prices").values()
                for e in prices
                if e.free and e.model_id
            }
            print("fetch usage export from opencode.ai/console ...")
            nominals = refresh_free_nominals(
                data_dir / "usage", data_dir / "opencode", _api_get(key), free_slugs
            )
        except (OSError, ValueError) as exc:
            print(f"warning: free-tier nominals not refreshed ({exc})")
        else:
            print(
                f"saved {len(nominals)} free-tier nominals to "
                f"{data_dir / 'usage' / FREE_TIER_SNAPSHOT_NAME}"
                if nominals is not None
                else "warning: free-tier nominals not refreshed "
                "(anchor model absent from the export)"
            )
    curated = load_curated(data_dir / "opencode", data_dir / "overrides")
    n = _print_conflicts(curated.conflicts, curated.orphans)
    if n:
        print(
            f"{n} curated override(s) disagree with upstream — "
            f"run `overrides` to review"
        )
    boards = fetch_boards(
        get=http_get, progress=lambda done, total, desc: print(f"fetch {desc} ...")
    )
    saved = save_boards(data_dir / "boards", boards)
    rows = sum(len(v) for v in boards.values())
    if saved:
        print(
            f"saved {rows} board rows from {', '.join(saved)} to {data_dir / 'boards'}"
        )
    return 0


def _column_label(name: str) -> str:
    """The short name a benchmark column carries: "Terminal Bench 2.1" → "TB 2.1".

    Column headers have to fit beside each other, and there are enough
    benchmarks to score on that the full names run past any terminal width.
    """
    if name == "LMArena Elo":
        return "ARENA"
    if name == "DeepSWE 1.1":
        # the version lives in the legend ("DeepSWE = DeepSWE 1.1"):
        # exact match, so a future revision keeps its number until renamed
        return "DeepSWE"
    for family, short in (("Terminal Bench", "TB"), ("SWE Bench", "SWE")):
        if name.startswith(family):
            rest = name[len(family) :]
            if short == "SWE":
                # "SWE Verified" is the widest header in the scores table;
                # "Vrfd" keeps the column parseable at a glance
                rest = rest.replace("Verified", "Vrfd")
            return short + rest
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
        name = sorted(spellings[folded], key=lambda n: (n.count("-") == 0, n))[0]
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
        parts = sorted(
            ((name, derived[name]) for name in derived if canonical(name) == key),
            key=lambda part: (-part[1], part[0]),
        )
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
    shown = (
        models
        if getattr(args, "all_models", False) or not on_offer
        else [m for m in models if offered(m.model_id, on_offer)]
    )
    ranked = sorted(
        (m for m in shown if m.scores),
        key=lambda m: -(combined(derived_scores(m, table)) or 0),
    )
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
    print(
        f"{len(models)} models · {len(columns)} benchmarks scored on · "
        f"{len(scored)} with scores · {len(one)} on one benchmark · "
        f"{len(zerod)} with none scored · {len(unscored)} without any"
    )
    print()
    cells = [_score_cells(m, table, columns) for m in ranked]
    # the same markers as `list`, over the models shown here: rank position
    # in this ordering, · where there is no score to rank
    marks = {id(m): grade_for(combined(derived_scores(m, table))) for m in ranked}
    print("GRADE: ++ ≥90 · + ≥75 · o 60–75 · - 50–60 · -- <50 · · unranked")
    heads = ["MODEL", "GRADE", "SCORE"] + [label for _k, label, _n in columns]
    grid = []
    for model, row in zip(ranked, cells, strict=True):
        derived = derived_scores(model, table)
        score = combined(derived)
        grid.append(
            [
                model.name,
                marks.get(id(model), "·"),
                f"{score:.0f}" if score is not None else "—",
            ]
            + row
        )
    # measured, not fixed: MODEL fits the shown rows (not the whole
    # catalog) and the fixed columns fit their headers. The benchmark
    # columns keep double gaps — their labels hold single spaces
    # ("SWE Vrfd"), so the gap is what delimits them.
    fixed_aligns = ["<", ">", ">"]
    widths = column_widths(heads, grid)
    lines = []
    for cells in [heads] + grid:
        left = format_row(cells[:3], widths[:3], fixed_aligns, gap=" ")
        right = format_row(cells[3:], widths[3:], "<", gap="  ")
        lines.append(("  " + left + ("  " + right if right else "")).rstrip())
    print("\n".join(lines))
    legend = [f"{label} = {name}" for _key, label, name in columns if label != name]
    if legend:
        print()
        print("  " + " · ".join(legend))
    if unscored:
        print(
            f"  {len(unscored)} with no benchmarks: "
            + ", ".join(sorted(m.name for m in unscored)[:10])
        )
    print('  how a score is derived: README, "How a model\'s score is computed"')
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
        return "TB4"  # harder scale, ranked on its own
    if folded.startswith("terminal-bench"):
        return "TB"
    if folded.startswith("swe-bench"):
        return "SWE"
    return benchmark[:5]


def _cells(row, axis: bool = True) -> tuple[str, ...]:
    """The fixed columns of one row, as text: score, cost, value, plan.

    The score is the model's combined 0-100 number, rounded for the column;
    the value is score per dollar — free lines divide by their nominal
    rate, so the cheapest free models read highest ("free" only where the
    price is exactly zero, "-" with no score). The best-value table orders
    on it, so the column that explains the order stays printed. *axis* is
    kept for the single-benchmark layout, which adds that benchmark's tag.
    Prices are cents, as elsewhere.
    """
    no_cost = row.free or row.price == 0
    price = "0" if no_cost else f"{round(row.price * 100)}"
    score = "-" if row.score is None else f"{round(row.score)}"
    if axis:
        return (score, price, _pick_value(row), row.provider)
    return (score, _bench_tag(row.benchmark), price, _pick_value(row), row.provider)


def _widths(rows, axis: bool = True) -> tuple[int, ...]:
    """Widest cell per column across the whole view, header included."""
    headings = AXIS_HEADERS if axis else SINGLE_HEADERS
    return tuple(column_widths(headings, [_cells(r, axis) for r in rows]))


def _print_row(
    row, widths: tuple[int, ...], curated: set[str] = frozenset(), axis: bool = True
) -> None:
    name = f"{row.name} *" if row.model.model_id in curated else row.name
    print(
        f"{row.grade:>{GRADE_WIDTH - 1}} "
        + _row_line(_cells(row, axis), widths)
        + f"  {name}"
    )


def _select(data_dir: Path, source: str | None, price: str = "weighted"):
    """Which providers a view covers, or None if the name is not one.

    Providers are zen and go, plus ``-`` for catalog models in neither tier.
    """
    tiers = sources.available_sources()
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
    """Model ids offered right now, or None for no restriction.

    With ``OPENCODE_API_KEY`` the live config API is the record — it is
    also snapshotted to ``available/`` on the way, so offline runs inherit
    the liveness. Without a key (or when the API is unreachable) the last
    snapshots decide. *bypass* (`--all`) skips every filter.
    """
    if bypass:
        return None
    key = load_key()
    if key:
        try:
            live = fetch_live(_api_get(key))
        except (OSError, ValueError, KeyError) as exc:
            print(
                f"warning: live config unreachable ({exc}) — using the last snapshots"
            )
        else:
            ids = set().union(*live.values())
            if not ids:
                print("warning: live config lists no models — using the last snapshots")
            else:
                save_live(data_dir, live)
                return ids
    else:
        print(
            "no API token found — availability may be stale, some models "
            "may be unavailable (`auth` for live data, `--all` for the "
            "full catalog)"
        )
    return available_ids(data_dir)


def _in_tier_slug_list(model_id: str, slugs: set[str]) -> bool:
    """Whether *model_id* is claimed by a tier's shipped slug list.

    Mirrors :func:`sources.members` matching: slug, full id, or the
    ``-free`` variant of the slug. Used to keep tier-listed models out of
    the catalog-only pool even when a tier dropped them from its offer.
    """
    slug = model_id.split("/")[-1]
    return slug in slugs or model_id in slugs or f"{slug}-free" in slugs


def _rows_for(
    data_dir: Path, selected: list[str], price: str, all_models: bool = False
):
    """Build, merge and dedupe the rows for *selected* providers.

    Shared by `list` and `next` so a recommendation can never disagree with
    the ranking it was drawn from.

    Unless *all_models*, a row is a model the plans actually offer: the
    live config API is the record when keyed, else the snapshots. A model
    the plans dropped is neither listed nor scored. The scales are still
    built over the whole catalog, so a score does not move when a plan
    changes what it sells.
    """
    models, curated, _ = _load(data_dir)
    on_offer = _current_offered(data_dir, bypass=all_models)
    # the fetched free-tier nominals win per model; the committed table is
    # only the offline fallback for whatever the snapshot does not cover
    snapshot = load_free_nominals(data_dir)
    free_rates = {**FREE_TIER_NOMINAL_RATES, **snapshot} if snapshot else None
    # one scale for the whole catalog: per-tier scales would make the same
    # score mean two different things in the merged view
    table = scales(models)
    groups, listed, counts, tier_slugs = [], [], {}, {}
    covered: set[str] = set()
    # slugs shipped for every selected tier: a model listed by any of them
    # belongs to that tier (or is dropped by availability) and must not be
    # routed to the catalog-only "-" pool, even when the tier dropped it
    selected_tier_slugs: set[str] = set()
    for provider in selected:
        if provider != "-":
            selected_tier_slugs |= sources.load_source(provider)
    for provider in selected:
        if provider == "-":
            pool = [
                m
                for m in models
                if m.model_id not in covered
                and not _in_tier_slug_list(m.model_id, selected_tier_slugs)
            ]
            free_ids = sources.members(models, None)[1]
            free_ids &= {m.model_id for m in pool}
        else:
            tier_slugs[provider] = sources.load_source(provider)
            pool, free_ids = sources.members(models, tier_slugs[provider])
            if on_offer is not None:
                pool = [m for m in pool if offered(m.model_id, on_offer)]
                free_ids &= {m.model_id for m in pool}
            counts[provider] = len(pool)
            covered |= {m.model_id for m in pool}
        listed += pool
        groups.append(
            build_rows(
                pool,
                _entries_for(provider, data_dir),
                free_ids,
                axis=price,
                provider=provider,
                table=table,
                free_rates=free_rates,
            )
        )
    rows = dedupe_providers(merge_rows(*groups))
    return rows, listed, curated, counts, tier_slugs, models


def _cmd_list(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")

    source = args.source
    chosen = _select(data_dir, source)
    if chosen is None:
        return _fail(
            f"unknown source {source!r} — available: "
            f"{', '.join(sources.available_sources())}, all"
        )
    selected, scope = chosen

    rows, listed, curated, counts, tier_slugs, models = _rows_for(
        data_dir, selected, args.price, getattr(args, "all_models", False)
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
        rows.sort(key=lambda r: (-ratio(r.score, r.price), -r.score))
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
    print(f"{head} · {len(rows)} lines · {order_note} · {COST_NOTE}")
    if not getattr(args, "all_models", False):
        plans = "Go/Zen plans" if source in (None, "all") else f"{source} plan"
        print(
            f"Only models available right now on the {plans} — "
            f"full list with --all-models."
        )
    print("GRADE: ++ ≥90 · + ≥75 · o 60–75 · - 50–60 · -- <50 · · unranked")

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
        _section(f"worst score (bottom 30%) · {len(worst)} lines", widths, axis)
        for row in worst:
            _print_row(row, widths, curated.curated, axis)

        _section(f"highest cost (top 30%) · {len(pricey)} lines", widths, axis)
        for row in pricey:
            _print_row(row, widths, curated.curated, axis)
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

    Free lines carry their nominal rate, so they divide like any other
    cost — the cheapest free models read highest. "free" is only the
    degenerate zero-price fallback; "-" means there is no score to value.
    """
    if row.score is None:
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
#: column instead of shifting the rest of the row.
NEXT_HEADERS = ("#", "PLAN", "SCORE", "¢/1M", "SCORE/$", "ENDPOINT", "BAND")
NEXT_ALIGNS = ("<", "<", ">", ">", ">", "<", "<")
NEXT_MIN_WIDTHS = (2, 5, 6, 5, 7, 0, 0)


def _next_table(picks: list) -> str:
    """The picks as an aligned grid: position, plan, score, cost, value,
    endpoint, band."""
    rows = []
    for i, row in enumerate(picks, 1):
        _score, price, value, _prov = _cells(row)
        rows.append(
            [
                str(i),
                row.provider,
                f"{round(row.score)}",
                price,
                value,
                _endpoint(row),
                _next_band(row),
            ]
        )
    return render(
        NEXT_HEADERS,
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
        return _fail(
            f"unknown source {args.source!r} — available: "
            f"{', '.join(sources.available_sources())}, all"
        )
    rows, listed, _curated, _counts, _slugs, _models = _rows_for(
        data_dir, chosen[0], args.price, getattr(args, "all_models", False)
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
    print(_next_table(picks))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="opencode-rank",
        description="Price/score Pareto frontier of models from opencode.ai/data",
    )
    parser.add_argument(
        "--data",
        default=str(DEFAULT_DATA),
        help="data directory (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fetch", help="refresh data from opencode.ai").add_argument(
        "--all",
        dest="all_models",
        action="store_true",
        help="download the full model catalog, not just the models the "
        "plans offer (kept records are never deleted either way)",
    )
    sub.add_parser(
        "models",
        help="the models the plans offer right now, "
        "as endpoints beside display names: live "
        "from the Console config API with "
        "OPENCODE_API_KEY, else the last fetch "
        "snapshots",
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
        "--price",
        choices=("weighted", "input", "output"),
        default="weighted",
        help="cost of a line (default: weighted 20%% in, 60%% cached, 20%% out)",
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
        "--price",
        choices=("weighted", "input", "output"),
        default="weighted",
        help="cost of a line (default: weighted 20%% in, 60%% cached, 20%% out)",
    )
    p_next.add_argument(
        "--all-models",
        "--all",
        dest="all_models",
        action="store_true",
        help="include models the plans no longer offer; the "
        "default shows only what a plan currently lists",
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
        "models": _cmd_models,
        "auth": _cmd_auth,
        "overrides": _cmd_conflicts,
        "list": _cmd_list,
        "next": _cmd_next,
        "scores": _cmd_scores,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
