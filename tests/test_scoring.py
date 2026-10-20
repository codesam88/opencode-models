"""Scoring: every benchmark on its own 0–100 scale, median across them."""

import unittest

from opencode_models.data import Model
from opencode_models.scoring import (
    NEUTRAL,
    canonical,
    combined,
    derived_scores,
    normalise,
    scales,
    score_of,
    tracked,
)


def _model(mid, sources=None, **scores):
    """A model to score. *sources* maps benchmark name to its source URL."""
    return Model(
        model_id=mid,
        name=mid,
        lab=mid.split("/")[0],
        price_input=1.0,
        price_output=1.0,
        price_cached_input=1.0,
        scores=scores,
        sources=sources or {},
    )


class ScalesTest(unittest.TestCase):
    def test_each_benchmark_gets_its_own_floor_and_ceiling(self):
        models = [
            _model("a/one", **{"Terminal-Bench 2.1": 50.0}),
            _model("a/two", **{"Terminal-Bench 2.1": 90.0}),
            _model("a/three", **{"SWE-Bench Verified": 10.0}),
            _model("a/four", **{"SWE-Bench Verified": 30.0}),
            _model("a/five", **{"Terminal-Bench 2.1": 70.0}),
        ]
        table = scales(models)
        self.assertEqual(table["Terminal-Bench 2.1"], (50.0, 90.0))
        self.assertEqual(table["SWE-Bench Verified"], (10.0, 30.0))

    def test_a_benchmark_nobody_compares_against_still_spans_itself(self):
        models = [
            _model("a/one", **{"Terminal-Bench 4.0": 7.0}),
            _model("a/two", **{"Terminal-Bench 2.1": 1.0}),
        ]
        self.assertEqual(scales(models)["Terminal-Bench 4.0"], (7.0, 7.0))


#: A host on the official-board list, and one that is not.
BOARD = "https://www.tbench.ai/leaderboard/terminal-bench/4.0"
VENDOR = "https://example.invalid/blog/introducing"


