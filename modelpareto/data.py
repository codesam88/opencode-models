"""Load opencode.ai/data per-model JSON files into normalized records.

The scraper (see fetch.py) stores one JSON file per model exactly as served
by ``https://opencode.ai/data/<lab>/<model>.json``. This module turns those
files into :class:`Model` records; parsing is tolerant — a model without
pricing, without benchmarks, or a file that is not valid JSON at all must
not take down a whole render.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Model:
    """One model's identity, pricing and benchmark scores."""

    model_id: str
    name: str
    lab: str
    price_input: float | None
    price_output: float | None
    price_cached_input: float | None
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        """Filename-safe unique key (``deepseek/deepseek-v4-pro`` → dashes)."""
        return self.model_id.replace("/", "-")


def load_model(path: Path) -> Model:
    """Parse one scraped ``<lab>__<model>.json`` file."""
    doc = json.loads(path.read_text())
    m = doc["model"]
    price = m.get("priceUsdPer1M") or {}
    scores = {
        b["name"]: b["score"]
        for b in m.get("benchmarks") or []
        if isinstance(b.get("score"), (int, float))
    }
    return Model(
        model_id=m["id"],
        name=m.get("name") or m["id"],
        lab=m.get("lab") or m["id"].split("/")[0],
        price_input=price.get("input"),
        price_output=price.get("output"),
        price_cached_input=price.get("cachedInput"),
        scores=scores,
    )


def load_models(data_dir: Path) -> list[Model]:
    """Parse every ``*.json`` file in *data_dir*, skipping unreadable ones."""
    models: list[Model] = []
    for path in sorted(Path(data_dir).glob("*.json")):
        try:
            models.append(load_model(path))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return models
