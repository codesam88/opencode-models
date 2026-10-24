"""`models` command: the models the plans offer right now, per tier."""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import availability
from ..identity import canonical_id
from ..models import load_models
from ..tables import render
from . import fail
from .render import bold_head

#: `models` grid headings, with and without the availability column.
MODELS_HEADERS = ("Endpoint", "Model")
MODELS_ALL_HEADERS = ("Avail", "Endpoint", "Model")

#: The no-key stale notice this command never printed: the origin line
#: already says the view runs from snapshots.
_STALE_NOTICE = "no API token found"


def _display_names(data_dir: Path) -> dict[str, str]:
    """Display names by slug or id.

    Catalog records come first; plan snapshots and tier-price entries add
    names for slugs the catalog does not carry (docs-documented orphans),
    so a served model the plans offer but the catalog never named can still
    be shown. Internal ids with no documentation stay unnamed and hidden.
    """
    names = {}
    for model in load_models(Path(data_dir) / "opencode"):
        names[model.model_id] = model.name
        names[model.model_id.split("/")[-1]] = model.name
    for tier in ("go", "zen"):
        for slug, rec in availability._plan_models(data_dir, tier).items():
            if isinstance(rec, dict) and rec.get("name"):
                names.setdefault(slug, rec["name"])
                names.setdefault(slug.split("/")[-1], rec["name"])
    return names


def _model_name(names: dict[str, str], slug: str) -> str:
    """The catalog's display name for a tier slug, or the slug itself.

    Tier pages list free variants under their own slug
    (``step-5-preview-free``) while the catalog carries the base model,
    so the ``-free`` suffix reaches back to it.
    """
    if slug in names:
        return names[slug]
    base = canonical_id(slug)
    if base in names:
        return names[base]
    return slug


def _status_label(ids, slug: str) -> str:
    """The API status marker for one row: [x] served, [ ] not, [?] unknown.

    *ids* is a live status mapping (slug -> bool) or a bare slug set from
    a docs snapshot — sets carry no per-model status, so the honest
    marker there is [?] rather than an invented one.
    """
    if not isinstance(ids, dict):
        return "[?]"
    value = ids.get(slug)
    return "[x]" if value is True else "[ ]" if value is False else "[?]"


def _catalog_known(slug: str, names: dict[str, str]) -> bool:
    """Whether the catalog can name *slug* (free variants reach each other)."""
    names_canonical = {canonical_id(k) for k in names}
    return slug in names or canonical_id(slug) in names_canonical


def _roster(
    tiers, names: dict[str, str], *, show_unserved: bool
) -> dict[str, tuple[str, str]]:
    """Which slugs the view shows: slug -> (tier, status marker).

    Served models only unless *show_unserved* adds the known-unserved
    ``[ ]`` rows; where both tiers carry a slug, ``go`` wins; rows no
    source can name (internal ids like ``test``) stay out.
    """
    by_slug: dict[str, tuple[str, str]] = {}
    for tier in sorted(tiers):
        ids = tiers[tier]
        for slug in sorted(ids):
            marker = _status_label(ids, slug)
            if marker == "[ ]" and not show_unserved:
                continue  # known-unserved: an --all view only
            if not _catalog_known(slug, names):
                continue  # internal ids (test, *-backup) are not ours to show
            if slug not in by_slug or tier == "go":
                by_slug[slug] = (tier, marker)
    return by_slug


def _rows(
    by_slug: dict[str, tuple[str, str]], names: dict[str, str]
) -> list[tuple[str, str, str]]:
    """Display rows as (endpoint, name, marker), sorted by name then endpoint."""
    rows = []
    for slug, (tier, marker) in by_slug.items():
        endpoint = f"opencode-go/{slug}" if tier == "go" else f"opencode/{slug}"
        name = _model_name(names, slug)
        rows.append((endpoint, name if name != slug else "-", marker))
    rows.sort(key=lambda row: (row[1].lower(), row[0]))
    return rows


def _summary(tiers, by_slug: dict[str, tuple[str, str]], unknown) -> str:
    """The headline counts: rows shown, per-tier offering sizes, unknown tiers."""
    counts = " + ".join(f"{len(tiers[t])} {t}" for t in sorted(tiers))
    return f"{len(by_slug)} models · {counts}" + (
        f" · {', '.join(unknown)} unknown" if unknown else ""
    )


def _table(rows: list[tuple[str, str, str]], *, show_unserved: bool) -> str:
    """The grid as text; the status marker column is an --all extra."""
    if show_unserved:
        head = list(MODELS_ALL_HEADERS)
        view = [(marker, endpoint, name) for endpoint, name, marker in rows]
    else:
        head = list(MODELS_HEADERS)
        view = [(endpoint, name) for endpoint, name, _marker in rows]
    return bold_head(render(head, view, aligns=("<",) * len(head), indent="  "))


def run(args: argparse.Namespace) -> int:
    """The models the plans offer right now, per tier.

    Live from the inference gateway's model lists (served = callable)
    plus the config API's known set, merged to ``available/`` on the way
    so `list` and `next` inherit the liveness. Default rows are the
    served models only; ``--all`` adds known-but-not-served rows, marked
    ``[ ]``. Offline: the snapshots the last ``fetch`` left behind. Rows
    the catalog cannot name (internal ids like ``test``) are hidden.
    """
    data_dir = Path(args.data)
    result = availability.current_offered(data_dir, bypass=False)
    lines = [w for w in result.warnings if not w.startswith(_STALE_NOTICE)]
    if result.tiers is None:
        if lines:
            print("\n".join(lines))
        return fail(
            f"no availability known in {data_dir} — run `fetch` "
            f"first (with OPENCODE_API_KEY for the live set)"
        )
    names = _display_names(data_dir)
    by_slug = _roster(result.tiers, names, show_unserved=args.all_models)
    rows = _rows(by_slug, names)
    lines.append(result.origin)
    lines.append(_summary(result.tiers, by_slug, result.unknown))
    lines.append("")  # the grid sits under the headline, not against it
    # the status marker column is an --all extra; default is endpoint + name
    lines.append(_table(rows, show_unserved=args.all_models))
    print("\n".join(lines))
    return 0
