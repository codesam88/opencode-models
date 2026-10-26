"""How old a data source is — the single freshness primitive.

Storage owns every on-disk layer, so it is the only place that may answer
"how old is this source?". This module exposes that one query and nothing
else: callers compare the returned age against their own thresholds.

A source name identifies a snapshot layer: ``available``, ``available/<tier>``,
``plans``, ``plans/<tier>``, ``boards``, ``boards/<name>``, ``arena``, or
``modelsdev``. A layer that was never fetched yields ``None``; a layer present only as a
legacy bare payload (no ``meta`` envelope) falls back to the file's mtime,
as does a corrupt ``meta`` block. Unknown source names raise ``KeyError``
immediately rather than being guessed at.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Per-source staleness thresholds: T2 ~4h availability / ~8h pricing.
# Owned here (Storage freshness policy) so both the sync flow and the
# lazy trigger in read paths share one definition. See
# .plan/architecture-refactoring.md and docs/architecture.md section 7.
AVAILABILITY_MAX_AGE = timedelta(hours=4)
PLANS_MAX_AGE = timedelta(hours=8)
#: The models.dev catalog slice moves daily; a day old still is the catalog.
MODELSDEV_MAX_AGE = timedelta(hours=24)


def _parse_iso(value):
    """An ISO-8601 timestamp as aware UTC, or None when unparseable."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _file_age(path: Path, now: datetime) -> timedelta | None:
    """Age of one snapshot file: meta.fetched_at, else its mtime."""
    if not path.is_file():
        return None
    fetched = None
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, ValueError):
        raw = None
    if isinstance(raw, dict):
        meta = raw.get("meta")
        if isinstance(meta, dict):
            fetched = _parse_iso(meta.get("fetched_at"))
    if fetched is None:
        try:
            fetched = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return None
    return now - fetched


def _layer_paths(data_dir: Path, source: str) -> list[Path]:
    """Resolve a source name to the file(s) that carry its snapshot."""
    data_dir = Path(data_dir)
    if source == "available":
        directory = data_dir / "available"
        return sorted(directory.glob("*.json")) if directory.is_dir() else []
    if source.startswith("available/"):
        tier = source[len("available/") :]
        if not tier or "/" in tier:
            raise KeyError(source)
        return [data_dir / "available" / f"{tier}.json"]

    if source == "plans":
        directory = data_dir / "plans"
        return sorted(directory.glob("*.json")) if directory.is_dir() else []
    if source.startswith("plans/"):
        tier = source[len("plans/") :]
        if not tier or "/" in tier:
            raise KeyError(source)
        return [data_dir / "plans" / f"{tier}.json"]

    if source == "boards":
        directory = data_dir / "boards"
        return sorted(directory.glob("*.json")) if directory.is_dir() else []
    if source.startswith("boards/"):
        name = source[len("boards/") :]
        if not name or "/" in name:
            raise KeyError(source)
        path = data_dir / "boards" / name
        if not path.suffix:
            path = path.with_suffix(".json")
        return [path]

    if source == "arena":
        return [data_dir / "aiarena" / "scores.json"]

    if source == "modelsdev":
        return [data_dir / "modelsdev.json"]

    raise KeyError(source)


def snapshot_age(data_dir: Path, source: str) -> timedelta | None:
    """Age of the *source* snapshot layer, or None if never fetched.

    *source* names a snapshot layer: ``available``, ``available/<tier>``,
    ``plans``, ``plans/<tier>``, ``boards``, ``boards/<name>``, or
    ``arena``. The layer's age is its oldest component's age, so a
    multi-file layer is as old as its oldest part. Unknown source names
    raise ``KeyError``.
    """
    now = datetime.now(timezone.utc)
    ages = []
    for path in _layer_paths(data_dir, source):
        age = _file_age(path, now)
        if age is not None:
            ages.append(age)
    if not ages:
        return None
    return max(ages)


__all__ = ["snapshot_age"]
