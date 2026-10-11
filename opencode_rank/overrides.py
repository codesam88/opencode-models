"""Curated score overlays layered over the fetched upstream records.

``data/opencode/`` is verbatim upstream (``fetch`` writes only there) and
``data/overrides/`` is hand-written: one sparse JSON per model carrying only
the entries we vouched for — never a full copy. Both are merged at load
time here, keyed by (benchmark name, version), so an override can add a
score upstream lacks (Grok 4.6's Terminal-Bench 2.1, measured by
Artificial Analysis but absent from the registry) or pin a value upstream
also carries (Grok 4.7's 4.0 number, where we prefer the independent run
over the vendor's own).

Not every source means the same thing. A score carries more cross-model
meaning when one evaluator ran every model on one harness than when a lab
published its own best-scaffold run, so each entry is tiered by where it
comes from and the override layer only overrides when it is at least as
good a source — otherwise the disagreement is surfaced, not silently
applied. :func:`load_curated` returns those disagreements for the
``conflicts`` command.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from .data import Model, benchmark_key, load_models

#: Independent evaluator that runs every model on one harness — the only
#: regime where cross-model differences are model differences.
TIER_INDEPENDENT = 1
#: Official benchmark leaderboard: standardized tasks and intervals, but the
#: agent scaffold is the submitter's choice (recorded per row, auditable).
TIER_OFFICIAL = 2
#: The lab's own model card / launch post: best scaffold, cherry-picked.
TIER_SELF_REPORTED = 3

TIER_LABELS = {
    TIER_INDEPENDENT: "independent",
    TIER_OFFICIAL: "official board",
    TIER_SELF_REPORTED: "self-reported",
}

#: Hosts whose runs are independent (they evaluate, not publish).
INDEPENDENT_HOSTS = ("artificialanalysis.ai",)
#: Hosts that run the official benchmark submission boards.
OFFICIAL_HOSTS = ("tbench.ai", "www.tbench.ai", "www.swebench.com",
                  "swebench.com")

OVERRIDES_DIR = "overrides"

#: The Terminal-Bench 2.1 → 4.0 band, fitted on the models whose vendor
#: cards carry *both* versions (tb4 ≈ 1.131·tb21 − 71.39, R² ≈ 0.77,
#: residuals up to ±10 points). A smoke alarm, never a projection: a score
#: this far off the band is a prompt to check who ran it, not a value to
#: substitute — the residuals are as large as the differences that matter.
TB40_BAND = (1.131, -71.39)
#: How far off the band a pair must sit before it is worth a look.
TB40_BAND_SLACK = 10.0


def source_tier(source: str | None) -> int:
    """Tier of a benchmark entry's source URL.

    Independent evaluators come first (same harness for every model, so the
    number is comparable across labs), then official benchmark boards (the
    tasks and intervals are standardized even if the agent is not), then
    everything else — vendor model cards, launch posts and aggregators
    without a published methodology.
    """
    if not source:
        return TIER_SELF_REPORTED
    host = source.split("//", 1)[-1].split("/", 1)[0].lower()
    if host in INDEPENDENT_HOSTS:
        return TIER_INDEPENDENT
    if host in OFFICIAL_HOSTS:
        return TIER_OFFICIAL
    return TIER_SELF_REPORTED


@dataclass(frozen=True)
class Conflict:
    """An override that disagrees with the upstream value it overlays."""

    model_id: str
    benchmark: str
    upstream: float
    override: float
    upstream_source: str | None
    override_source: str | None
    reason: str
    #: Whether the override actually landed (tier decided). A same-tier
    #: disagreement is reported and *kept as upstream* — the console must
    #: not claim an application that did not happen.
    applied: bool = True

    @property
    def override_wins(self) -> bool:
        """The override applies only when its source is at least as good.

        The curated layer exists to fix the record, not to spray opinions
        over it: a vendor's own number stays put against a random blog,
        but an independent rerun beats the vendor.
        """
        return (source_tier(self.override_source)
                <= source_tier(self.upstream_source))


def load_overrides(overrides_dir: Path) -> dict[str, dict]:
    """Parse ``*.json`` in *overrides_dir* into ``{model id: document}``.

    Matching is on the ``model.id`` the file pins, not on the filename, so a
    renamed file still finds its model; a file without a usable id is
    skipped rather than trusted.
    """
    found: dict[str, dict] = {}
    for path in sorted(Path(overrides_dir).glob("*.json")):
        try:
            doc = json.loads(path.read_text())["model"]
            model_id = doc["id"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        found[model_id] = doc
    return found


def apply(model: Model, override: dict) -> tuple[Model, list[Conflict]]:
    """Layer *override* onto *model*; return the model and its conflicts.

    Entries are keyed by (name, version) — the same key the catalog uses.
    An override adds a benchmark upstream lacks, or pins a value upstream
    also carries; pinning a *different* value is only applied when the
    override's source is at least as good (:attr:`Conflict.override_wins`),
    and either way the disagreement is returned for ``conflicts``.
    """
    scores = dict(model.scores)
    sources = dict(model.sources)
    note = override.get("note") or ""
    conflicts: list[Conflict] = []
    for entry in override.get("benchmarks") or []:
        key = benchmark_key(entry)
        value = entry.get("score")
        if not isinstance(value, (int, float)):
            continue  # a note or a half-filled entry adds nothing
        sources[key] = entry.get("source")
        if key in scores and scores[key] != value:
            conflict = Conflict(
                model_id=model.model_id,
                benchmark=key,
                upstream=scores[key],
                override=value,
                upstream_source=model.sources.get(key),
                override_source=entry.get("source"),
                reason=entry.get("note") or note,
            )
            conflicts.append(replace(conflict,
                                     applied=conflict.override_wins))
            if not conflict.override_wins:
                continue
        scores[key] = value
    return replace(model, scores=scores, sources=sources), conflicts


@dataclass(frozen=True)
class Curated:
    """Upstream records with overlays applied, plus the audit trail.

    ``conflicts`` is every disagreement between an override and upstream
    (whether or not the override won); ``orphans`` are override files whose
    model id no longer exists upstream — the signature of a typo or of a
    model pulled from the registry.
    """

    models: list[Model]
    conflicts: list[Conflict]
    curated: set[str]
    orphans: set[str]


def load_curated(upstream_dir: Path, overrides_dir: Path) -> Curated:
    """Load upstream records with the curated overlays applied.

    Models without an override come back untouched; a model carrying one
    gets its benchmarks merged and any disagreement recorded.
    """
    models: list[Model] = []
    conflicts: list[Conflict] = []
    curated: set[str] = set()
    overrides = load_overrides(overrides_dir)
    for model in load_models(upstream_dir):
        override = overrides.pop(model.model_id, None)
        if override:
            model, found = apply(model, override)
            conflicts.extend(found)
            curated.add(model.model_id)
        models.append(model)
    conflicts.sort(key=lambda c: (c.model_id, c.benchmark))
    return Curated(models=models, conflicts=conflicts,
                   curated=curated, orphans=set(overrides))


def curated_ids(overrides_dir: Path) -> set[str]:
    """Model ids that carry an override file, applied or not."""
    return set(load_overrides(overrides_dir))


def off_band_models(models: list[Model]) -> list[tuple[str, float, float, float]]:
    """Models whose Terminal-Bench 4.0 sits far from the 2.1→4.0 band.

    Returns ``(model id, 2.1 score, 4.0 score, residual)`` sorted by how
    far off the pair sits. The band is fitted on vendor-sourced pairs
    (deepseek-v4.1-flash, gemini-3.8-flash, the three MiMo v2.5/v2.6
    pairs) and early 4.0 runs are genuinely noisy, so a hit is a review
    prompt — it justifies no change on its own.
    """
    slope, intercept = TB40_BAND
    out: list[tuple[str, float, float, float]] = []
    for m in models:
        score21 = m.scores.get("Terminal-Bench 2.1")
        score40 = m.scores.get("Terminal-Bench 4.0")
        if score21 is None or score40 is None:
            continue
        residual = score40 - (slope * score21 + intercept)
        if abs(residual) > TB40_BAND_SLACK:
            out.append((m.model_id, score21, score40, residual))
    return sorted(out, key=lambda row: -abs(row[3]))
