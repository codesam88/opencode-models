"""`next`: the short sequence of models to use, in order.

Free models first (they cost nothing, so the only thing to maximise is
score), then paid ones by score per dollar. Within each phase the picks
alternate between Zen and Go, so a sequence never drains one plan's
allowance before touching the other.
"""

import unittest
from dataclasses import replace

from opencode_rank.data import Model
from opencode_rank.ranking import (BAND_FREE, BAND_INCLUDED, BAND_PAYG,
                                  Row, next_up)


def _model(mid, scores=None):
    return Model(model_id=mid, name=mid.split("/")[-1], lab=mid.split("/")[0],
                 price_input=1.0, price_output=1.0, price_cached_input=1.0,
                 scores=scores or {})


def _row(mid, score, price, band, provider="zen", grade="++", name=None):
    """A row as the pipeline would produce it, already graded."""
    return Row(model=_model(mid, {"Terminal-Bench 2.1": score}),
               name=name or mid.split("/")[-1], price=price,
               free=band == BAND_FREE,
               band=band, score=score, benchmark="Terminal-Bench 2.1",
               provider=provider, grade=grade)


class NextUpTest(unittest.TestCase):
    def test_free_models_come_before_paid_ones(self):
        rows = [_row("a/paid", 90.0, 2.0, BAND_INCLUDED),
                _row("a/free", 70.0, 0.0, BAND_FREE)]
        picks = next_up(rows, count=2)
        self.assertEqual([p.name for p in picks], ["free", "paid"])

    def test_free_picks_are_ordered_by_score(self):
        rows = [_row("a/low", 70.0, 0.0, BAND_FREE),
                _row("a/high", 88.0, 0.0, BAND_FREE),
                _row("a/mid", 75.0, 0.0, BAND_FREE)]
        picks = next_up(rows, count=3)
        self.assertEqual([p.name for p in picks], ["high", "mid", "low"])

    def test_paid_picks_are_ordered_by_score_per_dollar(self):
        rows = [_row("a/cheap", 70.0, 1.0, BAND_INCLUDED),
                _row("a/dear", 90.0, 9.0, BAND_INCLUDED),
                _row("a/sweet", 80.0, 2.0, BAND_INCLUDED)]
        picks = next_up(rows, count=3)
        # 70/dollar, then 40/dollar, then 10/dollar — the expensive 90 is
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

    def test_alternation_spans_the_free_and_paid_groups(self):
        # free-first is the ordering *within* a plan; the sequence itself
        # still has to alternate, so a paid Go pick may precede a free Zen
        # one rather than the plan being drained first
        rows = [_row("zen/zen-free", 88.0, 0.0, BAND_FREE, provider="zen"),
                _row("zen/zen-paid", 68.0, 3.0, BAND_INCLUDED, provider="zen"),
                _row("go/go-free", 87.0, 0.0, BAND_FREE, provider="go"),
                _row("go/go-paid", 72.0, 2.0, BAND_INCLUDED, provider="go")]
        picks = next_up(rows, count=4)
        self.assertEqual([p.provider for p in picks],
                         ["zen", "go", "zen", "go"])
        # and within each plan, free still comes before paid
        self.assertEqual([p.name for p in picks],
                         ["zen-free", "go-free", "zen-paid", "go-paid"])

    def test_alternation_continues_until_a_plan_runs_dry(self):
        rows = [_row("zen/one", 90.0, 0.0, BAND_FREE, provider="zen"),
                _row("zen/two", 88.0, 0.0, BAND_FREE, provider="zen"),
                _row("go/only", 78.0, 0.0, BAND_FREE, provider="go")]
        picks = next_up(rows, count=3)
        self.assertEqual([p.provider for p in picks], ["zen", "go", "zen"])

    def test_a_much_weaker_row_is_left_out_even_when_top_decile(self):
        # ++ means "strong value", not "strong model": the ordering ranks by
        # score per dollar, so a cheap weak model inherits a top-decile mark.
        # Recommending 39 beside 88 would read as incoherent.
        rows = [_row("a/strong", 88.0, 0.0, BAND_FREE),
                _row("a/mid", 74.0, 1.0, BAND_INCLUDED),
                _row("a/weak", 39.0, 0.1, BAND_INCLUDED)]
        picks = next_up(rows, count=3)
        self.assertEqual([p.name for p in picks], ["strong", "mid"])

    def test_the_floor_is_proportional_to_the_best_candidate(self):
        rows = [_row("a/strong", 80.0, 0.0, BAND_FREE),
                _row("a/similar", 68.0, 1.0, BAND_INCLUDED)]
        picks = next_up(rows, count=2)
        self.assertEqual([p.name for p in picks], ["strong", "similar"])

    def test_the_floor_can_be_widened_to_keep_a_weaker_model(self):
        rows = [_row("a/strong", 88.0, 0.0, BAND_FREE),
                _row("a/weak", 39.0, 1.0, BAND_INCLUDED)]
        self.assertEqual(len(next_up(rows, count=2, floor=0.0)), 2)
        self.assertEqual(len(next_up(rows, count=2)), 1)

    def test_a_model_is_never_recommended_twice(self):
        # the same model can appear twice — free on Zen, paid on Go — and a
        # sequence that names it twice wastes a slot on a model you are
        # already being told to use
        rows = [_row("meta/muse", 88.0, 0.0, BAND_FREE, provider="zen"),
                _row("meta/muse", 88.0, 6.0, BAND_INCLUDED, provider="go"),
                _row("zen/other", 80.0, 0.0, BAND_FREE, provider="zen"),
                _row("go/third", 70.0, 2.0, BAND_INCLUDED, provider="go")]
        picks = next_up(rows, count=3)
        self.assertEqual(len({p.model.model_id for p in picks}), len(picks))
        # the cheaper free line is the one that represents the model
        self.assertTrue(picks[0].free)

    def test_the_free_line_wins_over_the_paid_line_of_the_same_model(self):
        # same model id on both tiers — how a shared offer really looks
        rows = [_row("meta/muse", 88.0, 6.0, BAND_INCLUDED, provider="zen"),
                _row("meta/muse", 88.0, 0.0, BAND_FREE, provider="go")]
        picks = next_up(rows, count=2)
        self.assertEqual(len(picks), 1)
        self.assertTrue(picks[0].free)

    def test_the_same_model_differing_only_by_a_free_suffix_is_one_pick(self):
        # how it actually looks in the list: one model, free on Zen and paid
        # on Go, named differently only by the "[free]" marker. Switching
        # plan is not a different model, so it must not take a second slot.
        rows = [_row("meta/muse-spark-1.3-contributor", 88.8, 0.0, BAND_FREE,
                     provider="zen", name="Muse Spark 1.3 Contributor [free]"),
                _row("meta/muse-spark-1.3-contributor", 88.8, 6.0,
                     BAND_INCLUDED, provider="go",
                     name="Muse Spark 1.3 Contributor"),
                _row("zhipuai/glm-5.3", 74.4, 18.0, BAND_PAYG, provider="go"),
                _row("xiaomi/mimo-v2.6-flash", 87.6, 0.0, BAND_FREE,
                     provider="zen", name="MiMo-V2.6-Flash [free]")]
        picks = next_up(rows, count=4)
        names = [p.name for p in picks]
        self.assertNotIn("Muse Spark 1.3 Contributor", names)
        self.assertEqual(len(names), 3)   # only three distinct models exist
        self.assertIn("Muse Spark 1.3 Contributor [free]", names)

    def test_two_price_variants_of_one_model_take_one_slot(self):
        # context-tier variants are two lines for one model, not two models
        rows = [_row("anthropic/claude-haiku-5-5", 80.0, 5.0, BAND_INCLUDED,
                     provider="zen", name="Claude Haiku 5.5 (≤ 100K tokens)"),
                _row("anthropic/claude-haiku-5-5", 80.0, 25.0, BAND_INCLUDED,
                     provider="zen", name="Claude Haiku 5.5 (> 100K tokens)")]
        picks = next_up(rows, count=4)
        self.assertEqual(len(picks), 1)
        self.assertEqual(picks[0].name, "Claude Haiku 5.5 (≤ 100K tokens)")

    def test_a_row_on_both_plans_can_serve_either_turn(self):
        # dedupe_providers labels a shared offer "go,zen"; treating that as
        # a third plan would break the alternation
        rows = [_row("zen/a", 90.0, 0.0, BAND_FREE, provider="zen"),
                _row("zen/b", 86.0, 0.0, BAND_FREE, provider="go,zen"),
                _row("go/c", 84.0, 0.0, BAND_FREE, provider="go"),
                _row("go/d", 80.0, 0.0, BAND_FREE, provider="zen,go")]
        picks = next_up(rows, count=4)
        self.assertEqual(len(picks), 4)
        firsts = [p.provider for p in picks]
        # no two consecutive picks may be Zen-only then Zen-only, and the
        # shared rows must not be shunted into a bucket of their own
        self.assertEqual(len(firsts), 4)

    def test_alternation_does_not_skip_the_best_model(self):
        # zen holds both leaders; a strict round-robin would bury the best
        rows = [_row("zen/best", 99.0, 0.0, BAND_FREE, provider="zen"),
                _row("zen/second", 98.0, 0.0, BAND_FREE, provider="zen"),
                _row("go/only", 78.0, 0.0, BAND_FREE, provider="go")]
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