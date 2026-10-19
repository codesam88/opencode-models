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
from datetime import datetime, timezone
from pathlib import Path

#: The workspace's live model set, with tiered cost arrays and limits.
LIVE_CONFIG_URL = "https://opencode.ai/console/api/v2/config"

#: Config providers to console tiers. Anything else the API lists is not a
#: tier this tool ranks, so it is ignored rather than guessed at.
LIVE_TIERS = {"opencode": "zen", "opencode-go": "go"}

#: Where ``fetch`` snapshots the live set; ``models`` reads it offline.
SNAPSHOT_DIR = "available"


def fetch_live(get) -> dict[str, set[str]]:
    """Model slugs each tier offers right now, keyed by tier.

    *get* is an authed ``(url) -> str`` callable (the endpoint is
    person-free but key-gated). Unknown providers are ignored; a provider
    with no models entry counts as offering nothing, not as unknown.
    """
    payload = json.loads(get(LIVE_CONFIG_URL))
    providers = payload.get("providers") or {}
    return {tier: set((providers.get(name) or {}).get("models") or {})
            for name, tier in LIVE_TIERS.items()}


def save_live(data_dir: Path, live: dict[str, set[str]]) -> list[str]:
    """Snapshot the live set to ``available/<tier>.json``; returns files."""
    saved = []
    for tier, slugs in live.items():
        directory = Path(data_dir) / SNAPSHOT_DIR
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{tier}.json"
        path.write_text(json.dumps({
            "meta": {
                "source": LIVE_CONFIG_URL,
                "fetched_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"),
            },
            "models": sorted(slugs),
        }, indent=1))
        saved.append(path.name)
    return saved


def load_live(data_dir: Path) -> dict[str, set[str] | None]:
    """The live snapshot, by tier; None where there is none.

    None means "never fetched live", not "the plan offers nothing".
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
        out[tier] = set(models) if isinstance(models, list) else None
    return out
