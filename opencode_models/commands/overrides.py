"""`overrides` command: curated disagreements, ranked by provenance."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..catalog import load_catalog
from ..overrides import off_band_models
from . import fail
from .render import format_conflicts


def run(args: argparse.Namespace) -> int:
    """Curated overrides that disagree with upstream, ranked by provenance.

    Each disagreement shows both values with the tier of their source, and
    whether the override applies (its source is at least as good as
    upstream's) or is merely recorded. To accept upstream, delete the
    entry in data/overrides/.
    """
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return fail(f"no data directory at {data_dir} — run `fetch` first")
    models, curated, board_conflicts, _merge_warnings = load_catalog(data_dir)
    out = []
    text = format_conflicts(curated.conflicts, curated.orphans, curated.manual_orphans)
    n = len(curated.conflicts) + len(curated.orphans) + len(curated.manual_orphans)
    if text:
        out.append(text)
    if board_conflicts:
        out.append("— official boards —")
        board_text = format_conflicts(board_conflicts)
        if board_text:
            out.append(board_text)
    for mid, s21, s40, resid in off_band_models(curated.models):
        out.append(
            f"{mid}  Terminal-Bench 2.1 {s21} vs 4.0 {s40} — "
            f"{resid:+.0f} off the 2.1→4.0 band, check the run"
        )
    out.append(
        f"{len(curated.curated)} curated models · {n} curated "
        f"disagreements · {len(board_conflicts)} from official boards"
    )
    out.append(
        "tiers: self = vendor self-report · board = official board · "
        "indep = independent evaluator"
    )
    print("\n".join(out))
    return 0
