"""The curated overlay layer: tiers, merging and conflict reporting."""

import json
import tempfile
import unittest
from pathlib import Path

from opencode_models.models import Model
from opencode_models.overrides import (
    TIER_INDEPENDENT,
    TIER_OFFICIAL,
    TIER_SELF_REPORTED,
    apply,
    load_curated,
    load_overrides,
    merge_curated,
    off_band_models,
    source_tier,
)


def _m(model_id, scores=None, sources=None):
    return Model(
        model_id=model_id,
        name=model_id.split("/")[-1].upper(),
        lab=model_id.split("/")[0],
        price_input=1.0,
        price_output=1.0,
        price_cached_input=1.0,
        scores=scores or {},
        sources=sources or {},
    )


def _write(d: Path, name: str, model: dict) -> None:
    (d / name).write_text(json.dumps({"model": model}))


class SourceTierTest(unittest.TestCase):
    def test_independent_evaluators_rank_above_official_boards(self):
        self.assertEqual(
            source_tier("https://artificialanalysis.ai/x"), TIER_INDEPENDENT
        )
        self.assertEqual(
            source_tier("https://www.tbench.ai/leaderboard/4.0"), TIER_OFFICIAL
        )
        self.assertLess(TIER_INDEPENDENT, TIER_OFFICIAL)

    def test_vendor_pages_and_unattributed_scores_rank_last(self):
        self.assertEqual(source_tier("https://x.ai/news/grok-4-7"), TIER_SELF_REPORTED)
        self.assertEqual(source_tier(None), TIER_SELF_REPORTED)


