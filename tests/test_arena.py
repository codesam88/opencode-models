"""Arena.ai Elo cache/merge layer, tested offline."""

import json
import tempfile
import unittest
from pathlib import Path

from opencode_models.arena import (
    ARENA_BENCHMARK,
    ArenaCache,
    lookup_arena_elo,
    merge_arena_score,
    merge_arena_scores,
    merge_from_file,
)
from opencode_models.models import Model


def _m(model_id, price=1.0, scores=None, sources=None):
    return Model(
        model_id=model_id,
        name=model_id,
        lab=model_id.split("/")[0],
        price_input=price,
        price_output=None,
        price_cached_input=None,
        scores=scores or {},
        sources=sources or {},
    )


class ArenaCacheTest(unittest.TestCase):
    def test_missing_cache_loads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ArenaCache(Path(tmp)).load(), {})

    def test_truncated_cache_loads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.json"
            path.write_text('{"deepseek-v4-pro": 1400.0')
            self.assertEqual(ArenaCache(Path(tmp)).load(), {})

    def test_empty_cache_loads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.json"
            path.write_text("")
            self.assertEqual(ArenaCache(Path(tmp)).load(), {})

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = ArenaCache(Path(tmp))
            c.save({"deepseek-v4-pro": 1400.0})
            self.assertEqual(c.load(), {"deepseek-v4-pro": 1400.0})

    def test_save_writes_meta_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = ArenaCache(Path(tmp))
            c.save({"x": 1.0})
            raw = json.loads((Path(tmp) / "scores.json").read_text())
            self.assertIn("meta", raw)
            self.assertEqual(raw["scores"], {"x": 1.0})
            self.assertIn("source", raw["meta"])
            self.assertIn("fetched_at", raw["meta"])
            self.assertEqual(raw["meta"]["schema_rev"], 1)
            self.assertEqual(c.load(), {"x": 1.0})

    def test_legacy_bare_dict_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "scores.json").write_text(json.dumps({"x": 1.0}))
            self.assertEqual(ArenaCache(Path(tmp)).load(), {"x": 1.0})

    def test_save_creates_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "a" / "b"
            ArenaCache(nested).save({"x": 1.0})
            self.assertTrue((nested / "scores.json").exists())


class LookupTest(unittest.TestCase):
    def test_parses_rows_and_follows_next_page(self):
        def get(url):
            if "offset=0" in url:
                return (
                    '{"rows": [{"row": {"chatbot_name": "a", '
                    '"elo_score": 1500.0}}], '
                    '"next_page_url": "https://next.example.com"}'
                )
            return '{"rows": [{"row": {"chatbot_name": "b", "elo_score": 1600.0}}]}'

        self.assertEqual(lookup_arena_elo(get), {"a": 1500.0, "b": 1600.0})

    def test_skips_rows_without_elo(self):
        def get(url):
            return (
                '{"rows": [{"row": {"chatbot_name": "a"}}, {"row": {"elo_score": 5}}]}'
            )

        self.assertEqual(lookup_arena_elo(get), {})


class MergeTest(unittest.TestCase):
    def test_attaches_elo_to_matching_slug(self):
        m = _m("deepseek/deepseek-v4-pro")
        out = merge_arena_scores([m], {"deepseek-v4-pro": 1430.0})
        self.assertEqual(out[0].scores[ARENA_BENCHMARK], 1430.0)

    def test_non_matching_slug_untouched(self):
        m = _m("deepseek/deepseek-v4-pro")
        out = merge_arena_scores([m], {"other-model": 100.0})
        self.assertIs(out[0], m)

    def test_existing_arena_score_wins(self):
        m = _m("a/b", scores={ARENA_BENCHMARK: 99.0})
        out = merge_arena_scores([m], {"b": 100.0})
        self.assertEqual(out[0].scores[ARENA_BENCHMARK], 99.0)

    def test_merge_keeps_other_scores(self):
        m = _m("a/b", scores={"Terminal-Bench 4.0": 50.0})
        out = merge_arena_scores([m], {"b": 1400.0})
        self.assertEqual(out[0].scores["Terminal-Bench 4.0"], 50.0)
        self.assertEqual(out[0].scores[ARENA_BENCHMARK], 1400.0)

    def test_merge_arena_score_returns_copy(self):
        m = _m("a/b")
        merged = merge_arena_score(m, 1200.0)
        self.assertIsNot(merged, m)
        self.assertNotIn(ARENA_BENCHMARK, m.scores)

    def test_merge_arena_score_keeps_sources(self):
        m = _m("a/b", sources={"Foo": "https://example.com"})
        merged = merge_arena_score(m, 1200.0)
        self.assertEqual(merged.sources, m.sources)
        self.assertIsNot(merged.sources, m.sources)

    def test_merge_keeps_sources(self):
        m = _m("a/b", sources={"Foo": "https://example.com"})
        out = merge_arena_scores([m], {"b": 1400.0})
        self.assertEqual(out[0].sources, {"Foo": "https://example.com"})
        self.assertIsNot(out[0].sources, m.sources)

    def test_merge_from_file_applies_cached_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            ArenaCache(Path(tmp)).save({"b": 1330.0})
            out = merge_from_file([_m("a/b")], Path(tmp))
        self.assertEqual(out[0].scores[ARENA_BENCHMARK], 1330.0)


if __name__ == "__main__":
    unittest.main()
