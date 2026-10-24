"""`fetch` command: refresh every source, warning instead of failing."""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import arena as arena_mod
from .. import availability, go_pricing, zen_pricing
from ..arena import ArenaCache
from ..auth import load_key
from ..boards import fetch_boards, save_boards
from ..catalog import curated_layer
from ..fetch import SITEMAP_URL, http_get, refresh, urls_from_sitemap
from ..live import api_get as _api_get
from ..live import fetch_live, fetch_served, merge_served, save_live
from .render import print_conflicts

#: How often the long model-file walk reports: one line per file would bury
#: the per-source announcements, so every fiftieth file plus the last one.
_FETCH_TICK = 50


def _model_tick(done: int, total: int, name: str) -> None:
    if done % _FETCH_TICK == 0 or done == total:
        print(f"  opencode.ai/data: {done}/{total} model files ...")


def run(args: argparse.Namespace, get=None) -> int:
    """Refresh data from opencode.ai.

    Every source is attempted; a dead one warns and keeps existing
    records rather than failing the run. Progress prints stream as each
    phase runs — a fetch takes minutes, so buffering the report to the
    end would hide a stall. Per-phase outcome lines double as the
    report; the curated-conflict table reuses the shared renderer.
    """
    data_dir = Path(args.data)
    get = http_get if get is None else get
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
        urls_from_sitemap(get(SITEMAP_URL))
    except (OSError, ValueError, SyntaxError) as exc:  # sitemap down must not traceback
        print(
            f"warning: model catalog not refreshed ({exc}) — keeping existing records"
        )
    else:
        saved = refresh(data_dir, get=get, progress=_model_tick, only=only)
        print(f"saved {len(saved)} model files to {data_dir / 'opencode'}")
    print("fetch plan pricing from the plan docs pages ...")
    for module, label in ((go_pricing, "go"), (zen_pricing, "zen")):
        try:
            saved = module.refresh(data_dir, get=get)
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
        elo = arena_mod.lookup_arena_elo(get=get)
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
    n = print_conflicts(curated.conflicts, curated.orphans, curated.manual_orphans)
    if n:
        print(
            f"{n} curated override(s) disagree with upstream — "
            f"run `overrides` to review"
        )
    boards = fetch_boards(
        get=get, progress=lambda done, total, desc: print(f"fetch {desc} ...")
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
