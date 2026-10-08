"""Membership lists for the OpenCode Console tiers (zen = curated, go = open).

Slugs are bare model ids (``claude-opus-5``) as listed on the tier pages,
shipped as text files beside this module. Matching against the catalog
happens on the slug (last path segment of ``lab/model``).
"""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent / "sources"


def available_sources() -> list[str]:
    """Tier names that have a shipped membership file."""
    return sorted(p.stem for p in _DIR.glob("*.txt"))


def load_source(name: str) -> set[str]:
    """Slugs of models in tier *name*; raises KeyError for unknown tiers."""
    path = _DIR / f"{name}.txt"
    if not path.exists():
        raise KeyError(name)
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}
