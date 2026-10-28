"""Shared pure rendering: tables and headings, no I/O.

Each function takes plain values and returns strings; commands print
the result once at their edge. `print_conflicts` is the thin
print-wrapper the `fetch` progress path uses until it moves here.
"""

from __future__ import annotations

import sys

from ..overrides import source_tier
from ..ranking import ratio
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
    "FIX_NOTE",
    "EST_NOTE",
    "GRADE_WIDTH",
    "bold_head",
    "column_label",
    "bench_tag",
    "cells",
    "conflict_rows",
    "cost_unit",
    "format_conflicts",
    "format_list_row",
    "format_usage_tables",
    "header",
    "headings",
    "next_headings",
    "pick_value",
    "price_note",
    "print_conflicts",
    "rule",
    "section",
    "style",
    "value_unit",
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


FIX_NOTE = "Pricing and score/$ rank based on fixed 20/60/20 tokens per request"
#: `est` cost base: ¢/req built from a model's token mix as the Go plan
#: page publishes it (``assumptions`` × base-tier prices). A row with no
#: mix of its own — zen's records carry none, nor does a model no record
#: prices — takes the Go plan's median instead, marked `~`; with no mix
#: published anywhere at all, the fix rate above.
EST_NOTE = (
    "¢/req and score/$ based on reported in/out/cache rates, fallback to median (~)"
)

#: Every row is prefixed by its rank marker, outside the column grid and
#: without a header of its own: ++ at 90+, + at 75+, o at 60+, - at 50+,
#: -- below that, · unranked (no score at all). The header line is indented
#: by the same width so the columns still line up.
GRADE_WIDTH = 5


def cost_unit(price: str) -> str:
    """The cost column's unit: ``fix`` and ``est`` count cents per request.

    ``input`` and ``output`` show raw per-1M-token rates instead.
    """
    return "¢/req" if price in ("fix", "est") else "¢/1m"


def price_note(price: str) -> str:
    """The cost-basis note for the headline line, per `--cost` mode."""
    if price == "est":
        return EST_NOTE
    if price == "input":
        return "Pricing and score/$ rank based on input $/1M rate"
    if price == "output":
        return "Pricing and score/$ rank based on output $/1M rate"
    return FIX_NOTE


#: The value column's two units. `SCORE_PER_DOLLAR` is the ranking's own
#: measure; `SCORE_PER_CENT` is that same ratio over a hundred, which is
#: the readable one once every value in a table clears 100.
SCORE_PER_DOLLAR = "Score/$"
SCORE_PER_CENT = "Score/¢"


def value_unit(values) -> str:
    """The value column's unit for one table, read off that table's values.

    Every value has to sit above 100 for score per cent to be the
    readable of the two: at or below it, dividing by a hundred rounds the
    value down to nothing. A `-` (a free or unscored line) and a `free`
    (a non-free line priced at zero, whose ratio is unbounded) carry no
    number that could hold the column at dollars. A table with no values
    at all has nothing to go on and keeps the ranking's own unit.
    """
    values = list(values)
    if not values:
        return SCORE_PER_DOLLAR
    for value in values:
        if value in ("-", "free"):
            continue
        if float(value) <= 100:
            return SCORE_PER_DOLLAR
    return SCORE_PER_CENT


def headings(axis: bool, price: str, unit: str = SCORE_PER_DOLLAR) -> tuple[str, ...]:
    """Column headings, title-cased; the cost unit follows the `--cost` mode.

    Two layouts, both built around the one score: the default shows the
    model's score beside its cost and plan, and ``--benchmark X`` ranks on
    one benchmark and adds that benchmark's tag. The per-axis raw numbers
    live in `scores`, not here. *unit* names the value column, which
    `value_unit` picks per table.
    """
    cost = cost_unit(price)
    if axis:
        return ("Score", cost, unit, "Prov")
    return ("Score", "Bench", cost, unit, "Prov")


def next_headings(price: str, unit: str = SCORE_PER_DOLLAR) -> tuple[str, ...]:
    """The `next` grid headings; the cost unit follows the `--cost` mode."""
    return ("#", "Plan", "Score", cost_unit(price), unit, "Endpoint", "Band")


def header(
    widths: tuple[int, ...],
    axis: bool = True,
    price: str = "est",
    unit: str = SCORE_PER_DOLLAR,
) -> str:
    """One grid header line, plain text: bolding happens at the print site."""
    return format_row(headings(axis, price, unit), widths) + "  Model"


def section(
    title: str,
    widths: tuple[int, ...],
    axis: bool = True,
    price: str = "est",
    unit: str = SCORE_PER_DOLLAR,
) -> list[str]:
    """Open a table: blank line, rule-wrapped title, then its column header.

    Each ── section is its own table, so it carries its own header below
    the rule; band labels and other intermediate headers live inside the
    table, under that header. *unit* is that table's value unit: one
    section may read score per cent while its neighbour stays at dollars.
    """
    return [
        "",
        rule(title),
        " " * GRADE_WIDTH + style(header(widths, axis, price, unit)),
    ]


def format_list_row(
    cells: tuple[str, ...], widths: tuple[int, ...], grade: str, name: str
) -> str:
    """One ranked row: grade marker, cells, display name."""
    return f"{grade:>{GRADE_WIDTH - 1}} " + format_row(cells, widths) + f"  {name}"


def pick_value(row, unit: str = SCORE_PER_DOLLAR) -> str:
    """A pick's score per dollar, free lines included.

    Free lines are really free (metered $0, throttled separately), so
    they read "-" for score/$ and sort ahead of every cost-based line
    ($0 is cheaper than any price); "-" is also what an unscored line
    reads, told apart by the 0 cost beside it.

    *unit* is the unit the column claims: score per cent is the same
    ratio over a hundred, because a value may never drift from the unit
    its header names. Which unit a table takes is `value_unit`'s call.
    """
    if row.score is None:
        return "-"
    if row.free:
        return "-"
    if row.price == 0:
        return "free"
    value = ratio(row.score, row.price)
    if unit == SCORE_PER_CENT:
        value /= 100.0
    return f"{round(value)}"


def bench_tag(benchmark: str | None) -> str:
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


def cells(
    row, axis: bool = True, cost: str = "est", unit: str = SCORE_PER_DOLLAR
) -> tuple[str, ...]:
    """The fixed columns of one row, as text: score, cost, value, plan.

    The score is the model's combined 0-100 number, rounded for the column;
    the value is score per dollar — free lines are metered at $0 and read
    "-" for score/$ ("free" only where a non-free line prices exactly
    zero, "-" with no score). *axis* is kept for the single-benchmark
    layout, which adds that benchmark's tag.

    The value is expressed in *unit*, the unit the header names: a value
    may not drift from the unit its column claims, so the two are chosen
    together by `value_unit`.

    The cost cell follows *cost*, the `--cost` mode: a ¢/req basis carries
    up to two decimals with trailing zeros stripped, so a fraction of a
    cent stays visible next to the integer it used to hide behind. The
    ¢/1m rate under ``input``/``output`` is whole cents, as before.
    """
    no_cost = row.free or row.price == 0
    if no_cost:
        price = "0"
    elif cost_unit(cost) == "¢/req":
        price = f"{row.price * 100:.2f}".rstrip("0").rstrip(".")
    else:
        price = f"{round(row.price * 100)}"
    score = "-" if row.score is None else f"{round(row.score)}"
    if axis:
        return (score, price, pick_value(row, unit), row.provider)
    return (
        score,
        bench_tag(row.benchmark),
        price,
        pick_value(row, unit),
        row.provider,
    )
