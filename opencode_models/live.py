"""Live availability from the Console config API.

``GET /api/v2/config`` answers with the workspace's live model set, per
provider — the same plans the docs describe, but as the API serves them
right now rather than as the pricing pages last said. The docs snapshot
(``tier_prices/``) stays the offline fallback; this layer wins wherever it
exists, because the API is what the next request is actually checked
against.

Reading needs a service-account key (``OPENCODE_API_KEY``); the caller
builds the authed transport, so this module never touches the environment.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

#: The workspace's live model set, with tiered cost arrays and limits.
LIVE_CONFIG_URL = "https://opencode.ai/console/api/v2/config"

#: Config providers to console tiers. Anything else the API lists is not a
#: tier this tool ranks, so it is ignored rather than guessed at.
LIVE_TIERS = {"opencode": "zen", "opencode-go": "go"}

#: Where ``fetch`` snapshots the live set; ``models`` reads it offline.
SNAPSHOT_DIR = "available"


#: The inference gateway's model lists — the live truth for availability.
#: Presence on the list = servable by this key; the endpoint is public but
#: key-scoped (an anonymous zen call returns the catalog-wide list, a keyed
#: one this workspace's entitlements), so always call it authed.
SERVED_URLS = {
    "zen": "https://opencode.ai/inference/v1/models",
    "go": "https://opencode.ai/inference/go/v1/models",
}


def fetch_served(get) -> dict[str, set[str]]:
    """Model ids the inference gateway serves per tier, keyed by tier.

    *get* is an authed ``(url) -> str`` callable. Entries carry the
    OpenAI ``data[].id`` shape; malformed entries are skipped rather
    than trusted.
    """
    out: dict[str, set[str]] = {}
    for tier, url in SERVED_URLS.items():
        payload = json.loads(get(url))
        out[tier] = {
            item["id"]
            for item in (payload.get("data") or [])
            if isinstance(item, dict) and item.get("id")
        }
    return out


def fetch_live(get) -> dict[str, dict[str, bool]]:
    """Model slugs each tier offers right now, mapped to their disabled
    flag, keyed by tier.

    *get* is an authed ``(url) -> str`` callable (the endpoint is
    person-free but key-gated). Unknown providers are ignored; a provider
    with no models entry counts as offering nothing, not as unknown. The
    revised config marks withdrawn models with ``disabled: true`` — the
    flag rides along so the console can show status, and availability
    consumers decide separately what to do with it.
    """
    payload = json.loads(get(LIVE_CONFIG_URL))
    providers = payload.get("providers") or {}
    out: dict[str, dict[str, bool]] = {}
    for name, tier in LIVE_TIERS.items():
        models = (providers.get(name) or {}).get("models") or {}
        out[tier] = {
            slug: bool(spec.get("disabled")) if isinstance(spec, dict) else False
            for slug, spec in models.items()
        }
    return out


def save_live(data_dir: Path, live: dict[str, dict[str, bool]]) -> list[str]:
    """Snapshot the live set with statuses to ``available/<tier>.json``."""
    saved = []
    for tier, statuses in live.items():
        directory = Path(data_dir) / SNAPSHOT_DIR
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{tier}.json"
        path.write_text(
            json.dumps(
                {
                    "meta": {
                        "source": LIVE_CONFIG_URL,
                        "fetched_at": datetime.now(timezone.utc).isoformat(
                            timespec="seconds"
                        ),
                    },
                    "models": {slug: statuses[slug] for slug in sorted(statuses)},
                },
                indent=1,
            )
        )
        saved.append(path.name)
    return saved


def merge_served(
    served: dict[str, set[str]], known: dict[str, dict[str, bool]]
) -> dict[str, dict[str, bool]]:
    """Union of what the gateway serves and what config knows, per tier.

    Stored semantics: True = served right now, False = known to config
    but not served (the stale legacy set), absent = nobody knows. The
    config entry's own disabled flag is discarded — served-ness is the
    availability truth.
    """
    out: dict[str, dict[str, bool]] = {}
    for tier in sorted(set(served) | set(known)):
        status = {slug: False for slug in known.get(tier) or {}}
        for slug in served.get(tier) or set():
            status[slug] = True
        out[tier] = status
    return out


def load_live(data_dir: Path) -> dict[str, dict[str, bool | None] | None]:
    """The live snapshot by tier: slug -> disabled flag; None if absent.

    None tier means "never fetched live", not "the plan offers nothing".
    Files written before the flag existed stored a bare slug list; those
    load as ``{slug: None}`` — status unknown rather than invented.
    """
    out = {}
    for tier in sorted(LIVE_TIERS.values()):
        path = Path(data_dir) / SNAPSHOT_DIR / f"{tier}.json"
        if not path.exists():
            out[tier] = None
            continue
        try:
            models = json.loads(path.read_text()).get("models")
        except (json.JSONDecodeError, OSError, AttributeError):
            out[tier] = None
            continue
        if isinstance(models, list):
            out[tier] = {slug: None for slug in models}
        elif isinstance(models, dict):
            out[tier] = {
                slug: value if isinstance(value, bool) else None
                for slug, value in models.items()
            }
        else:
            out[tier] = None
    return out


def api_get(key: str):
    """Authed transport for the key-gated Console API (30s per request)."""

    def get(url: str) -> str:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "User-Agent": f"opencode-models/{__version__}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            exc.close()
            raise

    return get
