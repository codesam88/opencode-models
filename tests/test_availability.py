"""Which models the plans actually offer, as the provider's own snapshot says.

The shipped slug lists under ``opencode_rank/sources/`` are hand-maintained
and drift: providers unlist models and move them between plans, and a model
that is no longer offered is still displayed, scored and recommended as if it
were. The tier pricing snapshot that ``fetch`` writes is the provider's own
list, so it is the record these operations should be limited to.
"""

import json
import tempfile
import unittest
from pathlib import Path

from opencode_rank.availability import available_ids, load_available, unlisted


def _snapshot(data_dir: Path, tier: str, model_ids: list[str]) -> None:
    out = data_dir / "tier_prices"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{tier}_prices.json").write_text(json.dumps(
        [{"tier": tier, "model_id": mid, "name": mid, "input": 1.0,
          "output": 1.0, "cached_read": None, "limit": None, "free": False}
         for mid in model_ids]))


class AvailabilityTest(unittest.TestCase):
    def test_it_reads_the_plans_own_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _snapshot(d, "zen", ["m1", "m2"])
            _snapshot(d, "go", ["m3"])
            self.assertEqual(load_available(d),
                             {"zen": {"m1", "m2"}, "go": {"m3"}})

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
            (out / "zen_prices.json").write_text(json.dumps(
                [{"tier": "zen", "model_id": None, "name": "Mystery"},
                 {"tier": "zen", "model_id": "m1", "name": "M1"}]))
            self.assertEqual(load_available(d)["zen"], {"m1"})

    def test_it_reports_what_the_provider_has_dropped(self):
        from opencode_rank import sources
        real = sources.load_source
        sources.load_source = lambda tier: {"m1", "m2"}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp)
                _snapshot(d, "go", ["m1"])
                dropped = unlisted(d)
        finally:
            sources.load_source = real
        # the shipped list still carries m2, which the provider dropped
        self.assertEqual(dropped["go"], {"m2"})

    def test_nothing_is_reported_dropped_without_a_snapshot(self):
        from opencode_rank import sources
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(unlisted(Path(tmp)), {})

    def test_a_model_offered_only_through_a_free_slug_counts_as_offered(self):
        # the tier pages list free variants under their own slug
        # ("step-5-preview-free") while the catalog carries one model under
        # the base slug ("stepfun/step-5-preview"): exact matching drops the
        # model from the pool entirely, which is how every free row vanished
        # from the list view
        from opencode_rank.availability import offered
        ids = {"step-5-preview-free", "m1"}
        self.assertTrue(offered("stepfun/step-5-preview", ids))
        self.assertTrue(offered("a/m1", ids))
        self.assertFalse(offered("a/gone", ids))


if __name__ == "__main__":
    unittest.main()



class LivePrecedenceTest(unittest.TestCase):
    def test_the_live_snapshot_wins_over_the_docs_snapshot(self):
        from opencode_rank.live import save_live
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _snapshot(d, "zen", ["stale-model"])
            save_live(d, {"zen": {"live-model"}})
            self.assertEqual(load_available(d)["zen"], {"live-model"})

    def test_the_docs_snapshot_survives_where_live_was_never_fetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _snapshot(d, "zen", ["doc-model"])
            self.assertEqual(load_available(d)["zen"], {"doc-model"})
