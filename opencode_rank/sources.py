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


def members(models: list[Model], slugs: set[str] | None
            ) -> tuple[list[Model], set[str]]:
    """Tier membership: the models in a tier, plus which of them are free.

    The tier pages list free variants under their own slug
    (``mimo-v2.6-flash-free``, ``step-5-preview-free``) while the catalog
    carries one model under the base slug, so ``X-free`` has to reach ``X``.
    A member reached through a ``-free`` slug — or priced at zero — is free.
    """
    if slugs is None:  # `--source all`: no tier filter
        return (list(models),
                {m.model_id for m in models if m.price_input == 0})
    included: list[Model] = []
    free: set[str] = set()
    for m in models:
        slug = m.model_id.split("/")[-1]
        if slug in slugs or m.model_id in slugs:
            matched_free = False
        elif f"{slug}-free" in slugs:
            matched_free = True
        else:
            continue
        included.append(m)
        if matched_free or m.price_input == 0:
            free.add(m.model_id)
    return included, free
