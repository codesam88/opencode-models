"""Which models the plans actually offer, as the provider's own snapshot says.

Availability resolves a tier's offered slugs along the chain live snapshot ->
docs snapshot -> runtime plan snapshot -> shipped plan fallback. These tests
cover that resolution and the helpers that filter the catalog to it.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import opencode_models.availability as availability
from opencode_models.availability import (
    _snapshot_ids,
    available_ids,
    load_available,
)


def _snapshot(data_dir: Path, tier: str, model_ids: list[str]) -> None:
    out = data_dir / "plans"
    out.mkdir(parents=True, exist_ok=True)
    models = {}
    for mid in model_ids:
        if tier == "go":
            limits = {"go": None, "go_plus": None}
            rpm = {"go": None, "go_plus": None}
        else:
            limits = None
            rpm = None
        models[mid] = {
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
            "limits": limits,
            "requests_per_month": rpm,
            "assumptions": None,
        }
    (out / f"{tier}.json").write_text(
        json.dumps({"meta": {"plans": [tier]}, "models": models})
    )


class AvailabilityTest(unittest.TestCase):
    def test_it_reads_the_plans_own_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _snapshot(d, "zen", ["m1", "m2"])
            _snapshot(d, "go", ["m3"])
            self.assertEqual(load_available(d), {"zen": {"m1", "m2"}, "go": {"m3"}})

    def test_any_plan_counts_as_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _snapshot(d, "zen", ["m1"])
            _snapshot(d, "go", ["m1", "m2"])
            self.assertEqual(available_ids(d), {"m1", "m2"})

    def test_a_tier_the_provider_dropped_is_not_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _snapshot(d, "go", ["m1"])
            self.assertNotIn("m2", available_ids(d))

    def test_a_tier_with_no_snapshot_is_unknown_not_empty(self):
        # "we have not fetched this" is not "the plan offers nothing", and
        # restricting the console to an empty set would hide everything
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_available(Path(tmp))["zen"])

    def test_nothing_known_means_no_restriction(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(available_ids(Path(tmp)))

    def test_a_line_without_a_model_id_does_not_make_a_model_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "plans").mkdir(parents=True, exist_ok=True)
            (d / "plans" / "zen.json").write_text(
                json.dumps({"models": {"m1": {"free": False}}})
            )
            self.assertEqual(load_available(d)["zen"], {"m1"})

    def test_a_directory_at_the_snapshot_path_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "plans").mkdir(parents=True, exist_ok=True)
            (d / "plans" / "zen.json").mkdir()
            self.assertIsNone(load_available(d)["zen"])
            self.assertIsNone(_snapshot_ids(d, "zen"))

    def test_non_dict_rows_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "plans").mkdir(parents=True, exist_ok=True)
            (d / "plans" / "zen.json").write_text(
                json.dumps({"models": {"m1": {"free": False}}})
            )
            self.assertEqual(load_available(d)["zen"], {"m1"})
            self.assertEqual(_snapshot_ids(d, "zen"), {"m1"})

    def test_a_model_offered_only_through_a_free_slug_counts_as_offered(self):
        # the tier pages list free variants under their own slug
        # ("step-5-preview-free") while the catalog carries one model under
        # the base slug ("stepfun/step-5-preview"): exact matching drops the
        # model from the pool entirely, which is how every free row vanished
        # from the list view
        from opencode_models.availability import offered

        ids = {"step-5-preview-free", "m1"}
        self.assertTrue(offered("stepfun/step-5-preview", ids))
        self.assertTrue(offered("a/m1", ids))
        self.assertFalse(offered("a/gone", ids))


if __name__ == "__main__":
    unittest.main()


class LivePrecedenceTest(unittest.TestCase):
    def test_the_live_snapshot_wins_over_the_docs_snapshot(self):
        from opencode_models.live import save_live

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _snapshot(d, "zen", ["stale-model"])
            save_live(d, {"zen": {"live-model": False}})
            self.assertEqual(load_available(d)["zen"], {"live-model": False})

    def test_the_docs_snapshot_survives_where_live_was_never_fetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _snapshot(d, "zen", ["doc-model"])
            self.assertEqual(load_available(d)["zen"], {"doc-model"})


class LoadRosterPrecedenceTest(unittest.TestCase):
    def test_live_beats_plans_beats_shipped(self):
        import json
        from pathlib import Path

        from opencode_models.availability import load_roster
        from opencode_models.live import save_live

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # shipped fallback always present for zen; add the other layers
            (d / "plans").mkdir()
            (d / "plans" / "zen.json").write_text(
                json.dumps({"models": {"plan-only": {"free": False}}})
            )
            save_live(d, {"zen": {"live-only": False}})

            self.assertEqual(load_roster(d, "zen"), {"live-only"})

            (d / "available" / "zen.json").unlink()
            self.assertEqual(load_roster(d, "zen"), {"plan-only"})

            (d / "plans" / "zen.json").unlink()
            shipped = load_roster(d, "zen")
            self.assertIn("claude-opus-4-5", shipped)

    def test_unknown_tier_raises_keyerror(self):
        from opencode_models.availability import load_roster

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError):
                load_roster(Path(tmp), "nope")


class FreeSlugsTest(unittest.TestCase):
    def test_plans_free_flags(self):
        import json
        from pathlib import Path

        from opencode_models.availability import free_slugs

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "plans").mkdir(parents=True, exist_ok=True)
            (d / "plans" / "zen.json").write_text(
                json.dumps(
                    {
                        "models": {
                            "doc-free": {"free": True},
                            "doc-paid": {"free": False},
                            "plan-free": {"free": True},
                            "plan-paid": {"free": False},
                        }
                    }
                )
            )
            self.assertEqual(free_slugs(d, "zen"), {"doc-free", "plan-free"})


class OfferedCanonicalTest(unittest.TestCase):
    def test_variant_and_base_cross_match(self):
        from opencode_models.availability import offered

        self.assertTrue(offered("lab/base", {"base-contributor"}))
        self.assertTrue(offered("lab/base-contributor", {"base"}))
        self.assertFalse(offered("lab/absent", {"base"}))

    def test_separator_and_case_differences_do_not_break_a_match(self):
        # the sitemap spells slugs with dashes (muse-spark-1-3) while the
        # live config and catalog ids use dots (muse-spark-1.3), and the
        # registry keeps vendor capitalisation (MiniMax-M3) against a
        # lowercase roster slug: none of those name a different model
        from opencode_models.availability import offered, slug_matches

        self.assertTrue(slug_matches("meta/muse-spark-1-3", "muse-spark-1.3"))
        self.assertTrue(slug_matches("minimax/MiniMax-M3", "minimax-m3"))
        self.assertTrue(offered("meta/muse-spark-1-3", {"muse-spark-1.3"}))
        self.assertTrue(offered("minimax/MiniMax-M3", {"minimax-m3"}))

    def test_folding_separators_does_not_merge_distinct_models(self):
        # only separators and case fold — a suffix still names another
        # model, so the two Qwen variants stay apart
        from opencode_models.availability import offered, slug_matches

        self.assertFalse(slug_matches("a/qwen3.8-flash", "qwen3.8-flash-next"))
        self.assertFalse(slug_matches("a/qwen3.8-max", "qwen3.8-max-0902"))
        self.assertFalse(offered("a/qwen3.8-flash-next", {"qwen3.8-flash"}))

    def test_dict_ids_are_supported(self):
        from opencode_models.availability import offered

        ids = {"m1": True, "m2": False}
        self.assertTrue(offered("a/m1", ids))
        self.assertFalse(offered("a/gone", ids))


class ShippedAliasesGateTest(unittest.TestCase):
    def test_shipped_aliases_keys_and_entries(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        aliases = json.loads(
            (root / "opencode_models" / "data" / "aliases.json").read_text()
        )
        go = json.loads(
            (root / "opencode_models" / "data" / "plans" / "go.json").read_text()
        )
        zen = json.loads(
            (root / "opencode_models" / "data" / "plans" / "zen.json").read_text()
        )
        keys = set(go.get("models", {})) | set(zen.get("models", {}))
        self.assertTrue(aliases)
        for slug, entry in aliases.items():
            self.assertIn(slug, keys)
            self.assertIsInstance(entry, dict)
            self.assertIn("model_id", entry)
            self.assertIn("source", entry)


class AvailabilityAgeGateTest(unittest.TestCase):
    def _snapshot(self, data_dir, tier, hours_old):
        import json
        from datetime import datetime, timedelta, timezone

        d = Path(data_dir) / "available"
        d.mkdir(parents=True, exist_ok=True)
        past = datetime.now(timezone.utc) - timedelta(hours=hours_old)
        (d / f"{tier}.json").write_text(
            json.dumps(
                {
                    "meta": {"fetched_at": past.isoformat(timespec="seconds")},
                    "models": {"m-snap": True},
                }
            )
        )

    def test_stale_snapshot_pulls_live(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._snapshot(d, "go", 5)
            self._snapshot(d, "zen", 5)
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with mock.patch.object(
                    availability,
                    "fetch_served",
                    return_value={"go": {"m-live"}, "zen": {"m-live"}},
                ) as fs:
                    with mock.patch.object(
                        availability, "fetch_live", return_value={"go": {}, "zen": {}}
                    ):
                        res = availability.current_offered(d)
            self.assertTrue(fs.called)
            self.assertTrue(res.live)
            self.assertEqual(res.offered, {"m-live"})

    def test_fresh_snapshot_performs_no_network(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._snapshot(d, "go", 1)
            self._snapshot(d, "zen", 1)
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with mock.patch.object(
                    availability, "fetch_served", side_effect=AssertionError("no net")
                ):
                    res = availability.current_offered(d)
            self.assertFalse(res.live)
            self.assertEqual(res.offered, {"m-snap"})

    def test_missing_snapshot_pulls_live(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with mock.patch.object(
                    availability,
                    "fetch_served",
                    return_value={"go": {"m-live"}, "zen": {"m-live"}},
                ) as fs:
                    with mock.patch.object(
                        availability, "fetch_live", return_value={"go": {}, "zen": {}}
                    ):
                        res = availability.current_offered(d)
            self.assertTrue(fs.called)
            self.assertTrue(res.live)

    def test_unreachable_pull_warns_and_uses_snapshots(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._snapshot(d, "go", 5)
            self._snapshot(d, "zen", 5)
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with mock.patch.object(
                    availability, "fetch_served", side_effect=OSError("down")
                ):
                    res = availability.current_offered(d)
            self.assertFalse(res.live)
            self.assertIn("m-snap", res.offered)
            self.assertTrue(any("unreachable" in w for w in res.warnings))


class PlanRecordsTest(unittest.TestCase):
    """Normalized plan records: one loader fills missing mixes from the median."""

    def _write_plan(self, d: Path, models: dict) -> None:
        out = d / "plans"
        out.mkdir(parents=True, exist_ok=True)
        (out / "go.json").write_text(
            json.dumps(
                {
                    "meta": {
                        "source": "test",
                        "fetched_at": "2099-01-01T00:00:00+00:00",
                        "schema_rev": 1,
                        "plans": ["go"],
                    },
                    "models": models,
                }
            )
        )

    def _rec(self, price=1.0, assumptions=None, free=False):
        return {
            "name": "M",
            "free": free,
            "tiers": []
            if free
            else [{"label": None, "in": 1.0, "out": 2.0, "cached": 0.5}],
            "limits": {"go": 60, "go_plus": 120},
            "requests_per_month": {"go": 1, "go_plus": 2},
            "assumptions": assumptions,
            "dropped": False,
            "first_seen": None,
        }

    def test_unknown_tier_yields_empty(self):
        # no local snapshot and nothing shipped for this tier
        with tempfile.TemporaryDirectory() as tmp:
            records, filled = availability.plan_records(Path(tmp), "nope")
            self.assertEqual(records, {})
            self.assertEqual(filled, set())

    def test_missing_mixes_fall_back_to_the_median(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._write_plan(
                d,
                {
                    "a": self._rec(assumptions={"in": 100, "cached": 1000, "out": 10}),
                    "b": self._rec(assumptions={"in": 300, "cached": 3000, "out": 30}),
                    "c": self._rec(assumptions=None),
                },
            )
            records, filled = availability.plan_records(d, "go")
            self.assertEqual(filled, {"c"})
            self.assertEqual(
                records["c"]["assumptions"], {"in": 200, "cached": 2000, "out": 20}
            )
            # published and untouched models pass through as-is
            self.assertEqual(
                records["a"]["assumptions"], {"in": 100, "cached": 1000, "out": 10}
            )

    def test_nothing_missing_nothing_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._write_plan(
                d, {"a": self._rec(assumptions={"in": 1, "cached": 2, "out": 3})}
            )
            records, filled = availability.plan_records(d, "go")
            self.assertEqual(filled, set())
            self.assertEqual(
                records["a"]["assumptions"], {"in": 1, "cached": 2, "out": 3}
            )

    def test_free_models_are_never_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._write_plan(
                d,
                {
                    "a": self._rec(assumptions={"in": 100, "cached": 1000, "out": 10}),
                    "freebie": self._rec(free=True),
                },
            )
            _, filled = availability.plan_records(d, "go")
            self.assertEqual(filled, set())