class LoadOverridesTest(unittest.TestCase):
    def test_files_are_keyed_by_the_id_they_pin(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp), "x__grok.json", {"id": "x/grok", "benchmarks": []})
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
        override = {
            "model": {
                "id": "xai/grok-4.6",
                "benchmarks": [
                    {
                        "name": "Terminal-Bench",
                        "version": "2.1",
                        "score": 88.4,
                        "source": "https://artificialanalysis.ai/models/grok-4-6",
                    }
                ],
            }
        }
        doc = override["model"]
        model, conflicts = apply(
            _m(
                "xai/grok-4.6",
                {"Terminal-Bench 4.0": 20.3},
                {"Terminal-Bench 4.0": "https://x.ai/news/grok-4-7"},
            ),
            doc,
        )
        self.assertEqual(model.scores["Terminal-Bench 2.1"], 88.4)
        self.assertEqual(conflicts, [])

    def test_an_independent_override_beats_a_vendor_number(self):
        # Grok 4.7: the vendor's own 4.0 chart says 37.6, AA measured 33
        # with its own harness — the independent run wins the axis
        override = {
            "benchmarks": [
                {
                    "name": "Terminal-Bench",
                    "version": "4.0",
                    "score": 33.0,
                    "source": "https://artificialanalysis.ai/models/grok-4-7",
                }
            ]
        }
        model, conflicts = apply(
            _m(
                "xai/grok-4.7",
                {"Terminal-Bench 4.0": 37.6},
                {"Terminal-Bench 4.0": "https://x.ai/news/grok-4-7"},
            ),
            override,
        )
        self.assertEqual(model.scores["Terminal-Bench 4.0"], 33.0)
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(conflicts[0].override_wins)

    def test_a_weaker_sourced_override_is_reported_but_not_applied(self):
        override = {
            "benchmarks": [
                {
                    "name": "SWE-Bench",
                    "version": "Verified",
                    "score": 99.0,
                    "source": "https://random-blog.example/post",
                }
            ]
        }
        model, conflicts = apply(
            _m(
                "a/m",
                {"SWE-Bench Verified": 74.0},
                {"SWE-Bench Verified": "https://artificialanalysis.ai/models/m"},
            ),
            override,
        )
        self.assertEqual(model.scores["SWE-Bench Verified"], 74.0)
        self.assertFalse(conflicts[0].override_wins)

    def test_a_losing_override_leaves_the_kept_value_source_untouched(self):
        upstream_source = "https://artificialanalysis.ai/models/m"
        override = {
            "benchmarks": [
                {
                    "name": "SWE-Bench",
                    "version": "Verified",
                    "score": 99.0,
                    "source": "https://random-blog.example/post",
                }
            ]
        }
        model, conflicts = apply(
            _m(
                "a/m",
                {"SWE-Bench Verified": 74.0},
                {"SWE-Bench Verified": upstream_source},
            ),
            override,
        )
        self.assertEqual(model.scores["SWE-Bench Verified"], 74.0)
        self.assertEqual(model.sources["SWE-Bench Verified"], upstream_source)
        self.assertFalse(conflicts[0].override_wins)

    def test_a_winning_override_updates_value_and_source(self):
        override = {
            "benchmarks": [
                {
                    "name": "Terminal-Bench",
                    "version": "4.0",
                    "score": 33.0,
                    "source": "https://artificialanalysis.ai/models/grok-4-7",
                }
            ]
        }
        model, conflicts = apply(
            _m(
                "xai/grok-4.7",
                {"Terminal-Bench 4.0": 37.6},
                {"Terminal-Bench 4.0": "https://x.ai/news/grok-4-7"},
            ),
            override,
        )
        self.assertEqual(model.scores["Terminal-Bench 4.0"], 33.0)
        self.assertEqual(
            model.sources["Terminal-Bench 4.0"],
            "https://artificialanalysis.ai/models/grok-4-7",
        )
        self.assertTrue(conflicts[0].override_wins)

    def test_agreeing_values_conflict_about_nothing(self):
        override = {
            "benchmarks": [
                {
                    "name": "Terminal-Bench",
                    "version": "2.1",
                    "score": 88.4,
                    "source": "https://artificialanalysis.ai/models/grok-4-6",
                }
            ]
        }
        model, conflicts = apply(_m("x/grok", {"Terminal-Bench 2.1": 88.4}), override)
        self.assertEqual(conflicts, [])
        self.assertEqual(model.scores["Terminal-Bench 2.1"], 88.4)

    def test_entries_without_a_score_are_not_applied(self):
        override = {"benchmarks": [{"name": "Terminal-Bench", "note": "x"}]}
        model, conflicts = apply(_m("x/grok", {"Terminal-Bench 2.1": 1.0}), override)
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
            _write(
                up,
                "a__m.json",
                {
                    "id": "a/m",
                    "name": "M",
                    "benchmarks": [
                        {
                            "name": "SWE-Bench",
                            "version": "Verified",
                            "score": 74.0,
                            "source": "https://artificialanalysis.ai/m",
                        }
                    ],
                },
            )
            _write(up, "a__n.json", {"id": "a/n", "name": "N", "benchmarks": []})
            _write(
                over,
                "a__m.json",
                {
                    "id": "a/m",
                    "benchmarks": [
                        {
                            "name": "SWE-Bench",
                            "version": "Verified",
                            "score": 74.5,
                            "source": "https://artificialanalysis.ai/m2",
                        }
                    ],
                },
            )
            _write(over, "a__ghost.json", {"id": "a/ghost"})
            out = load_curated(up, over)
            by_id = {m.model_id: m for m in out.models}
            self.assertEqual(by_id["a/m"].scores["SWE-Bench Verified"], 74.5)
            self.assertEqual(by_id["a/n"].scores, {})  # untouched
            self.assertEqual(out.curated, {"a/m"})
            self.assertEqual(out.orphans, {"a/ghost"})
            self.assertEqual([c.model_id for c in out.conflicts], ["a/m"])


