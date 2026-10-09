"""The curated overlay layer: tiers, merging and conflict reporting."""

import json
import unittest
from pathlib import Path

from modelpareto.data import Model
from modelpareto.overrides import (
    TIER_INDEPENDENT,
    TIER_OFFICIAL,
    TIER_SELF_REPORTED,
    Conflict,
    apply,
    load_curated,
    load_overrides,
    off_band_models,
    source_tier,
)


def _m(model_id, scores=None, sources=None):
    return Model(model_id=model_id, name=model_id.split("/")[-1].upper(),
                 lab=model_id.split("/")[0], price_input=1.0, price_output=1.0,
                 price_cached_input=1.0, scores=scores or {},
                 sources=sources or {})


def _write(d: Path, name: str, model: dict) -> None:
    (d / name).write_text(json.dumps({"model": model}))


class SourceTierTest(unittest.TestCase):
    def test_independent_evaluators_rank_above_official_boards(self):
        self.assertEqual(source_tier("https://artificialanalysis.ai/x"),
                         TIER_INDEPENDENT)
        self.assertEqual(source_tier("https://www.tbench.ai/leaderboard/4.0"),
                         TIER_OFFICIAL)
        self.assertLess(TIER_INDEPENDENT, TIER_OFFICIAL)

    def test_vendor_pages_and_unattributed_scores_rank_last(self):
        self.assertEqual(source_tier("https://x.ai/news/grok-4-7"),
                         TIER_SELF_REPORTED)
        self.assertEqual(source_tier(None), TIER_SELF_REPORTED)


class LoadOverridesTest(unittest.TestCase):
    def test_files_are_keyed_by_the_id_they_pin(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp), "x__grok.json",
                   {"id": "x/grok", "benchmarks": []})
            self.assertEqual(set(load_overrides(Path(tmp))), {"x/grok"})

    def test_unusable_files_are_skipped_not_trusted(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "broken.json").write_text("{not json")
            _write(d, "no_id.json", {"benchmarks": []})
            self.assertEqual(load_overrides(d), {})


class ApplyTest(unittest.TestCase):
    def test_an_override_can_add_a_score_upstream_lacks(self):
        # Grok 4.6: AA measured Terminal-Bench 2.1 at 88.4 but the registry
        # carries only the 4.0-scale scores — the overlay supplies it
        override = {"model": {"id": "xai/grok-4.6", "benchmarks": [
            {"name": "Terminal-Bench", "version": "2.1", "score": 88.4,
             "source": "https://artificialanalysis.ai/models/grok-4-6"}]}}
        doc = override["model"]
        model, conflicts = apply(
            _m("xai/grok-4.6", {"Terminal-Bench 4.0": 20.3},
               {"Terminal-Bench 4.0": "https://x.ai/news/grok-4-7"}), doc)
        self.assertEqual(model.scores["Terminal-Bench 2.1"], 88.4)
        self.assertEqual(conflicts, [])

    def test_an_independent_override_beats_a_vendor_number(self):
        # Grok 4.7: the vendor's own 4.0 chart says 37.6, AA measured 33
        # with its own harness — the independent run wins the axis
        override = {"benchmarks": [
            {"name": "Terminal-Bench", "version": "4.0", "score": 33.0,
             "source": "https://artificialanalysis.ai/models/grok-4-7"}]}
        model, conflicts = apply(
            _m("xai/grok-4.7", {"Terminal-Bench 4.0": 37.6},
               {"Terminal-Bench 4.0": "https://x.ai/news/grok-4-7"}), override)
        self.assertEqual(model.scores["Terminal-Bench 4.0"], 33.0)
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(conflicts[0].override_wins)

    def test_a_weaker_sourced_override_is_reported_but_not_applied(self):
        override = {"benchmarks": [
            {"name": "SWE-Bench", "version": "Verified", "score": 99.0,
             "source": "https://random-blog.example/post"}]}
        model, conflicts = apply(
            _m("a/m", {"SWE-Bench Verified": 74.0},
               {"SWE-Bench Verified":
                "https://artificialanalysis.ai/models/m"}), override)
        self.assertEqual(model.scores["SWE-Bench Verified"], 74.0)
        self.assertFalse(conflicts[0].override_wins)

    def test_agreeing_values_conflict_about_nothing(self):
        override = {"benchmarks": [
            {"name": "Terminal-Bench", "version": "2.1", "score": 88.4,
             "source": "https://artificialanalysis.ai/models/grok-4-6"}]}
        model, conflicts = apply(_m("x/grok", {"Terminal-Bench 2.1": 88.4}),
                                 override)
        self.assertEqual(conflicts, [])
        self.assertEqual(model.scores["Terminal-Bench 2.1"], 88.4)

    def test_entries_without_a_score_are_not_applied(self):
        override = {"benchmarks": [{"name": "Terminal-Bench", "note": "x"}]}
        model, conflicts = apply(_m("x/grok", {"Terminal-Bench 2.1": 1.0}),
                                 override)
        self.assertEqual(model.scores, {"Terminal-Bench 2.1": 1.0})
        self.assertEqual(conflicts, [])


class LoadCuratedTest(unittest.TestCase):
    def test_models_conflicts_curated_ids_and_orphans(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            up, over = d / "opencode", d / "overrides"
            up.mkdir()
            over.mkdir()
            _write(up, "a__m.json", {
                "id": "a/m", "name": "M",
                "benchmarks": [{"name": "SWE-Bench", "version": "Verified",
                                "score": 74.0, "source":
                                "https://artificialanalysis.ai/m"}]})
            _write(up, "a__n.json", {"id": "a/n", "name": "N", "benchmarks": []})
            _write(over, "a__m.json", {"id": "a/m", "benchmarks": [
                {"name": "SWE-Bench", "version": "Verified", "score": 74.5,
                 "source": "https://artificialanalysis.ai/m2"}]})
            _write(over, "a__ghost.json", {"id": "a/ghost"})
            out = load_curated(up, over)
            by_id = {m.model_id: m for m in out.models}
            self.assertEqual(by_id["a/m"].scores["SWE-Bench Verified"], 74.5)
            self.assertEqual(by_id["a/n"].scores, {})   # untouched
            self.assertEqual(out.curated, {"a/m"})
            self.assertEqual(out.orphans, {"a/ghost"})
            self.assertEqual([c.model_id for c in out.conflicts], ["a/m"])


class OffBandTest(unittest.TestCase):
    def test_pairs_far_off_the_band_are_listed_worst_first(self):
        models = [
            _m("x/gemini", {"Terminal-Bench 2.1": 89.4,
                            "Terminal-Bench 4.0": 19.1}),
            _m("x/mimo", {"Terminal-Bench 2.1": 89.9,
                          "Terminal-Bench 4.0": 34.9}),
            _m("x/solo21", {"Terminal-Bench 2.1": 80.0}),
        ]
        out = off_band_models(models)
        self.assertEqual([row[0] for row in out], ["x/gemini"])
        self.assertEqual(out[0][1:3], (89.4, 19.1))
        self.assertAlmostEqual(out[0][3], 19.1 - (1.131 * 89.4 - 71.39), places=1)


if __name__ == "__main__":
    unittest.main()
