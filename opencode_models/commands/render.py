"""Shared pure rendering: tables and headings, no I/O.

Each function takes plain values and returns strings; commands print
the result once at their edge. `print_conflicts` is the thin
print-wrapper the `fetch` progress path uses until it moves here.
"""

from __future__ import annotations

import sys

from ..overrides import source_tier
from ..tables import column_widths, format_row, render

#: `overrides` grid headings.
CONFLICT_HEADERS = ("Model", "Benchmark", "Catalog", "Reported", "Used", "Note")

RULE_WIDTH = 60

#: How much of a board row's provenance the NOTE column carries.
_NOTE_WIDTH = 52

#: Source tiers, short enough to sit beside a number without dominating the
#: column. The long names are in the footer rather than in every row.
_TIER_SHORT = {1: "indep", 2: "board", 3: "self"}

__all__ = [
    "CONFLICT_HEADERS",
    "bold_head",
    "column_label",
    "conflict_rows",
    "format_conflicts",
    "format_usage_tables",
    "print_conflicts",
    "rule",
    "style",
]


def style(text: str) -> str:
    """Bold when talking to a terminal, plain when piped or captured."""
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def bold_head(text: str) -> str:
    """Bold the first (header) line when talking to a terminal.

    Piped or captured output is untouched, so tests read plain text.
    """
    head, *rest = text.splitlines()
    return "\n".join([style(head), *rest])


def tier_word(source) -> str:
    return _TIER_SHORT[source_tier(source)]


def rule(title: str) -> str:
    """A section title with a closing rule, like the list view tables."""
    return f"── {title} " + "─" * max(4, RULE_WIDTH - len(title))


def format_usage_tables(sections, left_headers, right_headers) -> list[str]:
    """Week/Month window grids as text, one width set across sections.

    One width set fits both tables so the Week and Month grids line up
    with each other, the way the list view's tables do.
    """
    left_aligns = ("<", "<", ">")
    right_aligns = (">", ">", ">", ">", ">", "<")
    left_widths = column_widths(
        left_headers, [line for _, left, _ in sections for line in left]
    )
    right_widths = column_widths(
        right_headers, [line for _, _, right in sections for line in right]
    )
    lines = []
    for title, left_rows, right_rows in sections:
        lines.append("")
        lines.append(rule(title))
        lines.append(
            "  "
            + style(
                format_row(left_headers, left_widths, left_aligns, gap=" ").rstrip()
                + "   "
                + format_row(right_headers, right_widths, right_aligns, gap=" ")
            )
        )
        for left, right in zip(left_rows, right_rows, strict=True):
            lines.append(
                "  "
                + format_row(left, left_widths, left_aligns, gap=" ").rstrip()
                + "   "
                + format_row(right, right_widths, right_aligns, gap=" ")
            )
    return lines


def column_label(name: str) -> str:
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


def conflict_note(c) -> str:
    """A conflict's provenance, trimmed to one line.

    The board rows carry the run that produced the number - agent, effort,
    rank, CI and trial count - and that is the part worth reading, so it
    goes in the table rather than on a second line under it. A longer note
    is cut: the full record is in ``data/boards/``.
    """
    text = " ".join((c.reason or "").split())
    return text if len(text) <= _NOTE_WIDTH else text[: _NOTE_WIDTH - 1] + "…"


def conflict_rows(conflicts, orphans=(), manual_orphans=()) -> list:
    """Disagreements between two sources, as table rows with the value used.

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
                f"{c.upstream:g} {tier_word(c.upstream_source)}",
                f"{c.override:g} {tier_word(c.override_source)}",
                f"{c.used:g}",
                conflict_note(c),
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
    return rows


def format_conflicts(conflicts, orphans=(), manual_orphans=()) -> str:
    """The conflicts table as text, or "" when there is nothing to show."""
    rows = conflict_rows(conflicts, orphans, manual_orphans)
    if not rows:
        return ""
    return bold_head(render(CONFLICT_HEADERS, rows, aligns="<", indent="  "))


def print_conflicts(conflicts, orphans=(), manual_orphans=()) -> int:
    """Print the conflicts table; return the row count. Fetch's interim use."""
    text = format_conflicts(conflicts, orphans, manual_orphans)
    if text:
        print(text)
    return len(conflicts) + len(orphans) + len(manual_orphans)
