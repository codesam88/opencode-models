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
from importlib import resources
from pathlib import Path

from . import __version__, availability
from . import arena as arena_mod
from .allowance import WINDOW_FRACTIONS, cost_per_request
from .arena import ArenaCache, merge_from_file
from .auth import load_key, save_key
from .benchmarks import available
from .boards import fetch_boards, load_boards, merge_boards, save_boards
from .fetch import SITEMAP_URL, http_get, refresh, urls_from_sitemap
from .identity import canonical_id
from .live import LIVE_CONFIG_URL, fetch_live, fetch_served, merge_served, save_live
from .models import Model, load_models
from .overrides import load_curated, off_band_models, source_tier
from .pricing import (
    USAGE_EXPORT_URL,
    load_prices,
    refresh_prices,
)
from .ranking import (
    BAND_FREE,
    BAND_INCLUDED,
    BAND_PAYG,
    RECOMMEND_FLOOR,
    build_rows,
    by_score_per_price,
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


def _style(text: str) -> str:
    """Bold when talking to a terminal, plain when piped or captured."""
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def _row_line(cells: tuple[str, ...], widths: tuple[int, ...]) -> str:
    return format_row(cells, widths)


def _cost_unit(price: str) -> str:
    """The cost column's unit: ``fix`` and ``est`` count cents per request.

    ``input`` and ``output`` show raw per-1M-token rates instead.
    """
    return "¢/req" if price in ("fix", "est") else "¢/1m"


def _bold_head(text: str) -> str:
    """Bold the first (header) line when talking to a terminal.

    Piped or captured output is untouched, so tests read plain text.
    """
    head, *rest = text.splitlines()
    return "\n".join([_style(head), *rest])


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


#: `models` grid headings, with and without the availability column.
MODELS_HEADERS = ("Endpoint", "Model")
MODELS_ALL_HEADERS = ("Avail", "Endpoint", "Model")

#: `overrides` grid headings.
CONFLICT_HEADERS = ("Model", "Benchmark", "Catalog", "Reported", "Used", "Note")

#: `scores` fixed columns; benchmark labels append after these.
SCORES_STATIC_HEADERS = ("Model", "Grade", "Score")


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


def _fail(msg: str) -> int:
    print(msg)
    return 1


def _load(data_dir: Path):
    """Upstream records, arena scores, curated overlays and official boards.

    Returns the models, the curated layer (which models carry overrides), and
    the board-vs-registry disagreements — so `overrides` can report the
    boards' numbers replacing self-reports.
    """
    curated = load_curated(
        data_dir / "opencode", data_dir / "overrides", data_dir / "manual"
    )
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


def _print_conflicts(conflicts, orphans=(), manual_orphans=()) -> int:
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
    manual_orphans = [
        (model_id, "", "", "", "", "manual record shadowed by upstream record")
        for model_id in sorted(manual_orphans)
    ]
    rows += manual_orphans
    if not rows:
        return 0
    print(_bold_head(render(CONFLICT_HEADERS, rows, aligns="<", indent="  ")))
    return len(conflicts) + len(orphans) + len(manual_orphans)


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
    n = _print_conflicts(curated.conflicts, curated.orphans, curated.manual_orphans)
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
                "User-Agent": f"opencode-models/{__version__}",
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
    """Display names by slug or id.

    Catalog records come first; plan snapshots and tier-price entries add
    names for slugs the catalog does not carry (docs-documented orphans),
    so a served model the plans offer but the catalog never named can still
    be shown. Internal ids with no documentation stay unnamed and hidden.
    """
    names = {}
    for model in load_models(Path(data_dir) / "opencode"):
        names[model.model_id] = model.name
        names[model.model_id.split("/")[-1]] = model.name
    for tier in ("go", "zen"):
        for slug, rec in availability._plan_models(data_dir, tier).items():
            if isinstance(rec, dict) and rec.get("name"):
                names.setdefault(slug, rec["name"])
                names.setdefault(slug.split("/")[-1], rec["name"])
    for _tier, entries in load_prices(data_dir / "tier_prices").items():
        for entry in entries:
            if entry.model_id and entry.name:
                names.setdefault(entry.model_id, entry.name)
                names.setdefault(entry.model_id.split("/")[-1], entry.name)
    return names


def _model_name(names: dict[str, str], slug: str) -> str:
    """The catalog's display name for a tier slug, or the slug itself.

    Tier pages list free variants under their own slug
    (``step-5-preview-free``) while the catalog carries the base model,
    so the ``-free`` suffix reaches back to it.
    """
    if slug in names:
        return names[slug]
    base = canonical_id(slug)
    if base in names:
        return names[base]
    return slug


def _status_label(ids, slug: str) -> str:
    """The API status marker for one row: [x] served, [ ] not, [?] unknown.

    *ids* is a live status mapping (slug -> bool) or a bare slug set from
    a docs snapshot — sets carry no per-model status, so the honest
    marker there is [?] rather than an invented one.
    """
    if not isinstance(ids, dict):
        return "[?]"
    value = ids.get(slug)
    return "[x]" if value is True else "[ ]" if value is False else "[?]"


def _catalog_known(slug: str, names: dict[str, str]) -> bool:
    """Whether the catalog can name *slug* (free variants reach each other)."""
    names_canonical = {canonical_id(k) for k in names}
    return slug in names or canonical_id(slug) in names_canonical


def _cmd_models(args: argparse.Namespace) -> int:
    """The models the plans offer right now, per tier.

    Live from the inference gateway's model lists (served = callable)
    plus the config API's known set, merged to ``available/`` on the way
    so `list` and `next` inherit the liveness. Default rows are the
    served models only; ``--all`` adds known-but-not-served rows, marked
    ``[ ]``. Offline: the snapshots the last ``fetch`` left behind. Rows
    the catalog cannot name (internal ids like ``test``) are hidden.
    """
    data_dir = Path(args.data)
    key = load_key()
    tiers = None
    if key:
        try:
            served = fetch_served(_api_get(key))
            known = fetch_live(_api_get(key))
        except (OSError, ValueError, KeyError) as exc:
            print(
                f"warning: live model list unreachable ({exc}) — showing the last snapshots"
            )
        else:
            tiers = merge_served(served, known)
            save_live(data_dir, tiers)
            origin = (
                f"live from {LIVE_CONFIG_URL} (recorded to {data_dir / 'available'})"
            )
            if not served.get("go"):
                print(
                    "warning: the Go plan returned no models — check that "
                    "models are enabled in the Console and that the key is a "
                    "personal one with model permissions "
                    "(User \u2192 Account \u2192 Personal API Keys)"
                )
    if tiers is None:
        known = availability.load_available(data_dir)
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
    by_slug: dict[str, tuple[str, str]] = {}  # slug -> (tier, status)
    for tier in sorted(tiers):
        ids = tiers[tier]
        for slug in sorted(ids):
            marker = _status_label(ids, slug)
            if marker == "[ ]" and not args.all_models:
                continue  # known-unserved: an --all view only
            if not _catalog_known(slug, names):
                continue  # internal ids (test, *-backup) are not ours to show
            if slug not in by_slug or tier == "go":
                by_slug[slug] = (tier, marker)
    rows = []
    for slug, (tier, marker) in by_slug.items():
        endpoint = f"opencode-go/{slug}" if tier == "go" else f"opencode/{slug}"
        name = _model_name(names, slug)
        rows.append((endpoint, name if name != slug else "-", marker))
    rows.sort(key=lambda row: (row[1].lower(), row[0]))
    counts = " + ".join(f"{len(tiers[t])} {t}" for t in sorted(tiers))
    print(origin)
    print(
        f"{len(by_slug)} models · {counts}"
        + (f" · {', '.join(unknown)} unknown" if unknown else "")
    )
    print()
    # the status marker column is an --all extra; default is endpoint + name
    if args.all_models:
        head = list(MODELS_ALL_HEADERS)
        view = [(marker, endpoint, name) for endpoint, name, marker in rows]
    else:
        head = list(MODELS_HEADERS)
        view = [(endpoint, name) for endpoint, name, _marker in rows]
    print(_bold_head(render(head, view, aligns=("<",) * len(head), indent="  ")))
    return 0


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
    curated = load_curated(
        data_dir / "opencode", data_dir / "overrides", data_dir / "manual"
    )
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


def _column_label(name: str) -> str:
    """The short name a benchmark column carries: "Terminal Bench 2.1" → "Tb 2.1".

    Column headers have to fit beside each other, and there are enough
    benchmarks to score on that the full names run past any terminal width.
    Labels are title-cased like every other grid header.
    """
    if name == "LMArena Elo":
        return "Arena"
    if name == "DeepSWE 1.1":
        # the version lives in the legend ("Deepswe = DeepSWE 1.1"):
        # exact match, so a future revision keeps its number until renamed
        return "Deepswe"
    for family, short in (("Terminal Bench", "Tb"), ("SWE Bench", "Swe")):
        if name.startswith(family):
            rest = name[len(family) :]
            if short == "Swe":
                # "Swe vrfd" is the widest header in the scores table;
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
    on_offer = availability.available_ids(data_dir)
    shown = (
        models
        if getattr(args, "all_models", False) or not on_offer
        else [m for m in models if availability.offered(m.model_id, on_offer)]
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
    heads = list(SCORES_STATIC_HEADERS) + [
        label.capitalize() for _k, label, _n in columns
    ]
    grid = []
    for model, row in zip(ranked, cells, strict=True):
        derived = derived_scores(model, table)
        score = combined(derived)
        grid.append(
            [
                model.name,
                marks.get(id(model), "·"),
                f"{score:.0f}" if score is not None else "-",
            ]
            + row
        )
    # measured, not fixed: MODEL fits the shown rows (not the whole
    # catalog) and the fixed columns fit their headers. The benchmark
    # columns keep double gaps — their labels hold single spaces
    # ("Swe vrfd"), so the gap is what delimits them.
    fixed_aligns = ["<", ">", ">"]
    widths = column_widths(heads, grid)
    lines = []
    for cells in [heads] + grid:
        left = format_row(cells[:3], widths[:3], fixed_aligns, gap=" ")
        right = format_row(cells[3:], widths[3:], "<", gap="  ")
        lines.append(("  " + left + ("  " + right if right else "")).rstrip())
    lines[0] = _style(lines[0])
    print("\n".join(lines))
    # footnotes: the grade key first, then the column legend and the
    # tail notes, as one block after a single blank line
    print()
    print("  GRADE: ++ ≥90 · + ≥75 · o 60–75 · - 50–60 · -- <50 · · unranked")
    legend = [
        f"{label.capitalize()} = {name}"
        for _key, label, name in columns
        if label != name
    ]
    if legend:
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

    With ``OPENCODE_API_KEY`` availability comes from the inference
    gateway's model lists (served = callable), while the config API
    only annotates what is known-but-unserved; the merged status is
    snapshotted to ``available/`` on the way, so offline runs inherit
    the liveness. Without a key the existing notice prints and the last
    snapshots decide. *bypass* (`--all`) skips every filter.
    """
    if bypass:
        return None
    key = load_key()
    if key:
        try:
            served = fetch_served(_api_get(key))
            known = fetch_live(_api_get(key))
        except (OSError, ValueError, KeyError) as exc:
            print(
                f"warning: live model list unreachable ({exc}) — using the last snapshots"
            )
        else:
            ids = set().union(*served.values())
            if not ids:
                print("warning: no models served — using the last snapshots")
            else:
                save_live(data_dir, merge_served(served, known))
                if not served.get("go"):
                    print(
                        "warning: the Go plan returned no models — check that "
                        "models are enabled in the Console and that the key is a "
                        "personal one with model permissions "
                        "(User \u2192 Account \u2192 Personal API Keys)"
                    )
                return ids
    else:
        print(
            "no API token found - availability may be stale, some models "
            "may be unavailable (`auth` for live data, `--all` for the "
            "full catalog)"
        )
    known = availability.load_available(data_dir)
    if all(ids is None for ids in known.values()):
        return None
    out: set[str] = set()
    for tier_ids in known.values():
        if isinstance(tier_ids, dict):
            out |= {slug for slug, served in tier_ids.items() if served is not False}
        elif tier_ids:
            out |= tier_ids
    return out or None


def _docs_known_slugs(data_dir: Path, tier: str) -> set[str]:
    """Slugs a tier's documentation knows about (tier_prices or plans)."""
    out: set[str] = set()
    snap = availability._snapshot_ids(data_dir, tier)
    if snap:
        out |= set(snap)
    out |= set(availability._plan_models(data_dir, tier))
    return out


def _synthesize_roster_models(
    data_dir: Path, tier: str, roster: set[str], models: list
) -> list:
    """Scoreless catalog records for docs-documented roster slugs with no catalog match.

    The roster is the source of truth for what is offered; the catalog only
    identifies and extends. A slug the plans offer and the docs describe but
    the catalog never carried (e.g. ``beam-free``) still renders, as an
    unscored row. Live-only or internal slugs with no docs record are
    excluded here — that is the junk filter.
    """
    plan_models = availability._plan_models(data_dir, tier)
    entries = load_prices(data_dir / "tier_prices").get(tier, [])
    entry_by_slug: dict[str, object] = {}
    for entry in entries:
        if entry.model_id and entry.model_id not in entry_by_slug:
            entry_by_slug[entry.model_id] = entry
    catalog_canonical = {canonical_id(m.model_id.split("/")[-1]) for m in models}
    aliases = availability.load_aliases(data_dir)
    catalog_by_id = {m.model_id: m for m in models}
    out: list[Model] = []
    for slug in sorted(roster):
        if slug not in _docs_known_slugs(data_dir, tier):
            continue
        if canonical_id(slug.split("/")[-1]) in catalog_canonical:
            continue
        rec = plan_models.get(slug)
        name = rec.get("name") if isinstance(rec, dict) else None
        entry = entry_by_slug.get(slug)
        if not name and entry is not None:
            name = entry.name
        if not name:
            name = slug
        if entry is not None:
            price_input = entry.input
            price_output = entry.output
            price_cached_input = entry.cached_read
        else:
            price_input = 0.0
            price_output = 0.0
            price_cached_input = 0.0
        lab = slug.split("/")[0] if "/" in slug else slug
        # aliases are an explicit mapping layer consulted only here: the
        # roster slug's own snapshot name/prices are kept, and the target
        # catalog model's scores and sources are unioned in (synthetic
        # records start scoreless, so the target provides all of them).
        # A missing target leaves the row scoreless.
        scores: dict = {}
        sources: dict = {}
        alias = aliases.get(slug)
        if isinstance(alias, dict):
            target_id = alias.get("model_id")
            target = catalog_by_id.get(target_id) if target_id else None
            if target is not None:
                scores = {**target.scores, **scores}
                sources = {**target.sources, **sources}
        out.append(
            Model(
                model_id=slug,
                name=name,
                lab=lab,
                price_input=price_input,
                price_output=price_output,
                price_cached_input=price_cached_input,
                scores=scores,
                sources=sources,
            )
        )
    return out


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
    # page-estimated $/req per snapshot slug (the `est` cost base);
    # Zen lines and snapshot-missing models fall back to the fix
    est_rates = None
    if price == "est":
        try:
            plan_snapshot, _origin = _load_plan_snapshot(data_dir)
        except (OSError, ValueError):
            plan_snapshot = {}
        est_rates = {}
        for slug, rec in (plan_snapshot.get("models") or {}).items():
            cpr = cost_per_request(rec.get("tiers") or [], rec.get("assumptions"))
            if cpr is not None and cpr > 0:
                est_rates[slug] = cpr
    # one scale for the whole catalog: per-tier scales would make the same
    # score mean two different things in the merged view
    table = scales(models)
    groups, listed, counts, tier_slugs = [], [], {}, {}
    covered: set[str] = set()
    synthetic_ids: set[int] = set()
    # slugs served/documented for every selected tier: a model listed by any
    # of them belongs to that tier (or is dropped by availability) and must
    # not be routed to the catalog-only "-" pool, even when the tier dropped it
    selected_tier_slugs: set[str] = set()
    for provider in selected:
        if provider != "-":
            selected_tier_slugs |= availability.load_roster(data_dir, provider)
    for provider in selected:
        if provider == "-":
            pool = [
                m
                for m in models
                if m.model_id not in covered
                and not any(
                    availability.slug_matches(m.model_id, s)
                    for s in selected_tier_slugs
                )
            ]
            free_ids = {m.model_id for m in pool if m.price_input == 0}
            provider_listed = list(pool)
        else:
            tier_slugs[provider] = availability.load_roster(data_dir, provider)
            pool, base_free = availability.members(models, tier_slugs[provider])
            catalog_pool = list(pool)
            catalog_ids = {id(m) for m in catalog_pool}
            pool = catalog_pool + _synthesize_roster_models(
                data_dir, provider, tier_slugs[provider], models
            )
            free_candidate_slugs = availability.free_slugs(data_dir, provider)
            free_ids = set(base_free) | {
                m.model_id
                for m in pool
                if any(
                    availability.slug_matches(m.model_id, s)
                    for s in free_candidate_slugs
                )
            }
            if on_offer is not None:
                pool = [m for m in pool if availability.offered(m.model_id, on_offer)]
                free_ids &= {m.model_id for m in pool}
            counts[provider] = sum(1 for m in pool if id(m) in catalog_ids)
            covered |= {m.model_id for m in pool}
            free_ids &= {m.model_id for m in pool}
            provider_listed = [m for m in pool if id(m) in catalog_ids]
            synthetic_ids |= {id(m) for m in pool if id(m) not in catalog_ids}
        listed += provider_listed
        provider_entries = _entries_for(provider, data_dir)
        catalog_rows = [m for m in pool if id(m) not in synthetic_ids]
        groups.append(
            build_rows(
                catalog_rows,
                provider_entries,
                free_ids,
                axis=price,
                provider=provider,
                table=table,
                est_rates=est_rates,
            )
        )
        synth_rows = [m for m in pool if id(m) in synthetic_ids]
        if synth_rows:
            # synthetic models name their own exact offering: no variant
            # reach, or a free twin would stage twice under two names.
            groups.append(
                build_rows(
                    synth_rows,
                    provider_entries,
                    free_ids,
                    axis=price,
                    provider=provider,
                    table=table,
                    est_rates=est_rates,
                    reach_variants=False,
                )
            )
    rows = dedupe_providers(merge_rows(*groups))
    rows = [
        replace(r, synthetic=True) if id(r.model) in synthetic_ids else r for r in rows
    ]
    return rows, listed, curated, counts, tier_slugs, models


def _cmd_list(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")

    source = args.source
    chosen = _select(data_dir, source)
    if chosen is None:
        return _fail(f"unknown source {source!r} — available: go, zen, all")
    selected, scope = chosen

    rows, listed, _curated, counts, tier_slugs, models = _rows_for(
        data_dir, selected, args.cost, getattr(args, "all_models", False)
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
    rows, listed, _curated, _counts, _slugs, _models = _rows_for(
        data_dir, chosen[0], args.cost, getattr(args, "all_models", False)
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


def _load_plan_snapshot(data_dir: Path) -> tuple[dict, str]:
    """The Go plan snapshot (limits, prices, assumptions) and its origin.

    Resolution order: the data dir's ``plans/go.json`` first — that is
    the copy ``plans-update`` refreshes — then the snapshot shipped
    inside the package, so an installed tool with no data dir still
    resolves. A corrupt local file falls through to the shipped one.
    """
    local = Path(data_dir) / "plans" / "go.json"
    if local.is_file():
        try:
            return json.loads(local.read_text()), "local"
        except (json.JSONDecodeError, OSError):
            pass
    shipped = resources.files("opencode_models") / "data" / "plans" / "go.json"
    return json.loads(shipped.read_text()), "shipped"


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
    try:
        snapshot, origin = _load_plan_snapshot(data_dir)
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
