"""Service-level tests for the shared list/next row pipeline.

Synthetic tmp data dirs only; no network, no live API.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import opencode_models.availability as availability
from opencode_models import go_pricing, zen_pricing
from opencode_models.catalog import load_catalog
from opencode_models.rows import rows_for
from tests import test_zen_pricing as zen_fixture


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
    out = data_dir / "plans"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{tier}.json").write_text(
        json.dumps(
            {
                "meta": {
                    "source": "test",
                    "fetched_at": "2099-01-01T00:00:00+00:00",
                    "schema_rev": 1,
                    "plans": [tier],
                },
                "models": {
                    mid: {
                        "name": mid,
                        "free": False,
                        "tiers": [
                            {
                                "label": None,
                                "in": 1.0,
                                "out": 1.0,
                                "cached": None,
                                "cached_write": None,
                            }
                        ],
                        "limits": None,
                        "requests_per_month": None,
                        "assumptions": None,
                    }
                    for mid in model_ids
                },
            }
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


class RowsForAliasMergeTest(unittest.TestCase):
    def test_contributor_twins_merge_to_one_model_with_shared_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "meta/muse-spark-1.3")
            _write_model(d, "meta/muse-spark-1.3-contributor")
            _snapshot(d, "zen", ["meta/muse-spark-1.3"])
            result = rows_for(d, ["zen"], price="fix", all_models=True)
            ids = {m.model_id for m in result.models}
            self.assertEqual(ids, {"meta/muse-spark-1.3"})
            scores = {r.model.model_id for r in result.rows}
            self.assertNotIn("meta/muse-spark-1.3-contributor", scores)


class CatalogCompositionTest(unittest.TestCase):
    def test_designation_twins_merge_at_the_catalog_choke_point(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "meta/muse-spark-1.3")
            _write_model(d, "meta/muse-spark-1.3-contributor")
            models, _curated, _conflicts, warnings = load_catalog(d)
            self.assertEqual([m.model_id for m in models], ["meta/muse-spark-1.3"])
            self.assertEqual(warnings, [])

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
            models, curated, conflicts, _warnings = load_catalog(d)
            ids = {m.model_id for m in models}
            self.assertIn("l/m1", ids)
            self.assertIn("x/m2", ids)
            self.assertIn("l/m1", curated.curated)
            # arena score attached to matching model
            m1 = next(m for m in models if m.model_id == "l/m1")
            self.assertIn("LMArena Elo", m1.scores)


if __name__ == "__main__":
    unittest.main()


class RowsForPlanRefreshTest(unittest.TestCase):
    def _stale_snapshot(self, data_dir, tier, hours_old=30):
        import json
        from datetime import datetime, timedelta, timezone

        out = data_dir / "plans"
        out.mkdir(parents=True, exist_ok=True)
        past = datetime.now(timezone.utc) - timedelta(hours=hours_old)
        (out / f"{tier}.json").write_text(
            json.dumps(
                {
                    "meta": {
                        "source": "test",
                        "fetched_at": past.isoformat(timespec="seconds"),
                        "schema_rev": 1,
                        "plans": [tier],
                    },
                    "models": {},
                }
            )
        )

    def test_stale_plans_refresh_from_docs_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._stale_snapshot(d, "zen", 30)
            with mock.patch.object(
                zen_pricing, "_fetch", return_value=zen_fixture.FIXTURE_HTML
            ):
                result = rows_for(d, ["zen"], price="fix", all_models=True)
            doc = json.loads((d / "plans" / "zen.json").read_text())
            self.assertIn("model-a", doc["models"])
            self.assertTrue(any("refreshed" in w for w in result.warnings))

    def test_fresh_plans_perform_no_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # fresh snapshot
            import json
            from datetime import datetime, timezone

            out = d / "plans"
            out.mkdir(parents=True, exist_ok=True)
            (out / "zen.json").write_text(
                json.dumps(
                    {
                        "meta": {
                            "source": "test",
                            "fetched_at": datetime.now(timezone.utc).isoformat(
                                timespec="seconds"
                            ),
                            "schema_rev": 1,
                            "plans": ["zen"],
                        },
                        "models": {},
                    }
                )
            )
            with mock.patch.object(
                zen_pricing, "_fetch", side_effect=AssertionError("no net")
            ):
                rows_for(d, ["zen"], price="fix", all_models=True)

    def test_keyless_run_performs_no_fetch_anywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # fresh snapshots so no refresh
            import json
            from datetime import datetime, timezone

            out = d / "plans"
            out.mkdir(parents=True, exist_ok=True)
            (out / "zen.json").write_text(
                json.dumps(
                    {
                        "meta": {
                            "source": "test",
                            "fetched_at": datetime.now(timezone.utc).isoformat(
                                timespec="seconds"
                            ),
                            "schema_rev": 1,
                            "plans": ["zen"],
                        },
                        "models": {},
                    }
                )
            )
            avail = d / "available"
            avail.mkdir(parents=True, exist_ok=True)
            (avail / "zen.json").write_text(
                json.dumps(
                    {
                        "meta": {
                            "fetched_at": datetime.now(timezone.utc).isoformat(
                                timespec="seconds"
                            )
                        },
                        "models": {"m": True},
                    }
                )
            )
            os.environ.pop("OPENCODE_API_KEY", None)
            with mock.patch.object(
                zen_pricing, "_fetch", side_effect=AssertionError("no net")
            ):
                with mock.patch.object(
                    go_pricing, "_fetch", side_effect=AssertionError("no net")
                ):
                    rows_for(d, ["zen"], price="fix", all_models=True)
