"""Zen/Go tier membership lists and score/price ranking for text output."""

import unittest

from modelpareto.ranking import by_score_per_price
from modelpareto.sources import available_sources, load_source
from modelpareto.data import Model


def _m(model_id, price, score):
    return Model(
        model_id=model_id,
        name=model_id,
        lab="x",
        price_input=price,
        price_output=None,
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
            [_m("paid", 1.0, 50.0), _m("free", 0.0, 30.0),
             _m("free2", 0.0, 60.0)],
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


if __name__ == "__main__":
    unittest.main()
