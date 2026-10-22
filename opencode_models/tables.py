"""Aligned text tables from declarative input.

Every console view was hand-rolling the same loop: measure the widest
cell per column (header included), pad, join, print — with the widths
and the alignment re-decided at each site, so a wide cell silently
broke the grid instead of widening it. These helpers take headers plus
rows of plain strings and return aligned text; the views only declare
*what* goes in each column, never how wide it is.
"""

from __future__ import annotations

from collections.abc import Sequence


def column_widths(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    min_widths: Sequence[int] = (),
) -> list[int]:
    """The display width of each column: header, cells and floor combined."""
    count = max([len(headers)] + [len(r) for r in rows] or [0])
    widths = []
    for i in range(count):
        cells = [r[i] for r in rows if len(r) > i]
        floor = min_widths[i] if i < len(min_widths) else 0
        widths.append(
            max(
                [len(headers[i]) if i < len(headers) else 0, floor]
                + [len(c) for c in cells]
                or [0]
            )
        )
    return widths


def _pad(cell: str, width: int, align: str) -> str:
    return cell.ljust(width) if align == "<" else cell.rjust(width)


def _aligns_for(aligns: str | Sequence[str], count: int) -> list[str]:
    if isinstance(aligns, str):
        return [aligns] * count
    return list(aligns) + [">"] * (count - len(aligns))


def format_row(
    cells: Sequence[str],
    widths: Sequence[int],
    aligns: str | Sequence[str] = ">",
    gap: str = "  ",
) -> str:
    """One grid line: padded cells joined, trailing padding stripped.

    Short rows pad out with blanks; a row wider than the grid is a caller
    bug and raises instead of silently dropping a column.
    """
    if len(cells) > len(widths):
        raise ValueError(
            f"row has {len(cells)} cells for {len(widths)} columns: {cells!r}"
        )
    padded = [cells[i] if i < len(cells) else "" for i in range(len(widths))]
    return gap.join(
        _pad(c, w, a)
        for c, w, a in zip(
            padded, widths, _aligns_for(aligns, len(widths)), strict=True
        )
    ).rstrip()


def format_group_row(
    labels: Sequence[tuple[int, str]],
    widths: Sequence[int],
    gap: str = "  ",
) -> str:
    """One group-header line above the column headers.

    *labels* pairs each label with the span of columns it covers; the
    label is centered over its span and the spans must cover the grid
    exactly. A label wider than its span would break the grid, so it
    raises like a too-wide row.
    """
    covered = sum(span for span, _ in labels)
    if covered != len(widths):
        raise ValueError(
            f"group spans cover {covered} columns for {len(widths)}: {labels!r}"
        )
    parts = []
    column = 0
    for span, label in labels:
        span_width = sum(widths[column : column + span]) + len(gap) * (span - 1)
        if len(label) > span_width:
            raise ValueError(
                f"group label {label!r} wider than its {span_width}-column span"
            )
        parts.append(label.center(span_width))
        column += span
    return gap.join(parts).rstrip()


def render(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    aligns: str | Sequence[str] = ">",
    header_aligns: str | Sequence[str] | None = None,
    min_widths: Sequence[int] = (),
    indent: str = "",
    gap: str = "  ",
    group: Sequence[tuple[int, str]] | None = None,
) -> str:
    """Headers plus rows as aligned text, one line each, no trailing newline.

    Columns measure across the headers and every row, so a wide cell
    widens its column instead of breaking the grid; *min_widths* holds
    narrow columns open where a fixed layout is wanted. Short rows pad
    out with blanks. *group*, when given, adds a centered group-header
    row above the headers (see format_group_row). Every line is
    *indent*ed and right-stripped.
    """
    widths = column_widths(headers, rows, min_widths)
    lines = []
    if group is not None:
        lines.append(format_group_row(group, widths, gap))
    lines.append(
        format_row(
            headers, widths, header_aligns if header_aligns is not None else aligns, gap
        )
    )
    lines += [format_row(row, widths, aligns, gap) for row in rows]
    return "\n".join(indent + line for line in lines)
