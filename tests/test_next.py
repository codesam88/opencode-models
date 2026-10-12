"""`next`: the short sequence of models to use, in order.

Free models first (they cost nothing, so the only thing to maximise is
score), then paid ones by score per dollar. Within each phase the picks
alternate between Zen and Go, so a sequence never drains one plan's
allowance before touching the other.
"""

import unittest
from dataclasses import replace

from opencode_rank.data import Model
from opencode_rank.ranking import BAND_FREE, BAND_INCLUDED, Row, next_up


def _model(mid, scores=None):
    return Model(model_id=mid, name=mid.split("/")[-1], lab=mid.split("/")[0],
                 price_input=1.0, price_output=1.0, price_cached_input=1.0,
                 scores=scores or {})


def _row(mid, score, price, band, provider="zen", grade="++"):
    """A row as the pipeline would produce it, already graded."""
    return Row(model=_model(mid, {"Terminal-Bench 2.1": score}),
               name=mid.split("/")[-1], price=price, free=band == BAND_FREE,
               band=band, score=score, benchmark="Terminal-Bench 2.1",
               provider=provider, grade=grade)


class NextUpTest(unittest.TestCase):
    def test_free_models_come_before_paid_ones(self):
        rows = [_row("a/paid", 90.0, 2.0, BAND_INCLUDED),
                _row("a/free", 70.0, 0.0, BAND_FREE)]
        picks = next_up(rows, count=2)
        self.assertEqual([p.name for p in picks], ["free", "paid"])

    def test_free_picks_are_ordered_by_score(self):
        rows = [_row("a/low", 60.0, 0.0, BAND_FREE),
                _row("a/high", 88.0, 0.0, BAND_FREE),
                _row("a/mid", 75.0, 0.0, BAND_FREE)]
        picks = next_up(rows, count=3)
        self.assertEqual([p.name for p in picks], ["high", "mid", "low"])

    def test_paid_picks_are_ordered_by_score_per_dollar(self):
        rows = [_row("a/cheap", 60.0, 1.0, BAND_INCLUDED),
                _row("a/dear", 90.0, 9.0, BAND_INCLUDED),
                _row("a/sweet", 80.0, 2.0, BAND_INCLUDED)]
        picks = next_up(rows, count=3)
        # 60/dollar, then 40/dollar, then 10/dollar — the expensive 90 is
        # the worst value despite the highest raw score
        self.assertEqual([p.name for p in picks], ["cheap", "sweet", "dear"])

    def test_only_top_decile_rows_are_eligible(self):
        rows = [_row("a/best", 99.0, 0.0, BAND_FREE, grade="++"),
                _row("a/near", 95.0, 0.0, BAND_FREE, grade="+"),
                _row("a/worst", 10.0, 0.0, BAND_FREE, grade="-")]
        picks = next_up(rows, count=3)
        self.assertEqual([p.name for p in picks], ["best"])

    def test_plans_alternate_when_both_offer_candidates(self):
        rows = [_row("zen/zen-best", 90.0, 0.0, BAND_FREE, provider="zen"),
                _row("zen/zen-next", 88.0, 0.0, BAND_FREE, provider="zen"),
                _row("go/go-best", 92.0, 0.0, BAND_FREE, provider="go"),
                _row("go/go-next", 86.0, 0.0, BAND_FREE, provider="go")]
        picks = next_up(rows, count=4)
        # the strongest candidate leads, and the plans then alternate
        self.assertEqual([p.name for p in picks],
                         ["go-best", "zen-best", "go-next", "zen-next"])
        self.assertEqual([p.provider for p in picks],
                         ["go", "zen", "go", "zen"])

    def test_alternation_does_not_skip_the_best_model(self):
        # zen holds both leaders; a strict round-robin would bury the best
        rows = [_row("zen/best", 99.0, 0.0, BAND_FREE, provider="zen"),
                _row("zen/second", 98.0, 0.0, BAND_FREE, provider="zen"),
                _row("go/only", 50.0, 0.0, BAND_FREE, provider="go")]
        picks = next_up(rows, count=3)
        self.assertEqual([p.name for p in picks], ["best", "only", "second"])

    def test_fewer_candidates_than_requested_is_not_an_error(self):
        rows = [_row("a/one", 90.0, 0.0, BAND_FREE)]
        self.assertEqual(len(next_up(rows, count=4)), 1)

    def test_no_eligible_rows_yields_nothing(self):
        self.assertEqual(next_up([_row("a/x", 50.0, 1.0, BAND_INCLUDED,
                                       grade="o")], count=4), [])

    def test_a_row_with_no_score_is_never_picked(self):
        rows = [replace(_row("a/none", 0.0, 0.0, BAND_FREE), score=None),
                _row("a/real", 50.0, 0.0, BAND_FREE)]
        picks = next_up(rows, count=4)
        self.assertEqual([p.name for p in picks], ["real"])


if __name__ == "__main__":
    unittest.main()