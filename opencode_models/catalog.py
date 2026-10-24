"""Catalog composition: upstream records + curated overlays + official boards.

One loader composes the three record sources every read command needs, so
the row pipeline, ``scores`` and ``conflicts`` never drift apart on what
the catalog contains.
"""

from __future__ import annotations

from pathlib import Path

from .arena import merge_from_file
from .boards import load_boards, merge_boards
from .models import Model
from .overrides import Conflict, Curated, load_curated


def load_catalog(
    data_dir: Path,
) -> tuple[list[Model], Curated, list[Conflict], list[str]]:
    """Upstream records with curated overlays and official boards applied.

    Returns ``(models, curated, board_conflicts, merge_warnings)``: the merged model list,
    the curated layer (which models carry overrides, conflicts, orphans),
    the board-vs-registry disagreements, and alias-merge conflict warnings.
    Designation twins (``-free``/``-contributor``) fold to one canonical
    model here, so every consumer shares the same identity.
    """
    data_dir = Path(data_dir)
    curated = load_curated(
        data_dir / "opencode", data_dir / "overrides", data_dir / "manual"
    )
    models = merge_from_file(curated.models, data_dir / "aiarena")
    models, board_conflicts = merge_boards(models, load_boards(data_dir / "boards"))
    from .identity import merge_aliases

    models, merge_warnings = merge_aliases(models)
    return models, curated, board_conflicts, merge_warnings


__all__ = ["load_catalog"]