class OffBandTest(unittest.TestCase):
    def test_pairs_far_off_the_band_are_listed_worst_first(self):
        models = [
            _m("x/gemini", {"Terminal-Bench 2.1": 89.4, "Terminal-Bench 4.0": 19.1}),
            _m("x/mimo", {"Terminal-Bench 2.1": 89.9, "Terminal-Bench 4.0": 34.9}),
            _m("x/solo21", {"Terminal-Bench 2.1": 80.0}),
        ]
        out = off_band_models(models)
        self.assertEqual([row[0] for row in out], ["x/gemini"])
        self.assertEqual(out[0][1:3], (89.4, 19.1))
        self.assertAlmostEqual(out[0][3], 19.1 - (1.131 * 89.4 - 71.39), places=1)


if __name__ == "__main__":
    unittest.main()


class CommittedOverridesTest(unittest.TestCase):
    """Whatever we ship in data/overrides must be well formed.

    The directory is empty today and may stay that way — the official
    boards now carry the provenance the overrides were patching in by hand.
    These tests pass vacuously on an empty directory and hold the moment
    someone adds a file.
    """

    def _files(self):
        root = Path(__file__).resolve().parent.parent / "data" / "overrides"
        return sorted(p for p in root.glob("*.json"))

    def test_every_entry_cites_a_source(self):
        for path in self._files():
            payload = json.loads(path.read_text())["model"]
            for entry in payload["benchmarks"]:
                with self.subTest(override=path.name, benchmark=entry.get("name")):
                    self.assertTrue(
                        entry.get("source"),
                        f"{path.name}: {entry.get('name')} "
                        f"{entry.get('version')} has no source — "
                        "an uncited value cannot be tiered",
                    )

    def test_every_model_id_names_its_lab(self):
        for path in self._files():
            model_id = json.loads(path.read_text())["model"]["id"]
            with self.subTest(override=path.name):
                self.assertIn("/", model_id)


class StepFiveOverrideTest(unittest.TestCase):
    """Step 5 Preview's vendor page is its only published record.

    Upstream carries nothing for the model, so every scored axis on that
    page is a new benchmark that applies cleanly. The contract is the set
    of keys, not the values: values are data and may be re-read, but a
    dropped or added axis changes what the model ranks on.
    """

    def _entries(self):
        root = Path(__file__).resolve().parent.parent / "data" / "overrides"
        payload = json.loads((root / "stepfun__step-5-preview.json").read_text())[
            "model"
        ]
        return payload["benchmarks"]

    def test_it_covers_exactly_the_three_scored_axes(self):
        keys = {(e["name"], e["version"]) for e in self._entries()}
        self.assertEqual(
            keys,
            {("Terminal-Bench", "2.1"), ("Terminal-Bench", "4.0"), ("DeepSWE", "1.1")},
        )

    def test_every_entry_names_the_page_and_the_effort(self):
        for entry in self._entries():
            with self.subTest(benchmark=(entry["name"], entry["version"])):
                self.assertIn("stepfun.com/step-5-preview", entry.get("source", ""))
                self.assertIn("High", entry.get("note", ""))


#: Every override file's contract: the exact set of (benchmark, version)
#: keys it holds. Keys are the contract; values are data and may be re-read.
#: Two sources feed these files - StepFun's page bundle (machine-read column
#: headers) and AA's TB 2.1 chart (eyewitness transcription, adopted only
#: where a run slug ties the bar to a model).
FILE_KEYS = {
    # StepFun's Step-5-Preview page: only cells upstream lacks at
    # equal-or-better tier
    "moonshotai__kimi-k3.json": {("Terminal-Bench", "4.0"), ("Terminal-Bench", "2.1")},
    "openai__gpt-6-astra.json": {("Terminal-Bench", "2.1")},
    "anthropic__claude-fable-5-1.json": {("DeepSWE", "1.1"), ("Terminal-Bench", "2.1")},
    "anthropic__claude-opus-5.json": {("Terminal-Bench", "2.1")},
    # AA's TB 2.1 chart: missing cells with a run slug behind the bar, plus
    # Kimi's 85.0, which StepFun's bundle states identically
    "alibaba__qwen3-8-flash-next.json": {("Terminal-Bench", "2.1")},
    "deepseek__deepseek-v4-flash.json": {("Terminal-Bench", "2.1")},
    "inclusionai__ling-3-0-flash-fin.json": {("Terminal-Bench", "2.1")},
    "alibaba__qwen3-8-max-0902.json": {("Terminal-Bench", "2.1")},
}