class ProvenanceTest(unittest.TestCase):
    """The ends of a scale are set by numbers other labs can check."""

    def test_a_vendor_number_cannot_set_the_floor(self):
        # one uncited 1.5 would otherwise compress every other model's score
        models = [
            _model(
                "a/vendor",
                {"Terminal-Bench 4.0": VENDOR},
                **{"Terminal-Bench 4.0": 1.5},
            ),
            _model(
                "a/board", {"Terminal-Bench 4.0": BOARD}, **{"Terminal-Bench 4.0": 20.0}
            ),
            _model(
                "a/other", {"Terminal-Bench 4.0": BOARD}, **{"Terminal-Bench 4.0": 40.0}
            ),
        ]
        table = scales(models)
        self.assertEqual(table["Terminal-Bench 4.0"], (20.0, 40.0))
        # and the vendor's own number reads as the bottom of that range
        self.assertEqual(derived_scores(models[0], table), {"Terminal-Bench 4.0": 0.0})

    def test_an_uncited_number_cannot_set_the_floor_either(self):
        models = [
            _model("a/none", **{"Terminal-Bench 4.0": 2.0}),
            _model(
                "a/board", {"Terminal-Bench 4.0": BOARD}, **{"Terminal-Bench 4.0": 20.0}
            ),
            _model(
                "a/other", {"Terminal-Bench 4.0": BOARD}, **{"Terminal-Bench 4.0": 40.0}
            ),
        ]
        self.assertEqual(scales(models)["Terminal-Bench 4.0"], (20.0, 40.0))

    def test_a_thin_minority_of_board_numbers_does_not_set_the_ends(self):
        # a scale drawn from 8 of 50 numbers is not a scale, it is a
        # threshold: it would clamp nearly every model to 0 or 100, which
        # is how Terminal-Bench 2.1 was briefly reduced to a yes/no
        models = [
            _model(
                f"l/m{i:02d}",
                {"Terminal-Bench 2.1": VENDOR},
                **{"Terminal-Bench 2.1": float(i)},
            )
            for i in range(1, 9)
        ]
        models += [
            _model(
                f"b/m{i:02d}",
                {"Terminal-Bench 2.1": BOARD},
                **{"Terminal-Bench 2.1": 60.0 + i},
            )
            for i in range(2)
        ]
        table = scales(models)
        # 2 of 10 checkable is a minority, so the whole field sets the ends
        self.assertEqual(table["Terminal-Bench 2.1"], (1.0, 61.0))

    def test_enough_board_numbers_to_set_the_ends(self):
        models = [
            _model(
                "a/vendor",
                {"Terminal-Bench 4.0": VENDOR},
                **{"Terminal-Bench 4.0": 1.5},
            )
        ]
        models += [
            _model(
                f"b/m{i:02d}",
                {"Terminal-Bench 4.0": BOARD},
                **{"Terminal-Bench 4.0": 20.0 + i},
            )
            for i in range(5)
        ]
        models += [
            _model(
                f"l/m{i:02d}",
                {"Terminal-Bench 4.0": VENDOR},
                **{"Terminal-Bench 4.0": 55.0 + i},
            )
            for i in range(5)
        ]
        # 5 of 11 checkable clears the bar, and the uncited 1.5 is out
        self.assertEqual(scales(models)["Terminal-Bench 4.0"], (20.0, 24.0))

    def test_a_benchmark_with_nothing_but_vendor_numbers_keeps_its_ends(self):
        # the rule tightens a scale where it can and stands aside where it
        # cannot: otherwise those models would score on nothing at all
        models = [
            _model(
                "a/one", {"SWE-Bench Verified": VENDOR}, **{"SWE-Bench Verified": 10.0}
            ),
            _model(
                "a/two", {"SWE-Bench Verified": VENDOR}, **{"SWE-Bench Verified": 30.0}
            ),
        ]
        table = scales(models)
        self.assertEqual(table["SWE-Bench Verified"], (10.0, 30.0))
        # the ends stand aside where they cannot be tightened, so the model
        # is still measured against the field rather than against nothing
        self.assertEqual(derived_scores(models[0], table), {"SWE-Bench Verified": 0.0})

    def test_a_vendor_number_can_still_be_scored(self):
        # it is excluded from setting the ends, not from being measured
        models = [
            _model(
                "a/vendor",
                {"Terminal-Bench 2.1": VENDOR},
                **{"Terminal-Bench 2.1": 89.9},
            ),
            _model(
                "a/board", {"Terminal-Bench 2.1": BOARD}, **{"Terminal-Bench 2.1": 20.0}
            ),
            _model(
                "a/other", {"Terminal-Bench 2.1": BOARD}, **{"Terminal-Bench 2.1": 40.0}
            ),
        ]
        table = scales(models)
        self.assertEqual(table["Terminal-Bench 2.1"], (20.0, 40.0))
        self.assertEqual(
            derived_scores(models[0], table), {"Terminal-Bench 2.1": 100.0}
        )


class CoverageFloorTest(unittest.TestCase):
    """One benchmark is not a ranking."""

    def test_a_model_measured_on_one_benchmark_has_no_score(self):
        models = [
            _model("a/one", **{"Terminal-Bench 2.1": 90.0, "SWE-Bench Verified": 90.0}),
            _model("a/two", **{"Terminal-Bench 2.1": 50.0, "SWE-Bench Verified": 50.0}),
            _model("a/thin", **{"Terminal-Bench 2.1": 89.9}),
        ]
        table = scales(models)
        # a median of one number is that number, so a single strong result
        # would otherwise read as a strong model
        self.assertIsNone(score_of(models[2], table))
        self.assertEqual(len(derived_scores(models[2], table)), 1)

    def test_two_benchmarks_are_enough(self):
        models = [
            _model("a/one", **{"Terminal-Bench 2.1": 90.0, "SWE-Bench Verified": 90.0}),
            _model("a/two", **{"Terminal-Bench 2.1": 10.0, "SWE-Bench Verified": 20.0}),
        ]
        self.assertEqual(score_of(models[0], scales(models)), 100.0)


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
        self.assertEqual(combined({"a": 100.0, "b": 80.0, "c": 20.0, "d": 0.0}), 50.0)

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

    def test_a_single_benchmark_is_not_a_score(self):
        # the median of one number is that number, so a lone strong result
        # would read as a strong model
        self.assertIsNone(combined({"only": 42.0}))

    def test_two_benchmarks_already_are(self):
        self.assertEqual(combined({"a": 42.0, "b": 60.0}), 51.0)


