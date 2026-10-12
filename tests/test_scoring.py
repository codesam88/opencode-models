"""Scoring: every benchmark on its own 0–100 scale, median across them."""

import unittest

from opencode_rank.data import Model
from opencode_rank.scoring import (
    NEUTRAL,
    combined,
    derived_scores,
    normalise,
    scales,
    tracked,
    score_of,
)


def _model(mid, **scores):
    return Model(model_id=mid, name=mid, lab=mid.split("/")[0],
                 price_input=1.0, price_output=1.0, price_cached_input=1.0,
                 scores=scores)


class ScalesTest(unittest.TestCase):
    def test_each_benchmark_gets_its_own_floor_and_ceiling(self):
        models = [_model("a/one", **{"Terminal-Bench 2.1": 50.0}),
                  _model("a/two", **{"Terminal-Bench 2.1": 90.0}),
                  _model("a/three", **{"SWE-Bench Verified": 10.0}),
                  _model("a/four", **{"SWE-Bench Verified": 30.0}),
                  _model("a/five", **{"Terminal-Bench 2.1": 70.0})]
        table = scales(models)
        self.assertEqual(table["Terminal-Bench 2.1"], (50.0, 90.0))
        self.assertEqual(table["SWE-Bench Verified"], (10.0, 30.0))

    def test_a_benchmark_nobody_compares_against_still_spans_itself(self):
        models = [_model("a/one", **{"Terminal-Bench 2.0": 7.0}), _model("a/two", **{"SWE-Bench Lite": 1.0})]
        self.assertEqual(scales(models)["Terminal-Bench 2.0"], (7.0, 7.0))


class NormaliseTest(unittest.TestCase):
    def test_the_best_model_is_a_hundred(self):
        self.assertEqual(normalise(90.0, (50.0, 90.0)), 100.0)

    def test_the_worst_model_is_zero(self):
        self.assertEqual(normalise(50.0, (50.0, 90.0)), 0.0)

    def test_the_distance_is_proportionate_and_linear(self):
        # halfway up the range is halfway up the scale
        self.assertEqual(normalise(70.0, (50.0, 90.0)), 50.0)
        self.assertEqual(normalise(60.0, (50.0, 90.0)), 25.0)
        self.assertEqual(normalise(80.0, (50.0, 90.0)), 75.0)

    def test_a_score_outside_the_range_clamps(self):
        self.assertEqual(normalise(120.0, (50.0, 90.0)), 100.0)
        self.assertEqual(normalise(10.0, (50.0, 90.0)), 0.0)

    def test_a_benchmark_everyone_scored_the_same_on_is_neutral(self):
        # no discrimination is possible, so the midpoint, not the top
        self.assertEqual(normalise(42.0, (42.0, 42.0)), NEUTRAL)


class CombinedTest(unittest.TestCase):
    def test_the_score_is_the_median_of_the_derived_scores(self):
        self.assertEqual(combined({"a": 100.0, "b": 50.0, "c": 0.0}), 50.0)

    def test_an_even_number_of_scores_averages_the_middle_two(self):
        self.assertEqual(combined({"a": 100.0, "b": 80.0, "c": 20.0, "d": 0.0}),
                         50.0)

    def test_one_loud_score_cannot_bury_a_consistently_strong_model(self):
        weak = {"a": 100.0, "b": 0.0, "c": 0.0, "d": 0.0, "e": 0.0}
        strong = {"a": 80.0, "b": 80.0, "c": 80.0, "d": 80.0, "e": 80.0}
        self.assertGreater(combined(strong), combined(weak))

    def test_one_bad_result_does_not_outweigh_a_consistently_strong_model(self):
        # the mean gets this backwards: 100, 100, 0 averages below 70
        lopsided = {"a": 100.0, "b": 100.0, "c": 0.0}
        steady = {"a": 70.0, "b": 70.0, "c": 70.0}
        self.assertGreater(combined(lopsided), combined(steady))
        self.assertLess(sum(lopsided.values()) / 3, sum(steady.values()) / 3)

    def test_a_model_with_no_benchmarks_has_no_score(self):
        self.assertIsNone(combined({}))

    def test_a_single_benchmark_is_its_own_score(self):
        self.assertEqual(combined({"only": 42.0}), 42.0)


