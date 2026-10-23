"""Which models the plans actually offer, as the provider's own snapshot says.

Availability resolves a tier's offered slugs along the chain live snapshot ->
docs snapshot -> runtime plan snapshot -> shipped plan fallback. These tests
cover that resolution and the helpers that filter the catalog to it.
"""

import json
import tempfile
import unittest
from pathlib import Path

from opencode_models.availability import (
    _snapshot_ids,
    available_ids,
    load_available,
)


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
            out = d / "tier_prices"
            out.mkdir(parents=True, exist_ok=True)
            (out / "zen_prices.json").write_text(
                json.dumps(
                    [
                        {"tier": "zen", "model_id": None, "name": "Mystery"},
                        {"tier": "zen", "model_id": "m1", "name": "M1"},
                    ]
                )
            )
            self.assertEqual(load_available(d)["zen"], {"m1"})

    def test_a_directory_at_the_snapshot_path_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            out = d / "tier_prices"
            out.mkdir(parents=True, exist_ok=True)
            (out / "zen_prices.json").mkdir()
            self.assertIsNone(load_available(d)["zen"])
            self.assertIsNone(_snapshot_ids(d, "zen"))

    def test_non_dict_rows_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            out = d / "tier_prices"
            out.mkdir(parents=True, exist_ok=True)
            (out / "zen_prices.json").write_text(
                json.dumps(
                    [
                        {"tier": "zen", "model_id": "m1", "name": "M1"},
                        "not a dict",
                    ]
                )
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
    def test_live_beats_tier_prices_beats_plans_beats_shipped(self):
        import json
        from pathlib import Path

        from opencode_models.availability import load_roster
        from opencode_models.live import save_live

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # shipped fallback always present for zen; add the other layers
            (d / "plans").mkdir()
            (d / "plans" / "zen.json").write_text(
                json.dumps({"models": {"runtime-only": {"free": False}}})
            )
            _snapshot(d, "zen", ["docs-only"])
            save_live(d, {"zen": {"live-only": False}})

            self.assertEqual(load_roster(d, "zen"), {"live-only"})

            (d / "available" / "zen.json").unlink()
            self.assertEqual(load_roster(d, "zen"), {"docs-only"})

            (d / "tier_prices" / "zen_prices.json").unlink()
            self.assertEqual(load_roster(d, "zen"), {"runtime-only"})

            (d / "plans" / "zen.json").unlink()
            shipped = load_roster(d, "zen")
            self.assertIn("claude-opus-4-5", shipped)

    def test_unknown_tier_raises_keyerror(self):
        from opencode_models.availability import load_roster

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError):
                load_roster(Path(tmp), "nope")


class FreeSlugsTest(unittest.TestCase):
    def test_tier_prices_and_plans_flags_union(self):
        import json
        from pathlib import Path

        from opencode_models.availability import free_slugs

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _snapshot(d, "zen", [])
            # tier_prices free flag
            out = d / "tier_prices"
            out.mkdir(parents=True, exist_ok=True)
            (out / "zen_prices.json").write_text(
                json.dumps(
                    [
                        {
                            "tier": "zen",
                            "model_id": "doc-free",
                            "name": "Doc Free",
                            "free": True,
                        },
                        {
                            "tier": "zen",
                            "model_id": "doc-paid",
                            "name": "Doc Paid",
                            "free": False,
                        },
                    ]
                )
            )
            (d / "plans").mkdir()
            (d / "plans" / "zen.json").write_text(
                json.dumps(
                    {
                        "models": {
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