#: Every benchmark name the tests mention, for walking the shortlist.
NAMES = (
    "Terminal-Bench 2.0",
    "Terminal-Bench 2.1",
    "Terminal-Bench 4.0",
    "Terminal Bench 2.1",
    "SWE-Bench Verified",
    "SWE-Bench Pro",
    "SWE Bench Pro",
    "SWE-Bench Lite",
    "SWE-Bench Multimodal",
    "SWE-Bench Multilingual",
    "Terminal-Bench 3.0",
    "Terminal-Bench Hard",
    "LMArena Elo",
    "SEC-Bench Pro",
    "DeepSWE 1.1",
    "DeepSWE",
)


class TrackedTest(unittest.TestCase):
    """Only the benchmarks the console ranks on are scored.

    Everything else the catalog mentions is context, not signal. A niche
    evaluation carried by four models gives one of them a hard 0 and drags
    the median down — "SEC-Bench Pro 17.7→0" was doing exactly that to a
    model that resolves 82 on SWE-bench Verified.
    """

    def test_the_axes_are_tracked(self):
        for name in (
            "Terminal-Bench 2.1",
            "Terminal-Bench 4.0",
            "Terminal Bench 2.1",
            "SWE-Bench Verified",
            "SWE-Bench Pro",
            "SWE Bench Pro",
        ):
            with self.subTest(benchmark=name):
                self.assertTrue(tracked(name))

    def test_a_benchmark_that_shares_the_prefix_is_not_tracked(self):
        # Terminal-Bench Hard and -Science are separate benchmarks, not runs
        for name in (
            "Terminal-Bench Hard",
            "Terminal-Bench-Science 0.1",
            "AutomationBench 1.0.6",
            "MiMo Code Bench",
            "SEC-Bench Pro",
            "Chartography",
            "GPQA Diamond",
        ):
            with self.subTest(benchmark=name):
                self.assertFalse(tracked(name))

    def test_an_older_revision_is_not_scored_on(self):
        # 2.0 is 2.1 with an easier, older and public task set, and a public
        # task set gets trained against. It costs no coverage to leave out:
        # every tier model whose only Terminal-Bench run is a 2.0 carries
        # SWE-bench as well.
        for name in ("Terminal-Bench 2.0", "Terminal Bench 2.0"):
            with self.subTest(benchmark=name):
                self.assertFalse(tracked(name))

    def test_a_benchmark_that_merely_adds_an_axis_is_left_out(self):
        # Each of these is a distinct task set that would widen the scoring
        # equation without adding a model we could not otherwise score:
        # Lite and Multimodal reach no tier model at all, Multilingual is
        # carried only alongside Verified or Pro, and 3.0 has two models.
        for name in (
            "SWE-Bench Lite",
            "SWE-Bench Multimodal",
            "SWE-Bench Multilingual",
            "Terminal-Bench 3.0",
            "LMArena Elo",
        ):
            with self.subTest(benchmark=name):
                self.assertFalse(tracked(name))

    def test_the_shortlist_is_the_five_benchmarks_the_console_orders_on(self):
        self.assertEqual(
            sorted({canonical(n) for n in NAMES if tracked(n)}),
            [
                "DeepSWE 1.1",
                "SWE Bench Pro",
                "SWE Bench Verified",
                "Terminal Bench 2.1",
                "Terminal Bench 4.0",
            ],
        )

    def test_the_older_deepswe_revision_is_not_scored_on(self):
        # "DeepSWE" and "DeepSWE 1.1" share no models in the catalog: they
        # are different revisions, and only 1.1 is broadly carried
        self.assertFalse(tracked("DeepSWE"))

    def test_an_untracked_benchmark_cannot_drag_the_median(self):
        models = [
            _model("a/one", **{"Terminal-Bench 2.1": 90.0, "SEC-Bench Pro": 17.7}),
            _model("a/two", **{"Terminal-Bench 2.1": 50.0, "SEC-Bench Pro": 90.0}),
        ]
        table = scales(models)
        derived = derived_scores(models[0], table)
        self.assertEqual(derived, {"Terminal-Bench 2.1": 100.0})

    def test_a_model_scored_only_on_untracked_benchmarks_has_no_score(self):
        models = [
            _model("a/one", **{"SEC-Bench Pro": 17.7}),
            _model("a/two", **{"SEC-Bench Pro": 90.0}),
        ]
        self.assertIsNone(score_of(models[0], scales(models)))


