"""models.dev: the go/zen catalog, fetched by ``sync`` as a T2 layer.

models.dev is an open database of models and providers (MIT,
github.com/anomalyco/models.dev). Only the two opencode providers are
kept: ``opencode-go`` prices the go rows, ``opencode`` the zen rows, so a
tier never borrows the other tier's prices. A record carries what the
views need from a catalog -- id, name, cost per 1M tokens, limits -- and
``canonical_model_id`` joins it to the opencode.ai card that carries its
benchmark scores.

The snapshot is the authority for which models a tier renders: a served
slug with no record here is not shown. Without a snapshot (fresh clone,
offline) the views fall back to the cards, as before.
"""

from __future__ import annotations

import json
from datetime import datetime as _datetime
from datetime import timedelta, timezone
from pathlib import Path

from .fetch import http_get
from .freshness import MODELSDEV_MAX_AGE, snapshot_age
from .models import Model

#: Where the catalog is fetched from; the payload holds many providers and
#: only the two in ``PROVIDERS`` are stored.
API_URL = "https://models.dev/api.json"

#: Console tier -> models.dev provider id.
PROVIDERS = {"go": "opencode-go", "zen": "opencode"}

#: Snapshot layer name for :func:`opencode_models.freshness.snapshot_age`.
SOURCE = "modelsdev"

#: File the slice is written to, relative to the data dir.
FILENAME = "modelsdev.json"

SCHEMA_REV = 1


def _path(data_dir) -> Path:
    return Path(data_dir) / FILENAME


def _age_str(age: timedelta) -> str:
    total = int(age.total_seconds())
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h"
    return f"{total // 86400}d"


def refresh(data_dir, get=None, force: bool = False):
    """Fetch the opencode slice into the snapshot when it is stale.

    Returns ``(status, messages, age)`` where status is ``"fresh"``,
    ``"refreshed"`` or ``"kept"`` -- the shape ``sync`` prints for the
    other layers. A failed or unusable fetch keeps the last snapshot and
    reports a warning: the catalog is a T2 source, so losing it must not
    fail the run.
    """
    data_dir = Path(data_dir)
    age = snapshot_age(data_dir, SOURCE)
    if not force and age is not None and age <= MODELSDEV_MAX_AGE:
        return "fresh", (f"{SOURCE}: up-to-date ({_age_str(age)})",), age
    get = get or http_get
    try:
        payload = json.loads(get(API_URL))
    except (OSError, ValueError) as exc:
        return (
            "kept",
            (
                f"warning: models.dev unavailable ({exc})",
                f"{SOURCE}: kept existing snapshot",
            ),
            age,
        )
    if not isinstance(payload, dict):
        payload = {}
    providers = {}
    missing = []
    for provider in PROVIDERS.values():
        block = payload.get(provider)
        models = block.get("models") if isinstance(block, dict) else None
        if not isinstance(models, dict) or not models:
            missing.append(provider)
            continue
        providers[provider] = block
    if missing:
        return (
            "kept",
            (
                f"warning: models.dev slice incomplete ({', '.join(missing)})",
                f"{SOURCE}: kept existing snapshot",
            ),
            age,
        )
    fetched_at = _datetime.now(timezone.utc).isoformat(timespec="seconds")
    _path(data_dir).write_text(
        json.dumps(
            {
                "meta": {
                    "source": API_URL,
                    "fetched_at": fetched_at,
                    "schema_rev": SCHEMA_REV,
                },
                "providers": providers,
            },
            indent=1,
        )
    )
    count = sum(len(block.get("models") or {}) for block in providers.values())
    return "refreshed", (f"{SOURCE}: refreshed ({count} models)",), age


def load(data_dir) -> dict[str, dict[str, dict]] | None:
    """The slice by tier (``{"go": …, "zen": …}``), or None when absent.

    None means "never fetched" -- callers fall back rather than reading
    an empty catalog as "this tier sells nothing".
    """
    try:
        raw = json.loads(_path(data_dir).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        return None
    out: dict[str, dict[str, dict]] = {}
    for tier, provider in PROVIDERS.items():
        block = providers.get(provider)
        models = block.get("models") if isinstance(block, dict) else None
        out[tier] = models if isinstance(models, dict) else {}
    return out


def to_models(
    records: dict, cards: list[Model], aliases: dict | None = None
) -> tuple[list[Model], dict[str, str]]:
    """Model rows for *records*, benchmark scores joined from cards.

    Prices come from the record (USD per 1M tokens, ``cache_read`` as the
    cached-input rate). Scores do not exist here, so the card a record
    names -- by ``canonical_model_id``, else by bare slug, else by a
    curated *aliases* entry -- contributes its scores and sources, which
    by then already carry the curated overlays and boards applied by the
    catalog loader.

    Returns ``(models, card_ids)``: ``card_ids`` maps a record id to the
    card it matched, so callers can still recognise the record's model
    where the catalog is keyed by card ids (curated markers).
    """
    by_id = {m.model_id: m for m in cards}
    by_slug = {m.model_id.split("/")[-1].lower(): m for m in cards}
    out: list[Model] = []
    card_ids: dict[str, str] = {}
    for slug, rec in records.items():
        if not isinstance(rec, dict):
            continue
        canonical = rec.get("canonical_model_id")
        card = by_id.get(canonical) if isinstance(canonical, str) else None
        card = card or by_slug.get(slug.lower())
        if card is None and aliases:
            # a curated alias is the manual form of canonical_model_id:
            # it points a slug the record cannot name at its card
            alias = aliases.get(slug)
            if isinstance(alias, dict) and isinstance(alias.get("model_id"), str):
                card = by_id.get(alias["model_id"])
        if card is not None:
            card_ids[slug] = card.model_id
        cost = rec.get("cost") if isinstance(rec.get("cost"), dict) else {}
        lab = (
            canonical.split("/")[0]
            if isinstance(canonical, str) and "/" in canonical
            else (rec.get("family") or "")
        )
        out.append(
            Model(
                # the record's own id, not its canonical form: this is the
                # key plan records, the roster and the availability snapshot
                # all speak, so pricing and membership join without a rewrite
                model_id=slug,
                name=rec.get("name") or slug,
                lab=lab,
                price_input=cost.get("input"),
                price_output=cost.get("output"),
                price_cached_input=cost.get("cache_read"),
                scores=dict(card.scores) if card else {},
                sources=dict(card.sources) if card else {},
            )
        )
    return out, card_ids


__all__ = [
    "API_URL",
    "FILENAME",
    "PROVIDERS",
    "SOURCE",
    "load",
    "refresh",
    "to_models",
]