#: Where each file's entries must come from. A screenshot transcription is
#: adopted only with a run slug tying bar to model - nine TB 4.0 cells were
#: reverted for lacking exactly that.
FILE_HOSTS = {
    "moonshotai__kimi-k3.json": {"stepfun.com/step-5-preview", "artificialanalysis.ai"},
    "openai__gpt-6-astra.json": {"stepfun.com/step-5-preview"},
    "anthropic__claude-fable-5-1.json": {"stepfun.com/step-5-preview"},
    "anthropic__claude-opus-5.json": {"stepfun.com/step-5-preview"},
    "alibaba__qwen3-8-flash-next.json": {"artificialanalysis.ai"},
    "deepseek__deepseek-v4-flash.json": {"artificialanalysis.ai"},
    "inclusionai__ling-3-0-flash-fin.json": {"artificialanalysis.ai"},
    "alibaba__qwen3-8-max-0902.json": {"artificialanalysis.ai"},
}


class CompetitorOverrideTest(unittest.TestCase):
    """One file per competitor, holding only what is missing upstream."""

    def _payload(self, filename):
        root = Path(__file__).resolve().parent.parent / "data" / "overrides"
        return json.loads((root / filename).read_text())["model"]

    def test_each_file_holds_exactly_the_missing_cells(self):
        for filename, keys in FILE_KEYS.items():
            with self.subTest(override=filename):
                got = {
                    (e["name"], e["version"])
                    for e in self._payload(filename)["benchmarks"]
                }
                self.assertEqual(got, keys)

    def test_each_cell_names_its_source_and_effort(self):
        for filename, hosts in FILE_HOSTS.items():
            for entry in self._payload(filename)["benchmarks"]:
                with self.subTest(
                    override=filename, benchmark=(entry["name"], entry["version"])
                ):
                    self.assertTrue(
                        any(h in entry.get("source", "") for h in hosts),
                        f"{filename}: {entry.get('source')}",
                    )
                    self.assertRegex(entry.get("note", ""), r"Max|max|effort")


class OverrideRulesTest(unittest.TestCase):
    """The rules, enforced by the loader rather than by scanning the repo.

    An override is hand-written, so the guard has to live where a new file
    meets the code: these use synthetic files to prove what the loader
    refuses, whatever the shipped directory happens to hold.
    """

    def _write(self, tmp: Path, *entries, name="vendor/model-x"):
        (tmp / "vendor__model-x.json").write_text(
            json.dumps({"model": {"id": name, "benchmarks": list(entries)}})
        )
        return load_overrides(tmp)

    def _entry(self, **over):
        return {
            "name": "Terminal-Bench",
            "version": "2.1",
            "score": 80.0,
            "source": "https://example.org/run",
            **over,
        }

    def test_a_sourced_entry_loads(self):
        with tempfile.TemporaryDirectory() as d:
            found = self._write(Path(d), self._entry())
            self.assertEqual(len(found["vendor/model-x"]["benchmarks"]), 1)

    def test_an_entry_without_a_source_is_dropped(self):
        # an unattributed score cannot be tiered, and would be treated as
        # the worst source there is — better absent than believed
        with tempfile.TemporaryDirectory() as d:
            found = self._write(Path(d), self._entry(source=None))
            self.assertEqual(found["vendor/model-x"]["benchmarks"], [])

    def test_an_entry_missing_the_source_key_entirely_is_dropped(self):
        # forgetting the key is the likelier slip; a missing key must not
        # read as an empty-but-present one
        with tempfile.TemporaryDirectory() as d:
            entry = self._entry()
            del entry["source"]
            found = self._write(Path(d), entry)
            self.assertEqual(found["vendor/model-x"]["benchmarks"], [])

    def test_a_sourced_entry_survives_alongside_an_unsourced_one(self):
        with tempfile.TemporaryDirectory() as d:
            found = self._write(
                Path(d), self._entry(source=None), self._entry(name="SciCode")
            )
            kept = [e["name"] for e in found["vendor/model-x"]["benchmarks"]]
            self.assertEqual(kept, ["SciCode"])

    def test_an_empty_directory_loads_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_overrides(Path(d)), {})

    def test_an_entry_lands_under_its_own_key(self):
        from opencode_models.overrides import benchmark_key

        with tempfile.TemporaryDirectory() as d:
            found = self._write(Path(d), self._entry(score=88.4))
            doc = found["vendor/model-x"]
            model, _ = apply(_m("vendor/model-x"), doc)
            key = benchmark_key(doc["benchmarks"][0])
            self.assertEqual(model.scores[key], 88.4)
            self.assertEqual(model.sources[key], "https://example.org/run")

    def test_an_entry_with_a_non_numeric_score_lands_nothing(self):
        # a half-filled entry is inert rather than wrong
        from opencode_models.overrides import benchmark_key

        with tempfile.TemporaryDirectory() as d:
            found = self._write(Path(d), self._entry(score="88.4"))
            doc = found["vendor/model-x"]
            model, _ = apply(_m("vendor/model-x"), doc)
            self.assertEqual(model.scores, {})
            self.assertNotIn(benchmark_key(doc["benchmarks"][0]), model.scores)


