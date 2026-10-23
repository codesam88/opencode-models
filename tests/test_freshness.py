"""How old a snapshot layer is.

Storage owns every on-disk layer, so ``snapshot_age`` is the single
freshness primitive every future sync check will use. These tests cover
its documented contract on synthetic tmp dirs only.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from opencode_models.freshness import snapshot_age


def _iso(dt):
    return dt.isoformat(timespec="seconds")


class FreshnessTest(unittest.TestCase):
    def test_fresh_meta_age_is_near_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "available").mkdir()
            (d / "available" / "go.json").write_text(
                json.dumps(
                    {
                        "meta": {"fetched_at": _iso(datetime.now(timezone.utc))},
                        "models": {},
                    }
                )
            )
            age = snapshot_age(d, "available/go")
            self.assertIsNotNone(age)
            self.assertLess(age, timedelta(seconds=5))

    def test_old_meta_age_is_exact_loosely(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "available").mkdir()
            past = datetime.now(timezone.utc) - timedelta(days=2)
            (d / "available" / "go.json").write_text(
                json.dumps({"meta": {"fetched_at": _iso(past)}, "models": {}})
            )
            age = snapshot_age(d, "available/go")
            self.assertIsNotNone(age)
            self.assertGreaterEqual(age, timedelta(days=2))
            self.assertLess(age, timedelta(days=3))

    def test_legacy_bare_payload_falls_back_to_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "available").mkdir()
            path = d / "available" / "go.json"
            path.write_text(json.dumps(["a", "b"]))
            old = datetime.now(timezone.utc) - timedelta(days=2)
            os.utime(path, (old.timestamp(), old.timestamp()))
            age = snapshot_age(d, "available/go")
            self.assertIsNotNone(age)
            self.assertGreaterEqual(age, timedelta(days=2))
            self.assertLess(age, timedelta(days=3))

    def test_missing_layer_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertIsNone(snapshot_age(d, "available/go"))
            self.assertIsNone(snapshot_age(d, "arena"))

    def test_unknown_source_raises_key_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with self.assertRaises(KeyError):
                snapshot_age(d, "not-a-layer")

    def test_corrupt_meta_falls_back_to_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "available").mkdir()
            path = d / "available" / "go.json"
            path.write_text("{not valid json")
            old = datetime.now(timezone.utc) - timedelta(days=2)
            os.utime(path, (old.timestamp(), old.timestamp()))
            age = snapshot_age(d, "available/go")
            self.assertIsNotNone(age)
            self.assertGreaterEqual(age, timedelta(days=2))
            self.assertLess(age, timedelta(days=3))

    def test_multi_file_aggregate_oldest_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "boards").mkdir()
            now = datetime.now(timezone.utc)
            (d / "boards" / "new.json").write_text(
                json.dumps(
                    {"meta": {"fetched_at": _iso(now - timedelta(days=1))}, "rows": []}
                )
            )
            (d / "boards" / "old.json").write_text(
                json.dumps(
                    {"meta": {"fetched_at": _iso(now - timedelta(days=3))}, "rows": []}
                )
            )
            age = snapshot_age(d, "boards")
            self.assertIsNotNone(age)
            self.assertGreaterEqual(age, timedelta(days=3))
            self.assertLess(age, timedelta(days=4))

    def test_arena_uses_aiarena_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "aiarena").mkdir()
            past = datetime.now(timezone.utc) - timedelta(hours=3)
            (d / "aiarena" / "scores.json").write_text(
                json.dumps({"meta": {"fetched_at": _iso(past)}, "scores": {}})
            )
            age = snapshot_age(d, "arena")
            self.assertIsNotNone(age)
            self.assertGreaterEqual(age, timedelta(hours=3))
            self.assertLess(age, timedelta(hours=4))

    def test_plans_aggregate_and_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "plans").mkdir()
            past = datetime.now(timezone.utc) - timedelta(hours=5)
            (d / "plans" / "go.json").write_text(
                json.dumps({"meta": {"fetched_at": _iso(past)}, "models": {}})
            )
            age = snapshot_age(d, "plans/go")
            self.assertIsNotNone(age)
            self.assertGreaterEqual(age, timedelta(hours=5))
            self.assertLess(age, timedelta(hours=6))
            agg = snapshot_age(d, "plans")
            self.assertIsNotNone(agg)
            self.assertGreaterEqual(agg, timedelta(hours=5))
            self.assertLess(agg, timedelta(hours=6))


if __name__ == "__main__":
    unittest.main()