class TrackedTest(unittest.TestCase):
    """Only the benchmarks the console ranks on are scored.

    Everything else the catalog mentions is context, not signal. A niche
    evaluation carried by four models gives one of them a hard 0 and drags
    the median down — "SEC-Bench Pro 17.7→0" was doing exactly that to a
    model that resolves 82 on SWE-bench Verified.
    """

    def test_the_axes_are_tracked(self):
        for name in ("Terminal-Bench 2.1", "Terminal-Bench 2.0",
                     "Terminal-Bench 4.0", "Terminal Bench 2.1",
                     "SWE-Bench Verified", "SWE-Bench Multilingual",
                     "SWE-Bench Pro", "SWE Bench Pro", "LMArena Elo"):
            with self.subTest(benchmark=name):
                self.assertTrue(tracked(name))

    def test_a_benchmark_that_shares_the_prefix_is_not_tracked(self):
        # Terminal-Bench Hard and -Science are separate benchmarks, not runs
        for name in ("Terminal-Bench Hard", "Terminal-Bench-Science 0.1",
                     "AutomationBench 1.0.6", "MiMo Code Bench",
                     "SEC-Bench Pro", "Chartography", "GPQA Diamond"):
            with self.subTest(benchmark=name):
                self.assertFalse(tracked(name))

    def test_an_untracked_benchmark_cannot_drag_the_median(self):
        models = [_model("a/one", **{"Terminal-Bench 2.1": 90.0,
                                     "SEC-Bench Pro": 17.7}),
                  _model("a/two", **{"Terminal-Bench 2.1": 50.0,
                                     "SEC-Bench Pro": 90.0})]
        table = scales(models)
        derived = derived_scores(models[0], table)
        self.assertEqual(derived, {"Terminal-Bench 2.1": 100.0})
        self.assertEqual(score_of(models[0], table), 100.0)

    def test_a_model_scored_only_on_untracked_benchmarks_has_no_score(self):
        models = [_model("a/one", **{"SEC-Bench Pro": 17.7}),
                  _model("a/two", **{"SEC-Bench Pro": 90.0})]
        self.assertIsNone(score_of(models[0], scales(models)))


class ScoreOfTest(unittest.TestCase):
    def test_scales_are_not_mixed_across_benchmarks(self):
        # a 4.0-scale 65 (the best 4.0 run) and a 2.1-scale 90 (the worst
        # 2.1 run) must not be compared as 65 and 90
        models = [_model("a/low2", **{"Terminal-Bench 2.1": 25.0, "Terminal-Bench 4.0": 65.0}),
                  _model("a/high2", **{"Terminal-Bench 2.1": 90.0, "Terminal-Bench 4.0": 60.0})]
        table = scales(models)
        low2 = score_of(models[0], table)
        high2 = score_of(models[1], table)
        self.assertEqual(low2, 50.0)      # best on 4.0, worst on 2.1
        self.assertEqual(high2, 50.0)     # the mirror image

    def test_the_best_model_on_everything_leads(self):
        models = [_model("a/strong", **{"Terminal-Bench 2.1": 90.0, "SWE-Bench Verified": 90.0}),
                  _model("a/mixed", **{"Terminal-Bench 2.1": 20.0, "SWE-Bench Verified": 95.0}),
                  _model("a/weak", **{"Terminal-Bench 2.1": 10.0, "SWE-Bench Verified": 10.0})]
        table = scales(models)
        # no model is best on both scales, so this is about the order, not
        # an absolute: strong everywhere, then lopsided, then weak
        strong, mixed, weak = (score_of(m, table) for m in models)
        self.assertGreater(strong, mixed)
        self.assertGreater(mixed, weak)

    def test_derived_scores_are_reported_per_benchmark(self):
        models = [_model("a/one", **{"Terminal-Bench 2.1": 50.0}),
                  _model("a/two", **{"Terminal-Bench 2.1": 90.0})]
        table = scales(models)
        self.assertEqual(derived_scores(models[0], table), {"Terminal-Bench 2.1": 0.0})
        self.assertEqual(derived_scores(models[1], table), {"Terminal-Bench 2.1": 100.0})

    def test_a_benchmark_with_no_spread_is_left_out(self):
        # only this model carries "Terminal-Bench 2.0", so it cannot say anything
        # about it — counting it at the midpoint would drag the median
        models = [_model("a/one", **{"Terminal-Bench 2.1": 50.0, "Terminal-Bench 2.0": 1.0}),
                  _model("a/two", **{"Terminal-Bench 2.1": 90.0})]
        table = scales(models)
        self.assertEqual(table["Terminal-Bench 2.0"], (1.0, 1.0))
        self.assertEqual(derived_scores(models[0], table), {"Terminal-Bench 2.1": 0.0})


if __name__ == "__main__":
    unittest.main()