class SyntheticMergeTest(unittest.TestCase):
    """Pure-merge coverage built from in-memory records only (no data dir)."""

    def test_overlay_adds_a_missing_benchmark(self):
        upstream = [_m("x/grok", {"Terminal-Bench 4.0": 20.3})]
        overrides = {
            "x/grok": {
                "id": "x/grok",
                "benchmarks": [
                    {
                        "name": "Terminal-Bench",
                        "version": "2.1",
                        "score": 88.4,
                        "source": "https://artificialanalysis.ai/models/grok",
                    }
                ],
            }
        }
        out = merge_curated(upstream, [], overrides)
        model = out.models[0]
        self.assertEqual(model.scores["Terminal-Bench 2.1"], 88.4)
        self.assertEqual(model.scores["Terminal-Bench 4.0"], 20.3)
        self.assertEqual(out.curated, {"x/grok"})
        self.assertEqual(out.conflicts, [])
        self.assertEqual(out.orphans, set())

    def test_override_pins_over_a_self_report(self):
        upstream = [
            _m(
                "x/grok",
                {"Terminal-Bench 4.0": 37.6},
                {"Terminal-Bench 4.0": "https://x.ai/news/grok"},
            )
        ]
        overrides = {
            "x/grok": {
                "id": "x/grok",
                "benchmarks": [
                    {
                        "name": "Terminal-Bench",
                        "version": "4.0",
                        "score": 33.0,
                        "source": "https://artificialanalysis.ai/models/grok",
                    }
                ],
            }
        }
        out = merge_curated(upstream, [], overrides)
        model = out.models[0]
        self.assertEqual(model.scores["Terminal-Bench 4.0"], 33.0)
        self.assertEqual(len(out.conflicts), 1)
        self.assertTrue(out.conflicts[0].override_wins)

    def test_override_loses_to_an_independent_run_and_is_recorded(self):
        upstream = [
            _m(
                "a/m",
                {"SWE-Bench Verified": 74.0},
                {"SWE-Bench Verified": "https://artificialanalysis.ai/models/m"},
            )
        ]
        overrides = {
            "a/m": {
                "id": "a/m",
                "benchmarks": [
                    {
                        "name": "SWE-Bench",
                        "version": "Verified",
                        "score": 99.0,
                        "source": "https://random-blog.example/post",
                    }
                ],
            }
        }
        out = merge_curated(upstream, [], overrides)
        model = out.models[0]
        self.assertEqual(model.scores["SWE-Bench Verified"], 74.0)
        self.assertEqual(
            model.sources["SWE-Bench Verified"],
            "https://artificialanalysis.ai/models/m",
        )
        self.assertEqual(len(out.conflicts), 1)
        self.assertFalse(out.conflicts[0].override_wins)
        self.assertFalse(out.conflicts[0].applied)
        self.assertEqual(out.conflicts[0].used, 74.0)

    def test_orphan_override_is_reported(self):
        upstream = [_m("a/m", {})]
        overrides = {"a/ghost": {"id": "a/ghost", "benchmarks": []}}
        out = merge_curated(upstream, [], overrides)
        self.assertEqual(out.orphans, {"a/ghost"})

    def test_manual_record_merges_and_shadow_is_reported(self):
        upstream = [_m("l/dup", {})]
        manual = [_m("l/dup", {}), _m("l/only-manual", {})]
        out = merge_curated(upstream, manual, {})
        ids = {m.model_id for m in out.models}
        self.assertIn("l/only-manual", ids)
        self.assertEqual(out.manual_orphans, {"l/dup"})

    def test_manual_model_is_overridable(self):
        upstream = [_m("l/plain", {})]
        manual = [_m("l/m")]
        overrides = {
            "l/m": {
                "id": "l/m",
                "benchmarks": [
                    {
                        "name": "Terminal-Bench",
                        "version": "2.1",
                        "score": 60.0,
                        "source": "https://example.invalid/run",
                    }
                ],
            }
        }
        out = merge_curated(upstream, manual, overrides)
        by_id = {m.model_id: m for m in out.models}
        self.assertEqual(by_id["l/m"].scores.get("Terminal-Bench 2.1"), 60.0)


