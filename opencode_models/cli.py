"""Command-line interface.

Usage::

    python3 -m opencode_models fetch            # refresh data/ from opencode.ai

Exit codes: 0 success, 1 usage or data error (message on stdout).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from . import sync as sync_mod  # noqa: F401 (test compat: freezes lazy refresh)
from .commands.auth import run as _cmd_auth
from .commands.fetch import run as _cmd_fetch
from .commands.list import _current_offered  # noqa: F401 (test compat)
from .commands.list import run as _cmd_list
from .commands.models import MODELS_ALL_HEADERS, MODELS_HEADERS  # noqa: F401 (test compat)
from .commands.models import run as _cmd_models
from .commands.next import (
    _endpoint,  # noqa: F401 (test compat)
    _next_table,  # noqa: F401 (test compat)
)
from .commands.next import run as _cmd_next
from .commands.overrides import run as _cmd_conflicts
from .commands.render import (
    CONFLICT_HEADERS,  # noqa: F401 (test compat; moves with fetch)
)
from .commands.render import column_label as _column_label  # noqa: F401 (test compat)
from .commands.render import header as _header  # noqa: F401 (test compat)
from .commands.render import headings as _headings  # noqa: F401 (test compat)
from .commands.render import next_headings as _next_headings  # noqa: F401 (test compat)
from .commands.render import pick_value as _pick_value  # noqa: F401 (test compat)
from .commands.render import print_conflicts as _print_conflicts  # noqa: F401 (test compat)
from .commands.render import (
    style as _style,  # noqa: F401 (test compat)
)
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
    p_auth = sub.add_parser(
        "auth",
        help="save the Console service-account key "
        "to an owner-only file (prompted, never "
        "echoed); verifies it live when it can",
    )
    p_auth.add_argument(
        "--plan",
        choices=("go", "go-plus"),
        default=None,
        help="record the workspace plan alongside the key "
        "(Plus caps are 2-4x Go caps, so usage headroom needs it)",
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
        help="cost calculation (default: %(default)s): est ¢/req "
        "from published token mix, else Go plan median (~); "
        "fix: 20%% in, 60%% cached, 20%% out",
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
        help="cost of a line (default: %(default)s): est ¢/req from "
        "the published token mix, Go plan median (~) where a "
        "model has none; fix 20%% in, 60%% cached, 20%% out; "
        "input/output rate only",
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
        "--plan",
        choices=("go", "go-plus"),
        default=None,
        help="which plan's limits to read (default: stored preference, "
        "else Go); --plan go-plus for the $40/mo plan",
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
