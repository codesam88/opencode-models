"""Catalog composition: upstream records + curated overlays + official boards.

One loader composes the three record sources every read command needs, so
the row pipeline, ``scores`` and ``conflicts`` never drift apart on what
the catalog contains.
"""

from __future__ import annotations

from pathlib import Path

from .arena import merge_from_file
from .boards import load_boards, merge_boards
from .identity import merge_aliases
from .models import Model, load_manual, load_models
from .overrides import Conflict, Curated, load_overrides, merge_curated


def curated_layer(data_dir: Path) -> tuple[Curated, list[str]]:
    """Curated overlays over alias-merged upstream (single choke point).

    Twins fold before curation, so overlays apply to canonical models
    once: a designation-keyed overlay matches nothing and surfaces as
    an orphan instead of being silently dropped by plain-wins.
    """
    data_dir = Path(data_dir)
    upstream, merge_warnings = merge_aliases(load_models(data_dir / "opencode"))
    curated = merge_curated(
        upstream,
        load_manual(data_dir / "manual"),
        load_overrides(data_dir / "overrides"),
    )
    return curated, merge_warnings


def load_catalog(
    data_dir: Path,
) -> tuple[list[Model], Curated, list[Conflict], list[str]]:
    """Upstream records with curated overlays and official boards applied.

    Returns ``(models, curated, board_conflicts, merge_warnings)``: the merged model list,
    the curated layer (which models carry overrides, conflicts, orphans),
    the board-vs-registry disagreements, and alias-merge conflict warnings.
    Designation twins (``-free``/``-contributor``) fold before curation,
    so every consumer shares the same identity.
    """
    data_dir = Path(data_dir)
    curated, merge_warnings = curated_layer(data_dir)
    models = merge_from_file(curated.models, data_dir / "aiarena")
    models, board_conflicts = merge_boards(models, load_boards(data_dir / "boards"))
    return models, curated, board_conflicts, merge_warnings


__all__ = ["curated_layer", "load_catalog"]
