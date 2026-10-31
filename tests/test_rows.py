"""Service-level tests for the shared list/next row pipeline.

Synthetic tmp data dirs only; no network, no live API.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import opencode_models.availability as availability
from opencode_models import go_pricing, zen_pricing
from opencode_models import sync as sync_mod
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


class RowsForCatalogAuthorityTest(unittest.TestCase):
    """The models.dev slice decides which roster slugs get a row."""

    def _modelsdev(self, d: Path, providers: dict) -> None:
        (d / "modelsdev.json").write_text(
            json.dumps(
                {
                    "meta": {"fetched_at": "2099-01-01T00:00:00+00:00"},
                    "providers": providers,
                }
            )
        )

    def _rows(self, d, roster, *, tier="zen", all_models=False, price="fix"):
        orig_roster = availability.load_roster
        orig_offered = availability.current_offered
        availability.load_roster = lambda _d, _t: set(roster)
        availability.current_offered = lambda _d, bypass=False: type(
            "R", (), {"offered": set(roster), "warnings": ()}
        )()
        try:
            return rows_for(d, [tier], price=price, all_models=all_models)
        finally:
            availability.load_roster = orig_roster
            availability.current_offered = orig_offered

    def test_snapshot_records_decide_which_roster_slugs_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/m1")
            _snapshot(d, "zen", ["m-alpha"])
            self._modelsdev(
                d,
                {
                    "opencode-go": {"models": {}},
                    "opencode": {
                        "models": {
                            "m-alpha": {
                                "id": "m-alpha",
                                "canonical_model_id": "l/m1",
                                "name": "M Alpha",
                                "cost": {
                                    "input": 1.0,
                                    "output": 2.0,
                                    "cache_read": 0.5,
                                },
                            },
                            "m-beta": {
                                "id": "m-beta",
                                "name": "M Beta",
                                "cost": {
                                    "input": 2.0,
                                    "output": 4.0,
                                    "cache_read": 1.0,
                                },
                            },
                        }
                    },
                },
            )
            # served, but in no catalog record: not rendered, not filtered
            result = self._rows(
                d, {"m-alpha", "m-beta", "omen-alpha", "test"}, tier="zen"
            )
            self.assertEqual(
                {r.model.model_id for r in result.rows}, {"m-alpha", "m-beta"}
            )

    def test_snapshot_rows_take_the_name_scores_and_price_from_their_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/m1")
            # a second scored card so the benchmark scales have a spread
            _write_model(
                d,
                "l/m2",
                benches=[
                    ("Terminal-Bench", "2.1", 70.0),
                    ("SWE-Bench", "Verified", 40.0),
                ],
            )
            _snapshot(d, "zen", ["m-beta"])
            self._modelsdev(
                d,
                {
                    "opencode-go": {"models": {}},
                    "opencode": {
                        "models": {
                            "m-alpha": {
                                "id": "m-alpha",
                                "canonical_model_id": "l/m1",
                                "name": "M Alpha",
                                "cost": {
                                    "input": 1.0,
                                    "output": 2.0,
                                    "cache_read": 0.5,
                                },
                            },
                            "m-beta": {
                                "id": "m-beta",
                                "name": "M Beta",
                                "cost": {
                                    "input": 2.0,
                                    "output": 4.0,
                                    "cache_read": 1.0,
                                },
                            },
                        }
                    },
                },
            )
            result = self._rows(d, {"m-alpha", "m-beta"}, tier="zen")
            by_id = {r.model.model_id: r for r in result.rows}
            # name and price from the record, scores from the card it names
            self.assertEqual(by_id["m-alpha"].model.name, "M Alpha")
            self.assertIsNotNone(by_id["m-alpha"].score)
            self.assertEqual(by_id["m-alpha"].model.price_input, 1.0)
            self.assertEqual(by_id["m-beta"].model.name, "M Beta")
            self.assertIsNone(by_id["m-beta"].score)
            # no card record: always listed, never dropped as unrankable
            self.assertTrue(by_id["m-beta"].synthetic)

    def test_without_a_snapshot_the_cards_decide(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/m1")
            _snapshot(d, "zen", ["l/m1"])
            # a served slug no card carries renders nothing: synthesis is gone
            result = self._rows(d, {"l/m1", "m-ghost"}, tier="zen")
            self.assertEqual({r.model.model_id for r in result.rows}, {"l/m1"})


class RowsForEstFallbackTest(unittest.TestCase):
    def test_missing_plan_snapshot_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/m1")
            _snapshot(d, "zen", ["l/m1"])
            result = rows_for(d, ["zen"], price="est", all_models=True)
            self.assertGreaterEqual(len(result.rows), 1)


class EstGoMedianFallbackTest(unittest.TestCase):
    """est prices any row without its own mix from the Go plan's median."""

    GO_MIX = {"in": 100, "cached": 1000, "out": 10}

    def _plan(self, d: Path, tier: str, models: dict) -> None:
        (d / "plans").mkdir(parents=True, exist_ok=True)
        (d / "plans" / f"{tier}.json").write_text(
            json.dumps({"meta": {}, "models": models})
        )

    def _rec(self, price=(1.0, 1.0, None), assumptions=None, name="M"):
        return {
            "name": name,
            "free": False,
            "tiers": [
                {
                    "label": None,
                    "in": price[0],
                    "out": price[1],
                    "cached": price[2],
                    "cached_write": None,
                }
            ],
            "limits": None,
            "requests_per_month": None,
            "assumptions": assumptions,
        }

    def _rows(self, d, roster, *, tier, price="est"):
        orig_roster = availability.load_roster
        orig_offered = availability.current_offered
        availability.load_roster = lambda _d, _t: set(roster)
        availability.current_offered = lambda _d, bypass=False: type(
            "R", (), {"offered": set(roster), "warnings": ()}
        )()
        try:
            return rows_for(d, [tier], price=price, all_models=True)
        finally:
            availability.load_roster = orig_roster
            availability.current_offered = orig_offered

    def _go_publishes_a_mix(self, d: Path) -> None:
        # one publishing record: its mix is the plan median
        self._plan(d, "go", {"g-a": self._rec(assumptions=self.GO_MIX)})

    def test_a_zen_row_without_a_mix_prices_from_the_go_median(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/z-model")
            self._go_publishes_a_mix(d)
            self._plan(d, "zen", {"z-model": self._rec(price=(2.0, 4.0, 1.0))})
            result = self._rows(d, {"z-model"}, tier="zen")
            row = next(r for r in result.rows if r.endpoint_id == "z-model")
        # (100*2.0 + 1000*1.0 + 10*4.0) / 1e6 — not the fix mix's 1.8
        self.assertAlmostEqual(row.price, 0.00124, places=12)
        self.assertIn("median", row.markers)

    def test_a_row_with_no_record_at_all_prices_from_the_go_median(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._go_publishes_a_mix(d)
            (d / "modelsdev.json").write_text(
                json.dumps(
                    {
                        "meta": {"fetched_at": "2099-01-01T00:00:00+00:00"},
                        "providers": {
                            "opencode-go": {
                                "models": {
                                    "new-model": {
                                        "id": "new-model",
                                        "name": "New Model",
                                        "cost": {
                                            "input": 1.0,
                                            "output": 3.0,
                                            "cache_read": 0.5,
                                        },
                                    }
                                }
                            },
                            "opencode": {"models": {}},
                        },
                    }
                )
            )
            result = self._rows(d, {"new-model"}, tier="go")
            row = next(r for r in result.rows if r.endpoint_id == "new-model")
        # (100*1.0 + 1000*0.5 + 10*3.0) / 1e6 — not the fix mix's 1.1
        self.assertAlmostEqual(row.price, 0.00063, places=12)
        self.assertIn("median", row.markers)

    def test_a_published_mix_still_wins_over_the_median(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/g-a")
            self._plan(
                d,
                "go",
                {"g-a": self._rec(price=(3.0, 9.0, 1.5), assumptions=self.GO_MIX)},
            )
            result = self._rows(d, {"g-a"}, tier="go")
            row = next(r for r in result.rows if r.endpoint_id == "g-a")
        # its own mix: (100*3.0 + 1000*1.5 + 10*9.0) / 1e6
        self.assertAlmostEqual(row.price, 0.00189, places=12)
        self.assertNotIn("median", row.markers)

    def test_the_median_never_reaches_another_cost_axis(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/z-model")
            self._go_publishes_a_mix(d)
            self._plan(d, "zen", {"z-model": self._rec(price=(2.0, 4.0, 1.0))})
            for axis in ("fix", "input", "output"):
                result = self._rows(d, {"z-model"}, tier="zen", price=axis)
                row = next(r for r in result.rows if r.endpoint_id == "z-model")
                with self.subTest(axis=axis):
                    self.assertNotIn("median", row.markers)
            # fix still means the weighted 20/60/20 rate: 0.2*2 + 0.6*1 + 0.2*4
            result = self._rows(d, {"z-model"}, tier="zen", price="fix")
            row = next(r for r in result.rows if r.endpoint_id == "z-model")
            self.assertAlmostEqual(row.price, 1.8, places=12)


class EstPricesEveryVariantTest(unittest.TestCase):
    """A labelled tier prices from its own rates, not the base tier's.

    Off-Peak/Peak and <=100K/>100K lines exist to be priced apart: each
    line's est cost is its own tier's rates under the record's mix.
    """

    MIX = {"in": 100, "cached": 1000, "out": 10}

    def test_peak_and_off_peak_lines_carry_their_own_rates(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "l/ds")
            (d / "plans").mkdir(parents=True, exist_ok=True)
            (d / "plans" / "go.json").write_text(
                json.dumps(
                    {
                        "meta": {},
                        "models": {
                            "ds": {
                                "name": "DS",
                                "free": False,
                                "tiers": [
                                    {
                                        "label": "Off-Peak",
                                        "in": 0.66,
                                        "out": 1.98,
                                        "cached": 0.022,
                                        "cached_write": None,
                                    },
                                    {
                                        "label": "Peak",
                                        "in": 1.32,
                                        "out": 3.96,
                                        "cached": 0.044,
                                        "cached_write": None,
                                    },
                                ],
                                "limits": None,
                                "requests_per_month": None,
                                "assumptions": self.MIX,
                            }
                        },
                    }
                )
            )
            result = rows_for(d, ["go"], price="est", all_models=True)
            by_name = {r.name: r for r in result.rows}
        off = by_name["DS (Off-Peak)"]
        peak = by_name["DS (Peak)"]
        # each line: its own tier's rates under the same published mix
        self.assertAlmostEqual(off.price, 0.0001078, places=12)
        self.assertAlmostEqual(peak.price, 0.0002156, places=12)
        self.assertGreater(peak.price, off.price)
        # the record publishes the mix, so neither line is a median one
        self.assertNotIn("median", off.markers)
        self.assertNotIn("median", peak.markers)


class RowsForTimeTierFilterTest(unittest.TestCase):
    """Off-Peak/Peak lines follow the snapshot's published windows."""

    PEAK = {
        "slugs": ["ds"],
        "windows": [["01:00", "04:00"], ["06:00", "10:00"]],
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri"],
        "tz": "UTC",
        "raw": "DS: Peak hours are 01:00-04:00 and 06:00-10:00 UTC, ...",
    }
    TIME_TIERS = [
        {
            "label": "Off-Peak",
            "in": 0.66,
            "out": 1.98,
            "cached": 0.022,
            "cached_write": None,
        },
        {
            "label": "Peak",
            "in": 1.32,
            "out": 3.96,
            "cached": 0.044,
            "cached_write": None,
        },
    ]
    SIZE_TIERS = [
        {
            "label": "≤ 256K tokens",
            "in": 0.4,
            "out": 1.6,
            "cached": 0.04,
            "cached_write": None,
        },
        {
            "label": "> 256K tokens",
            "in": 1.2,
            "out": 4.8,
            "cached": 0.12,
            "cached_write": None,
        },
    ]

    def _snapshot(self, d: Path, peak=PEAK):
        _write_model(d, "l/ds")
        _write_model(d, "l/size")
        (d / "plans").mkdir(parents=True, exist_ok=True)
        doc = {
            "meta": {
                "source": "test",
                "fetched_at": "2099-01-01T00:00:00+00:00",
                "schema_rev": 1,
                "plans": ["go"],
            },
            "models": {
                "ds": {
                    "name": "DS",
                    "free": False,
                    "tiers": list(self.TIME_TIERS),
                    "limits": None,
                    "requests_per_month": None,
                    "assumptions": None,
                },
                "size": {
                    "name": "SIZE",
                    "free": False,
                    "tiers": list(self.SIZE_TIERS),
                    "limits": None,
                    "requests_per_month": None,
                    "assumptions": None,
                },
            },
        }
        if peak is not None:
            doc["peak"] = peak
        (d / "plans" / "go.json").write_text(json.dumps(doc))

    def _rows(self, d, now, all_models=False):
        roster = {"ds", "size"}
        orig_roster = availability.load_roster
        orig_offered = availability.current_offered
        availability.load_roster = lambda _d, _t: set(roster)
        availability.current_offered = lambda _d, bypass=False: type(
            "R", (), {"offered": set(roster), "warnings": ()}
        )()
        try:
            return rows_for(d, ["go"], price="fix", all_models=all_models, now=now).rows
        finally:
            availability.load_roster = orig_roster
            availability.current_offered = orig_offered

    @staticmethod
    def _at(month, day, hour, minute=0):
        return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)

    def _names(self, rows):
        return {r.name for r in rows}

    def test_off_peak_window_keeps_only_the_off_peak_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._snapshot(d)
            names = self._names(self._rows(d, self._at(10, 30, 5)))
        self.assertIn("DS (Off-Peak)", names)
        self.assertNotIn("DS (Peak)", names)

    def test_peak_window_keeps_only_the_peak_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._snapshot(d)
            names = self._names(self._rows(d, self._at(10, 30, 2)))
        self.assertIn("DS (Peak)", names)
        self.assertNotIn("DS (Off-Peak)", names)

    def test_all_models_shows_both_time_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._snapshot(d)
            names = self._names(self._rows(d, self._at(10, 30, 2), all_models=True))
        self.assertIn("DS (Off-Peak)", names)
        self.assertIn("DS (Peak)", names)

    def test_unknown_rule_fails_safe_to_the_peak_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._snapshot(d, peak=None)
            names = self._names(self._rows(d, self._at(10, 30, 5)))
        self.assertIn("DS (Peak)", names)
        self.assertNotIn("DS (Off-Peak)", names)

    def test_token_window_tiers_are_never_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._snapshot(d)
            for now in (self._at(10, 30, 2), self._at(10, 30, 5)):
                names = self._names(self._rows(d, now))
                with self.subTest(now=now):
                    self.assertIn("SIZE (≤ 256K tokens)", names)
                    self.assertIn("SIZE (> 256K tokens)", names)

    def test_rule_written_by_the_same_run_refresh_is_applied(self):
        # The read path refreshes a stale snapshot and then filters with it;
        # reading the rule before the refresh would miss it for one run.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._snapshot(d, peak=None)

            def refresh(_data_dir, _tiers, get=None):
                doc = json.loads((d / "plans" / "go.json").read_text())
                doc["peak"] = self.PEAK
                (d / "plans" / "go.json").write_text(json.dumps(doc))

            with mock.patch.object(sync_mod, "refresh_plan_as_job", refresh):
                names = self._names(self._rows(d, self._at(10, 30, 5)))
        self.assertIn("DS (Off-Peak)", names)
        self.assertNotIn("DS (Peak)", names)


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


class RowsForMarkersTest(unittest.TestCase):
    def _fixture(self, d):
        import json

        _write_model(d, "l/m1")
        _write_model(
            d,
            "l/m2",
            benches=[("Terminal-Bench", "2.1", 60.0), ("SWE-Bench", "Verified", 30.0)],
        )
        _snapshot(d, "go", ["m1", "m2"])
        snap = json.loads((d / "plans" / "go.json").read_text())
        snap["models"]["m1"]["assumptions"] = {
            "in": 100,
            "cached": 1000,
            "out": 10,
        }
        (d / "plans" / "go.json").write_text(json.dumps(snap))

    def test_median_estimated_rows_carry_the_median_key(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._fixture(d)
            result = rows_for(d, ["go"], price="est", all_models=True)
            by_id = {r.model.model_id: r for r in result.rows}
            self.assertEqual(by_id["l/m2"].markers, ("median",))
            self.assertEqual(by_id["l/m1"].markers, ())
            # the median estimate prices the row, not the fixed mix
            self.assertAlmostEqual(by_id["l/m2"].price, 0.00111, places=9)

    def test_curated_rows_carry_the_curated_key_first(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._fixture(d)
            (d / "overrides").mkdir(exist_ok=True)
            (d / "overrides" / "l__m2.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "l/m2",
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 70.0,
                                    "metric": "acc",
                                    "source": "https://example.com",
                                }
                            ],
                        }
                    }
                )
            )
            result = rows_for(d, ["go"], price="est", all_models=True)
            by_id = {r.model.model_id: r for r in result.rows}
            # registry order wins: curated before median
            self.assertEqual(by_id["l/m2"].markers, ("curated", "median"))
            self.assertEqual(by_id["l/m1"].markers, ())

    def test_picks_carry_markers_into_next(self):
        import tempfile

        from opencode_models.ranking import grade_rows, next_up, order_rows

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._fixture(d)
            result = rows_for(d, ["go"], price="est", all_models=True)
            picks = next_up(grade_rows(order_rows(result.rows)), count=2)
            marked = {p.model.model_id: p.markers for p in picks}
            self.assertEqual(marked["l/m2"], ("median",))


