"""Model markers: symbols appended to display names, decided from source data.

Each marker has a stable key, a one-character symbol, and a legend
fragment. Markers are decided where catalog, roster, and pricing meet
(:func:`rows_for` attaches marker *keys*); views only render them with
:func:`suffix` and :func:`legend`, and :func:`median_note` states the
median case as a footnote where a symbol would not fit.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass


@dataclass(frozen=True)
class Marker:
    symbol: str
    blurb: str


MARKERS: dict[str, Marker] = {
    "curated": Marker("*", "curated overlay"),
    "median": Marker("~", "assumes median in/out/cache distribution"),
}

__all__ = ["MARKERS", "Marker", "legend", "median_note", "suffix"]


def suffix(keys: tuple[str, ...] | list[str]) -> str:
    """The symbols for marker keys, space-separated for appending to a name.

    Registry order wins over caller order, so the suffix is deterministic
    however the keys arrived.
    """
    present = set(keys)
    symbols = [marker.symbol for key, marker in MARKERS.items() if key in present]
    return (" " + "".join(symbols)) if symbols else ""


def legend(keys: tuple[str, ...] | list[str]) -> str:
    """One legend line for the markers present, or "" when there are none."""
    shown = [k for k in MARKERS if k in set(keys)]
    if not shown:
        return ""
    entries = [f"{MARKERS[k].symbol} {MARKERS[k].blurb}" for k in shown]
    return "Markers: " + " · ".join(entries)


def median_note(slugs: list[str], width: int = 80) -> list[str]:
    """Footnote naming models whose estimate used the plan median.

    One specific note, not a generic formatter: the sentence is fixed
    (models marked, reason given) and only the wrapping is parametric.
    """
    if not slugs:
        return []
    text = (
        "Note: "
        + ", ".join(slugs)
        + " estimated with the plan median (no published assumptions)"
    )
    return textwrap.wrap(text, width=width, subsequent_indent="  ")
