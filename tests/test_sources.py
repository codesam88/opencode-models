"""Zen/Go tier membership lists and score/price ranking for text output."""

import unittest

from opencode_models.data import Model
from opencode_models.ranking import by_score_per_price
from opencode_models.sources import available_sources, load_source, members


def _m(model_id, price, score, output=None):
    return Model(
        model_id=model_id,
        name=model_id,
        lab="x",
        price_input=price,
        price_output=output,
        price_cached_input=None,
        scores={} if score is None else {"TB": score},
    )


class SourcesTest(unittest.TestCase):
    def test_ships_zen_and_go(self):
        self.assertEqual(sorted(available_sources()), ["go", "zen"])

    def test_load_returns_slugs(self):
        slugs = load_source("zen")
        self.assertIn("claude-opus-5", slugs)
        self.assertTrue(all("/" not in s for s in slugs))

    def test_unknown_source_raises(self):
        with self.assertRaises(KeyError):
            load_source("nope")


class MembershipTest(unittest.TestCase):
    def test_free_variant_slug_reaches_the_base_model_and_flags_it_free(self):
        models = [_m("xiaomi/mimo-v2.6-flash", 0.16, 50.0), _m("lab/other", 1.0, 40.0)]
        got, free = members(models, {"mimo-v2.6-flash-free", "other"})
        # the tier page offers MiMo-V2.6-Flash Free; the catalog knows one
        # model under the base slug — it must not vanish from the list
        self.assertEqual(
            sorted(m.model_id for m in got), ["lab/other", "xiaomi/mimo-v2.6-flash"]
        )
        self.assertEqual(free, {"xiaomi/mimo-v2.6-flash"})

    def test_plain_slug_member_is_not_free(self):
        _, free = members([_m("a/m1", 1.0, 50.0)], {"m1"})
        self.assertEqual(free, set())

    def test_zero_priced_model_is_free_whatever_the_slug(self):
        _, free = members([_m("a/m1", 0.0, 50.0)], {"m1"})
        self.assertEqual(free, {"a/m1"})

    def test_no_slugs_means_every_model_in_the_catalog(self):
        models = [_m("a/paid", 1.0, 50.0), _m("a/free", 0.0, 40.0)]
        got, free = members(models, None)
        self.assertEqual(got, models)
        self.assertEqual(free, {"a/free"})


class RankingTest(unittest.TestCase):
    BENCH = "TB"

    def test_sorted_by_score_over_price_descending(self):
        # a: 80/2=40, b: 90/10=9, c: 40/1=40 → a before c by score on tie
        out = by_score_per_price(
            [_m("a", 2.0, 80.0), _m("b", 10.0, 90.0), _m("c", 1.0, 40.0)],
            self.BENCH,
        )
        self.assertEqual([m.model_id for m in out], ["a", "c", "b"])

    def test_free_models_rank_by_score(self):
        out = by_score_per_price(
            [_m("paid", 1.0, 50.0), _m("free", 0.0, 30.0), _m("free2", 0.0, 60.0)],
            self.BENCH,
        )
        # infinite ratio first, highest-scoring free model leads
        self.assertEqual([m.model_id for m in out], ["free2", "free", "paid"])

    def test_excludes_models_missing_price_or_score(self):
        out = by_score_per_price(
            [_m("ok", 1.0, 50.0), _m("np", None, 90.0), _m("ns", 1.0, None)],
            self.BENCH,
        )
        self.assertEqual([m.model_id for m in out], ["ok"])

    def test_output_price_axis_changes_ratio(self):
        # Both cost $2/M input; output differs: a 5, b 10 → a cheaper out
        models = [_m("a", 2.0, 80.0, output=5.0), _m("b", 2.0, 90.0, output=10.0)]
        out = by_score_per_price(models, self.BENCH, price_field="price_output")
        # a: 80/5=16, b: 90/10=9 → a leads despite lower score
        self.assertEqual([m.model_id for m in out], ["a", "b"])

    def test_unknown_price_field_raises(self):
        with self.assertRaises(ValueError):
            by_score_per_price(
                [_m("a", 1.0, 50.0)], self.BENCH, price_field="price_cache_write"
            )


if __name__ == "__main__":
    unittest.main()
