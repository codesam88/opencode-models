"""Service-level tests for the shared list/next row pipeline.

Synthetic tmp data dirs only; no network, no live API.
"""

import json
import tempfile
import unittest
from pathlib import Path

import opencode_models.availability as availability
from opencode_models.catalog import load_catalog
from opencode_models.rows import rows_for


def _write_model(data_dir: Path, mid: str, price: float = 1.0, benches=None):
    if benches is None:
        benches = [
            ("Terminal-Bench", "2.1", 50.0),
            ("SWE-Bench", "Verified", 25.0),
        ]
    out = data_dir / "opencode"
    out.mkdir(parents=True, exist_ok=True)
    doc = {
        "model": {
            "id": mid,
            "name": mid.split("/")[-1].upper(),
            "lab": mid.split("/")[0],
            "priceUsdPer1M": {"input": price, "output": None, "cachedInput": None},
            "benchmarks": [
                {
                    "name": b[0],
                    "version": b[1],
                    "score": b[2],
                    "metric": "acc",
                    "source": "",
                }
                for b in benches
            ],
        }
    }
    (out / f"{mid.replace('/', '__')}.json").write_text(json.dumps(doc))


def _snapshot(data_dir: Path, tier: str, model_ids: list[str]) -> None:
    out = data_dir / "tier_prices"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{tier}_prices.json").write_text(
        json.dumps(
            [
                {
                    "tier": tier,
                    "model_id": mid,
                    "name": mid,
                    "input": 1.0,
                    "output": 1.0,
                    "cached_read": None,
                    "limit": None,
                    "free": False,
                }
                for mid in model_ids
            ]
        )
    )


class RowsForEmptyRosterTest(unittest.TestCase):
    def test_empty_roster_yields_no_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/m1")
            _snapshot(d, "zen", ["l/m1"])
            orig = availability.load_roster
            availability.load_roster = lambda _d, _name: set()
            try:
                result = rows_for(d, ["zen"], price="fix", all_models=True)
            finally:
                availability.load_roster = orig
            self.assertEqual(result.rows, [])
            self.assertEqual(result.listed, [])
            self.assertEqual(result.counts, {"zen": 0})


class RowsForDroppedModelTest(unittest.TestCase):
    def test_dropped_model_is_filtered_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/m1")
            _write_model(d, "l/m2")
            _snapshot(d, "zen", ["l/m1", "l/m2"])

            orig_roster = availability.load_roster
            orig_offered = availability.current_offered
            availability.load_roster = lambda _d, _name: {"l/m1", "l/m2"}
            availability.current_offered = lambda _d, bypass=False: type(
                "R", (), {"offered": {"l/m1"}, "warnings": ()}
            )()
            try:
                result = rows_for(d, ["zen"], price="fix", all_models=False)
            finally:
                availability.load_roster = orig_roster
                availability.current_offered = orig_offered
            ids = {r.model.model_id for r in result.rows}
            self.assertEqual(ids, {"l/m1"})
            self.assertNotIn("l/m2", ids)


class RowsForSyntheticTest(unittest.TestCase):
    def test_docs_documented_slug_without_catalog_match_synthesizes_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/m1")
            _snapshot(d, "zen", ["ghost-slug"])
            (d / "plans").mkdir()
            (d / "plans" / "zen.json").write_text(
                json.dumps({"models": {"ghost-slug": {"name": "Ghost", "free": False}}})
            )
            result = rows_for(d, ["zen"], price="fix", all_models=True)
            synth = [r for r in result.rows if r.model.model_id == "ghost-slug"]
            self.assertEqual(len(synth), 1)
            self.assertTrue(synth[0].synthetic)
            self.assertIsNone(synth[0].score)


class RowsForEstFallbackTest(unittest.TestCase):
    def test_missing_plan_snapshot_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/m1")
            _snapshot(d, "zen", ["l/m1"])
            result = rows_for(d, ["zen"], price="est", all_models=True)
            self.assertGreaterEqual(len(result.rows), 1)


class CatalogCompositionTest(unittest.TestCase):
    def test_curated_arena_boards_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/m1")
            # arena cache
            (d / "aiarena").mkdir()
            (d / "aiarena" / "scores.json").write_text(
                json.dumps({"meta": {"schema_rev": 1}, "scores": {"m1": 1500.0}})
            )
            # manual model
            (d / "manual").mkdir()
            (d / "manual" / "x__m2.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "x/m2",
                            "name": "M2",
                            "lab": "x",
                            "priceUsdPer1M": {
                                "input": 2.0,
                                "output": 2.0,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 60.0,
                                    "metric": "acc",
                                    "source": "",
                                },
                                {
                                    "name": "SWE-Bench",
                                    "version": "Verified",
                                    "score": 30.0,
                                    "metric": "acc",
                                    "source": "",
                                },
                            ],
                        }
                    }
                )
            )
            # override for upstream model
            (d / "overrides").mkdir()
            (d / "overrides" / "m1.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "l/m1",
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 99.0,
                                    "metric": "acc",
                                    "source": "https://example.com",
                                }
                            ],
                        }
                    }
                )
            )
            models, curated, conflicts = load_catalog(d)
            ids = {m.model_id for m in models}
            self.assertIn("l/m1", ids)
            self.assertIn("x/m2", ids)
            self.assertIn("l/m1", curated.curated)
            # arena score attached to matching model
            m1 = next(m for m in models if m.model_id == "l/m1")
            self.assertIn("LMArena Elo", m1.scores)


if __name__ == "__main__":
    unittest.main()
