"""`scores` command: every model's score, one column per benchmark."""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import availability
from ..catalog import load_catalog
from ..ranking import grade_for
from ..scoring import (
    _TRACKED,
    _folded,
    canonical,
    combined,
    derived_scores,
    scales,
)
from ..tables import column_widths, format_row
from .render import column_label, style

#: `scores` fixed columns; benchmark labels append after these.
SCORES_STATIC_HEADERS = ("Model", "Grade", "Score")


def _columns(models, table: dict) -> list[tuple[str, str, str]]:
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
        columns.append((folded, column_label(folded), name))
    return columns


def _cells(model, table: dict, columns: list[tuple[str, str, str]]) -> list[str]:
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


def _select(models, on_offer, all_models: bool) -> list:
    """The models this view ranks: the offered set unless asked for all."""
    if all_models or not on_offer:
        return models
    return [m for m in models if availability.offered(m.model_id, on_offer)]


def _ranked(shown, table: dict) -> tuple[list, list]:
    """Scored models best-first, plus the models with no scores at all."""
    ranked = sorted(
        (m for m in shown if m.scores),
        key=lambda m: -(combined(derived_scores(m, table)) or 0),
    )
    return ranked, [m for m in shown if not m.scores]


def _counts(ranked, table: dict) -> tuple[list, list, list]:
    """How many models the view could score: scored, one benchmark, none."""
    counts: dict[int, list] = {}
    for m in ranked:
        counts.setdefault(len(derived_scores(m, table)), []).append(m)
    scored = [m for m in ranked if combined(derived_scores(m, table)) is not None]
    return (
        scored,
        counts.get(1, []),
        [m for m in ranked if not derived_scores(m, table)],
    )


def _grid(ranked, table: dict, columns: list) -> tuple[list, list]:
    """Header labels plus one row per model: name, grade, score, cells."""
    marks = {id(m): grade_for(combined(derived_scores(m, table))) for m in ranked}
    heads = list(SCORES_STATIC_HEADERS) + [
        label.capitalize() for _k, label, _n in columns
    ]
    grid = []
    for model, row in zip(
        ranked, [_cells(m, table, columns) for m in ranked], strict=True
    ):
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
    return heads, grid


def _render_table(heads: list, grid: list) -> list[str]:
    """The grid as text lines: fixed columns left, benchmark columns right.

    Measured, not fixed: MODEL fits the shown rows (not the whole
    catalog) and the fixed columns fit their headers. The benchmark
    columns keep double gaps — their labels hold single spaces
    ("Swe vrfd"), so the gap is what delimits them.
    """
    widths = column_widths(heads, grid)
    lines = []
    for cells in [heads] + grid:
        left = format_row(cells[:3], widths[:3], ["<", ">", ">"], gap=" ")
        right = format_row(cells[3:], widths[3:], "<", gap="  ")
        lines.append(("  " + left + ("  " + right if right else "")).rstrip())
    lines[0] = style(lines[0])
    return lines


def _footnotes(columns: list, unscored: list) -> list[str]:
    """Grade key, column legend, and tail notes as one block."""
    lines = ["  GRADE: ++ ≥90 · + ≥75 · o 60–75 · - 50–60 · -- <50 · · unranked"]
    legend = [
        f"{label.capitalize()} = {name}"
        for _key, label, name in columns
        if label != name
    ]
    if legend:
        lines.append("  " + " · ".join(legend))
    if unscored:
        lines.append(
            f"  {len(unscored)} with no benchmarks: "
            + ", ".join(sorted(m.name for m in unscored)[:10])
        )
    lines.append('  how a score is derived: README, "How a model\'s score is computed"')
    return lines


def run(args: argparse.Namespace) -> int:
    """Every model's score, one column per benchmark we score on.

    Each cell holds the raw number and where it lands on that benchmark's
    0-100 scale, because the raw numbers do not compare across benchmarks.
    How a model's score is derived from those cells is documented rather
    than printed: see the README's "How a model's score is computed" and
    ``scoring.py``. This command only reads: `fetch` owns every network call,
    including the arena Elo cache.
    """
    data_dir = Path(args.data)
    models, _curated, _boards, _merge_warnings = load_catalog(data_dir)
    table = scales(models)
    on_offer = availability.available_ids(data_dir)
    shown = _select(models, on_offer, getattr(args, "all_models", False))
    ranked, unscored = _ranked(shown, table)
    columns = _columns(models, table)

    # four counts, because "models with scores" used to mean "models with
    # any benchmark" - which overstated the scored field by a third - and
    # "on one benchmark" used to fold in models with no scored benchmark
    # at all
    scored, one, zerod = _counts(ranked, table)
    lines = [
        f"{len(models)} models · {len(columns)} benchmarks scored on · "
        f"{len(scored)} with scores · {len(one)} on one benchmark · "
        f"{len(zerod)} with none scored · {len(unscored)} without any",
        "",
    ]
    heads, grid = _grid(ranked, table, columns)
    lines.extend(_render_table(heads, grid))
    lines.append("")
    lines.extend(_footnotes(columns, unscored))
    print("\n".join(lines))
    return 0