class CatalogCompositionTest(unittest.TestCase):
    def _write_override(self, d: Path, mid: str, score: float = 99.0) -> None:
        out = d / "overrides"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{mid.replace('/', '__')}.json").write_text(
            json.dumps(
                {
                    "model": {
                        "id": mid,
                        "benchmarks": [
                            {
                                "name": "Terminal-Bench",
                                "version": "2.1",
                                "score": score,
                                "metric": "acc",
                                "source": "https://example.com",
                            }
                        ],
                    }
                }
            )
        )

    def test_base_keyed_override_applies_to_the_merged_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "meta/muse-spark-1.3")
            _write_model(d, "meta/muse-spark-1.3-contributor")
            self._write_override(d, "meta/muse-spark-1.3")
            models, curated, _conflicts, _warnings = load_catalog(d)
            self.assertEqual([m.model_id for m in models], ["meta/muse-spark-1.3"])
            self.assertIn("meta/muse-spark-1.3", curated.curated)
            self.assertNotIn("meta/muse-spark-1.3-contributor", curated.orphans)

    def test_designation_keyed_override_is_reported_orphan(self):
        # overlays apply to canonical models once: pinning an override to
        # the designation id matches nothing post-merge, so it surfaces
        # as an orphan instead of being silently dropped by plain-wins.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_model(d, "meta/muse-spark-1.3")
            _write_model(d, "meta/muse-spark-1.3-contributor")
            self._write_override(d, "meta/muse-spark-1.3-contributor")
            models, curated, _conflicts, _warnings = load_catalog(d)
            self.assertEqual([m.model_id for m in models], ["meta/muse-spark-1.3"])
            self.assertIn("meta/muse-spark-1.3-contributor", curated.orphans)
            merged = models[0]
            self.assertNotEqual(merged.scores.get("Terminal-Bench 2.1"), 99.0)

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
        import contextlib
        import io

        job = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._stale_snapshot(d, "zen", 30)
            with mock.patch.object(
                zen_pricing, "_fetch", return_value=zen_fixture.FIXTURE_HTML
            ):
                with contextlib.redirect_stdout(job):
                    result = rows_for(d, ["zen"], price="fix", all_models=True)
            doc = json.loads((d / "plans" / "zen.json").read_text())
            self.assertIn("model-a", doc["models"])
        # the refresh reports itself as a labelled job: header first, then
        # the report — and nothing of it joins the caller's warning list
        text = job.getvalue()
        self.assertTrue(text.startswith("Checking models..."), repr(text))
        self.assertIn("plans/zen: refreshed", text)
        self.assertEqual(len(text) - len(text.rstrip("\n")), 2, repr(text))
        self.assertFalse(any("refreshed" in w for w in result.warnings))

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
