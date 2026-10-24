"""`next` command: free and paid picks interleaved band by band."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..ranking import RECOMMEND_FLOOR, grade_rows, next_up, order_rows, plans_of
from ..rows import rows_for
from ..scoring import MIN_AXES
from ..tables import render as render_table
from . import fail
from .list import _select
from .render import bold_head, cells, next_headings

NEXT_BANDS = {
    "free": "free",
    "included": "included",
    "payg": "payg",
    "catalog": "catalog",
}


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
    headers = next_headings(price)
    rows = []
    for i, row in enumerate(picks, 1):
        _score, cost, value, _prov = cells(row)
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
    return render_table(
        headers,
        rows,
        aligns=NEXT_ALIGNS,
        min_widths=NEXT_MIN_WIDTHS,
        indent="  ",
        gap=" ",
    )


def run(args: argparse.Namespace) -> int:
    """A short ordered sequence: free and paid picks interleaved band by band."""
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return fail(f"no data directory at {data_dir} — run `fetch` first")
    chosen = _select(data_dir, args.source)
    if chosen is None:
        return fail(f"unknown source {args.source!r} — available: go, zen, all")
    _result = rows_for(
        data_dir, chosen[0], args.cost, getattr(args, "all_models", False)
    )
    lines = list(_result.warnings)
    rows, listed, _curated, _counts, _slugs, _models = (
        _result.rows,
        _result.listed,
        _result.curated,
        _result.counts,
        _result.tier_slugs,
        _result.models,
    )
    if not listed:
        if lines:
            print("\n".join(lines))
        return fail(
            f"no model of {args.source or 'zen+go'} is in {data_dir} "
            f"— run `fetch` first"
        )
    picks = next_up(grade_rows(order_rows(rows)), count=args.count)
    if not picks:
        if lines:
            print("\n".join(lines))
        return fail(
            f"no model of {args.source or 'zen+go'} scores on "
            f"{MIN_AXES}+ benchmarks in {data_dir} — nothing to "
            f"recommend yet"
        )

    lines.append(
        f"Next {len(picks)} recommended models, "
        f">{RECOMMEND_FLOOR:.0%} overall score, free and paid picks "
        f"interleaved, alternating Zen/Go plans."
    )
    lines.append("")
    lines.append(bold_head(_next_table(picks, args.cost)))
    print("\n".join(lines))
    return 0