class DeepSeekV41FlashOverrideTest(unittest.TestCase):
    """deepseek-v4.1-flash serves the 1017 weights and ranks above mimo-v2.6-flash.

    The opencode slug defaults to the newer 1017 variant under the hood
    (1017 is a minor update to Flash per its model card), so the override
    pins the 1017 card's TB 4.0 and DeepSWE numbers onto the base record.
    With those applied the curated final lands above mimo-v2.6-flash.
    """

    FILENAME = "deepseek__deepseek-v4-1-flash.json"
    SOURCE_1017 = "https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017"

    def _payload(self):
        root = Path(__file__).resolve().parent.parent / "data" / "overrides"
        return json.loads((root / self.FILENAME).read_text())["model"]

    def test_it_pins_the_1017_tb40_and_deepswe_cells(self):
        payload = self._payload()
        self.assertEqual(payload["id"], "deepseek/deepseek-v4.1-flash")
        self.assertIn("1017", payload.get("note", ""))
        got = {(e["name"], e.get("version")): e for e in payload["benchmarks"]}
        self.assertEqual(set(got), {("Terminal-Bench", "4.0"), ("DeepSWE", "1.1")})
        self.assertEqual(got[("Terminal-Bench", "4.0")]["score"], 42.3)
        self.assertEqual(got[("DeepSWE", "1.1")]["score"], 77.0)
        for entry in got.values():
            self.assertIn("1017", entry.get("source", ""))
            self.assertIn("huggingface.co", entry.get("source", ""))

    def test_curated_final_lands_above_mimo_v26_flash(self):
        from opencode_models.catalog import load_catalog
        from opencode_models.scoring import combined, derived_scores, scales

        root = Path(__file__).resolve().parent.parent / "data"
        models, *_ = load_catalog(root)
        table = scales(models)
        by_id = {m.model_id: m for m in models}
        deepseek = combined(
            derived_scores(by_id["deepseek/deepseek-v4.1-flash"], table)
        )
        mimo = combined(derived_scores(by_id["xiaomi/mimo-v2.6-flash"], table))
        self.assertIsNotNone(deepseek)
        self.assertIsNotNone(mimo)
        self.assertGreater(deepseek, mimo)


if __name__ == "__main__":
    unittest.main()