class CanonicalTest(unittest.TestCase):
    """Two labs spell the same benchmark differently; it is one column."""

    def test_the_dash_and_space_spellings_are_one_benchmark(self):
        self.assertEqual(
            canonical("Terminal-Bench 2.1"), canonical("Terminal Bench 2.1")
        )
        self.assertEqual(canonical("SWE-Bench Pro"), canonical("SWE Bench Pro"))

    def test_a_benchmark_that_merely_shares_the_prefix_stays_apart(self):
        # folding the dashes must not turn -Science into a Terminal-Bench run
        self.assertNotEqual(
            canonical("Terminal-Bench 2.1"), canonical("Terminal-Bench-Science 0.1")
        )

    def test_the_two_spellings_are_scaled_against_each_other(self):
        # One benchmark means one scale: a model measured by one registry
        # and a model measured by another are not each the worst of a pair
        models = [
            _model("a/one", **{"Terminal-Bench 2.1": 90.0}),
            _model("a/two", **{"Terminal Bench 2.1": 10.0}),
        ]
        table = scales(models)
        self.assertEqual(
            derived_scores(models[0], table), {"Terminal-Bench 2.1": 100.0}
        )
        self.assertEqual(derived_scores(models[1], table), {"Terminal Bench 2.1": 0.0})

    def test_an_untracked_name_is_left_exactly_as_it_is(self):
        self.assertEqual(canonical("SEC-Bench Pro"), "SEC-Bench Pro")


class ScoreOfTest(unittest.TestCase):
    def test_scales_are_not_mixed_across_benchmarks(self):
        # a 4.0-scale 65 (the best 4.0 run) and a 2.1-scale 90 (the worst
        # 2.1 run) must not be compared as 65 and 90
        models = [
            _model(
                "a/low2", **{"Terminal-Bench 2.1": 25.0, "Terminal-Bench 4.0": 65.0}
            ),
            _model(
                "a/high2", **{"Terminal-Bench 2.1": 90.0, "Terminal-Bench 4.0": 60.0}
            ),
        ]
        table = scales(models)
        low2 = score_of(models[0], table)
        high2 = score_of(models[1], table)
        self.assertEqual(low2, 50.0)  # best on 4.0, worst on 2.1
        self.assertEqual(high2, 50.0)  # the mirror image

    def test_the_best_model_on_everything_leads(self):
        models = [
            _model(
                "a/strong", **{"Terminal-Bench 2.1": 90.0, "SWE-Bench Verified": 90.0}
            ),
            _model(
                "a/mixed", **{"Terminal-Bench 2.1": 20.0, "SWE-Bench Verified": 95.0}
            ),
            _model(
                "a/weak", **{"Terminal-Bench 2.1": 10.0, "SWE-Bench Verified": 10.0}
            ),
        ]
        table = scales(models)
        # no model is best on both scales, so this is about the order, not
        # an absolute: strong everywhere, then lopsided, then weak
        strong, mixed, weak = (score_of(m, table) for m in models)
        self.assertGreater(strong, mixed)
        self.assertGreater(mixed, weak)

    def test_derived_scores_are_reported_per_benchmark(self):
        models = [
            _model("a/one", **{"Terminal-Bench 2.1": 50.0}),
            _model("a/two", **{"Terminal-Bench 2.1": 90.0}),
        ]
        table = scales(models)
        self.assertEqual(derived_scores(models[0], table), {"Terminal-Bench 2.1": 0.0})
        self.assertEqual(
            derived_scores(models[1], table), {"Terminal-Bench 2.1": 100.0}
        )

    def test_a_benchmark_with_no_spread_is_left_out(self):
        # only this model carries "Terminal-Bench 4.0", so it cannot say
        # anything about it — counting it at the midpoint would drag the
        # median of a model that has no say in the benchmark
        models = [
            _model("a/one", **{"Terminal-Bench 2.1": 50.0, "Terminal-Bench 4.0": 1.0}),
            _model("a/two", **{"Terminal-Bench 2.1": 90.0}),
        ]
        table = scales(models)
        self.assertEqual(table["Terminal-Bench 4.0"], (1.0, 1.0))
        self.assertEqual(derived_scores(models[0], table), {"Terminal-Bench 2.1": 0.0})


if __name__ == "__main__":
    unittest.main()
