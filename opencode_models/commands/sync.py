"""`sync` command: refresh stale T2 sources, validate offline."""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import sync as sync_mod


def run(args: argparse.Namespace, get=None) -> int:
    """Refresh stale T2 sources (availability + plan pricing).

    T1 sources (catalog, boards, arena) are explicit `fetch` only and
    are left untouched here. `--force` refreshes regardless of age
    (curation); `--check` only validates the local snapshots, failing
    shut. The transport override is a parameter (tests) — production
    passes `cli.http_get`.
    """
    if args.check:
        return sync_mod.check_snapshots(Path(args.data))
    return sync_mod.sync(Path(args.data), get=get, force=args.force)
