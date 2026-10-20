"""Parsing opencode.ai/data per-model JSON files into normalized records."""

import json
import tempfile
import unittest
from pathlib import Path

from opencode_rank.data import Model, load_model, load_models

SAMPLE = {
    "page": "https://opencode.ai/data/deepseek/deepseek-v4-pro",
    "updatedAt": "2026-10-08T21:48:16.000Z",
    "summary": "DeepSeek V4 Pro ranked #14 ...",
    "model": {
        "id": "deepseek/deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "lab": "deepseek",
        "labName": "DeepSeek",
        "releaseDate": "2026-02-01",
        "contextTokens": 1000000,
        "priceUsdPer1M": {
            "input": 1.74,
            "output": 3.48,
            "cachedInput": 0.2,
            "cacheWrite": None,
        },
        "benchmarks": [
            {
                "name": "SWE-Bench Verified",
                "score": 80.6,
                "metric": "resolved",
                "source": "https://example.com",
            },
            {
                "name": "Terminal-Bench",
                "score": 55.0,
                "metric": "accuracy",
                "source": "https://example.com",
            },
        ],
    },
    "usage": None,
}


class LoadModelTest(unittest.TestCase):
    def _model(self, sample=SAMPLE) -> Model:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "deepseek__deepseek-v4-pro.json"
            p.write_text(json.dumps(sample))
            return load_model(p)

    def test_extracts_identity(self):
        m = self._model()
        self.assertEqual(m.model_id, "deepseek/deepseek-v4-pro")
        self.assertEqual(m.name, "DeepSeek V4 Pro")
        self.assertEqual(m.lab, "deepseek")

    def test_extracts_prices(self):
        m = self._model()
        self.assertEqual(m.price_input, 1.74)
        self.assertEqual(m.price_output, 3.48)
        self.assertEqual(m.price_cached_input, 0.2)

    def test_extracts_benchmark_scores(self):
        m = self._model()
        self.assertEqual(m.scores["SWE-Bench Verified"], 80.6)
        self.assertEqual(m.scores["Terminal-Bench"], 55.0)

    def test_missing_price_yields_none(self):
        sample = json.loads(json.dumps(SAMPLE))
        sample["model"]["priceUsdPer1M"] = None
        m = self._model(sample)
        self.assertIsNone(m.price_input)

    def test_model_without_that_benchmark_absent_from_scores(self):
        m = self._model()
        self.assertNotIn("Terminal-Bench Pro", m.scores)

    def test_slug_replaces_slash_with_dash(self):
        m = self._model()
        self.assertEqual(m.slug, "deepseek-deepseek-v4-pro")

    def test_benchmark_version_becomes_part_of_the_key(self):
        """Terminal-Bench 2.1 (Fable 5, 88) must not mix with 4.0 (Fable 5.1, 55.8)."""
        sample = json.loads(json.dumps(SAMPLE))
        sample["model"]["benchmarks"] = [
            {
                "name": "Terminal-Bench",
                "version": "2.1",
                "score": 88.0,
                "metric": "success rate",
                "source": "",
            },
        ]
        m = self._model(sample)
        self.assertEqual(m.scores, {"Terminal-Bench 2.1": 88.0})

    def test_benchmark_without_version_keeps_bare_name(self):
        sample = json.loads(json.dumps(SAMPLE))
        sample["model"]["benchmarks"] = [
            {"name": "Terminal-Bench", "score": 55.8, "metric": "score", "source": ""},
        ]
        m = self._model(sample)
        self.assertEqual(m.scores, {"Terminal-Bench": 55.8})


class LoadModelsTest(unittest.TestCase):
    def test_loads_every_json_in_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name, mid in (("a__m1.json", "a/m1"), ("b__m2.json", "b/m2")):
                s = json.loads(json.dumps(SAMPLE))
                s["model"]["id"] = mid
                (d / name).write_text(json.dumps(s))
            (d / "not-json.txt").write_text("ignored")
            models = load_models(d)
        self.assertEqual(sorted(m.model_id for m in models), ["a/m1", "b/m2"])

    def test_skips_unparseable_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "broken.json").write_text("{ not json")
            good = json.loads(json.dumps(SAMPLE))
            (d / "good.json").write_text(json.dumps(good))
            models = load_models(d)
        self.assertEqual(len(models), 1)


if __name__ == "__main__":
    unittest.main()
