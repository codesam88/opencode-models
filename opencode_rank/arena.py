"""Arena.ai (LMArena) Elo scores, cached in data/ so every command except the
online lookups reads locally.

The live arena API is Cloudflare-blocked, so the lookup uses the community
copy on HuggingFace's datasets-server JSON API (no auth). Results are
folded into ``data/aiarena/scores.json`` — a {slug: elo} cache — and merged
into models at load time as an "LMArena Elo" benchmark axis.
"""

from __future__ import annotations

import json
from pathlib import Path

from .data import Model

CACHE_NAME = "scores.json"
ARENA_BENCHMARK = "LMArena Elo"
ROWS_URL = (
    "https://datasets-server.huggingface.co/rows?dataset="
    "openaccess-ai-collective/chatbot-arena-elo-scores&config=default"
    "&split=train&offset=0&length=100"
)


class ArenaCache:
    """Persistent {model slug: Elo} cache backed by one JSON file."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / CACHE_NAME

    def load(self) -> dict[str, float]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, scores: dict[str, float]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(scores, indent=1, sort_keys=True) + "\n")


def lookup_arena_elo(get) -> dict[str, float]:
    """Fetch {slug: elo} from the datasets-server rows API (paginated).

    *get* is the injected transport (``fetch.http_get`` in production).
    """
    scores: dict[str, float] = {}
    url: str | None = ROWS_URL
    while url:
        doc = json.loads(get(url))
        for row in doc.get("rows", []):
            r = row.get("row", {})
            name = r.get("chatbot_name")
            elo = r.get("elo_score")
            if name and isinstance(elo, (int, float)):
                scores[name] = float(elo)
        url = doc.get("next_page_url")
    return scores


def _match_key(m: Model) -> str:
    """Arena names are bare model ids (``deepseek-v4-pro``), so match on the
    last path segment of our ``lab/model`` id, not the lab-qualified slug."""
    return m.model_id.rsplit("/", 1)[-1]


def merge_arena_scores(models: list[Model], arena: dict[str, float]) -> list[Model]:
    """Attach LMArena Elo to models whose bare model id matches an arena
    entry.

    Only models that do not already carry an arena score are touched
    (cache wins), and a missing price does not disqualify the merge — the
    arena axis is usable for ranking even before pricing is known.
    """
    result = []
    for m in models:
        key = _match_key(m)
        if key in arena and ARENA_BENCHMARK not in m.scores:
            result.append(merge_arena_score(m, arena[key]))
        else:
            result.append(m)
    return result


def merge_arena_score(m: Model, elo: float) -> Model:
    """Return a copy of *m* with the arena score attached."""
    scores = dict(m.scores)
    scores[ARENA_BENCHMARK] = elo
    return Model(
        model_id=m.model_id,
        name=m.name,
        lab=m.lab,
        price_input=m.price_input,
        price_output=m.price_output,
        price_cached_input=m.price_cached_input,
        scores=scores,
        sources=dict(m.sources),
    )


def merge_from_file(models: list[Model], data_dir: Path) -> list[Model]:
    """Model list with any cached arena scores applied."""
    cache = ArenaCache(data_dir)
    return merge_arena_scores(models, cache.load())
