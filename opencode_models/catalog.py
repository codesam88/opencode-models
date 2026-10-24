"""Catalog composition: upstream records + curated overlays + official boards.

One loader composes the three record sources every read command needs, so
the row pipeline, ``scores`` and ``conflicts`` never drift apart on what
the catalog contains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .arena import ArenaCache, merge_arena_scores
from .boards import BoardRow, load_boards, merge_boards
from .identity import merge_aliases
from .models import Model, load_manual, load_models
from .overrides import Conflict, Curated, load_overrides, merge_curated


@dataclass(frozen=True)
class Composition:
    """One composed catalog: models plus the audit trail of how they merged."""

    models: list[Model]
    curated: Curated
    board_conflicts: list[Conflict] = field(default_factory=list)
    merge_warnings: tuple[str, ...] = ()


def compose_catalog(
    upstream: list[Model],
    manual: list[Model],
    overrides: dict,
    arena_scores: dict[str, float],
    board_rows: list[BoardRow],
) -> Composition:
    """Compose a catalog from already-loaded records; no I/O, no clock.

    Order is the contract: alias twins fold first, then curated
    overlays apply to canonical models once, then arena scores, then
    official boards. A designation-keyed overlay matches nothing
    post-merge and surfaces as an orphan instead of being silently
    dropped by plain-wins.
    """
    folded, merge_warnings = merge_aliases(upstream)
    curated = merge_curated(folded, manual, overrides)
    models = merge_arena_scores(curated.models, arena_scores)
    models, board_conflicts = merge_boards(models, board_rows)
    return Composition(
        models=models,
        curated=curated,
        board_conflicts=board_conflicts,
        merge_warnings=tuple(merge_warnings),
    )


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
    comp = compose_catalog(
        load_models(data_dir / "opencode"),
        load_manual(data_dir / "manual"),
        load_overrides(data_dir / "overrides"),
        ArenaCache(data_dir / "aiarena").load(),
        load_boards(data_dir / "boards"),
    )
    return comp.models, comp.curated, comp.board_conflicts, list(comp.merge_warnings)


__all__ = ["Composition", "compose_catalog", "curated_layer", "load_catalog"]
