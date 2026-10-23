"""Console list: pricing rows, the combined-benchmark order, the cull split."""

import unittest

from opencode_models.models import Model
from opencode_models.pricing import PriceEntry
from opencode_models.ranking import (
    Row,
    axis_scores,
    build_rows,
    by_score_per_price,
    dedupe_providers,
    grade_for,
    grade_rows,
    merge_rows,
    order_rows,
    partition_rows,
)

TB = "Terminal-Bench 2.1"
TB4 = "Terminal-Bench 4.0"
SWE = "SWE-Bench Pro"


def _m(
    model_id,
    price,
    tb=None,
    swe=None,
    output=None,
    cached=None,
    name=None,
    tb40=None,
    scores=None,
    alone=False,
):
    """A model fixture. *alone* keeps it to the one benchmark asked for.

    Unless *alone*, a Terminal-Bench score is paired with the same number on
    SWE-bench: a model measured on one benchmark carries no score at all
    (scoring.MIN_AXES), and these tests are about ordering and grading
    rather than about how much evidence a model has.
    """
    scores = dict(scores or {})
    if tb is not None:
        scores[TB] = tb
    if swe is not None:
        scores[SWE] = swe
    elif tb is not None and not alone:
        scores[SWE] = tb
    if tb40 is not None:
        scores[TB4] = tb40
    lab = model_id.split("/")[0]
    return Model(
        model_id=model_id,
        name=name or model_id.split("/")[-1].upper(),
        lab=lab,
        price_input=price,
        price_output=output,
        price_cached_input=cached,
        scores=scores,
    )


def _entry(
    model_id, inp, out, cached=None, limit=None, free=False, name=None, tier="go"
):
    return PriceEntry(
        tier=tier,
        model_id=model_id,
        name=name or model_id,
        input=inp,
        output=out,
        cached_read=cached,
        limit=limit,
        free=free,
    )


class RowBuildingTest(unittest.TestCase):
    def test_documented_entry_replaces_the_catalog_price(self):
        # catalog says $1.74 (the Zen rate); Go documents $0.66 off-peak
        models = [_m("lab/deepseek-v4-pro", 1.74, tb=50.0)]
        entries = [
            _entry(
                "deepseek-v4-pro",
                0.66,
                1.98,
                cached=0.022,
                limit=15.0,
                name="DeepSeek V4 Pro (Off-Peak)",
            )
        ]
        rows = build_rows(models, entries)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].price, 0.2 * 0.66 + 0.6 * 0.022 + 0.2 * 1.98)
        self.assertEqual(rows[0].band, "included")  # Go grants $15/month
        self.assertIn("Off-Peak", rows[0].name)

    def test_a_free_line_is_priced_at_zero(self):
        # free is metered at $0 (the throttle lives elsewhere), so every
        # free line carries price 0 whatever its traffic profile
        models = [_m("lab/paid", 1.0, tb=50.0), _m("lab/gift", 5.0, tb=40.0)]
        entries = [
            _entry("paid", 1.0, 1.0, cached=1.0),  # costs 1.0
            _entry("gift", 0.0, 0.0, cached=0.0, free=True),  # free tier
        ]
        rows = {r.model.model_id: r for r in build_rows(models, entries)}
        self.assertEqual(rows["lab/gift"].price, 0.0)
        self.assertEqual(rows["lab/gift"].band, "free")
        self.assertTrue(rows["lab/gift"].name.endswith("[free]"))
        self.assertEqual(rows["lab/paid"].price, 1.0)
        self.assertEqual(rows["lab/paid"].band, "payg")

    def test_free_lines_price_zero_whatever_their_traffic(self):
        # Step-5-Free burns ~70x what Muse-Spark-Contributor-Free does per
        # request, but both are metered at $0 — the throttle is not the
        # dollar math, so both lines cost the same nothing
        models = [
            _m("stepfun/step-5-preview", 0.959, tb=50.0, swe=30.0),
            _m("meta/muse-spark-1.3-contributor", 0.1, tb=30.0, swe=50.0),
        ]
        entries = [
            _entry("step-5-preview-free", 0.0, 0.0, free=True),
            _entry("muse-spark-1.3-contributor-free", 0.0, 0.0, free=True),
        ]
        rows = {r.model.model_id: r for r in build_rows(models, entries)}
        self.assertEqual(rows["stepfun/step-5-preview"].price, 0.0)
        self.assertEqual(rows["meta/muse-spark-1.3-contributor"].price, 0.0)

    def test_free_tag_is_not_added_when_the_name_already_says_free(self):
        models = [_m("lab/ling", 1.0, tb=40.0, name="Ling 3.1 Flash Free")]
        entries = [_entry("ling", 0.0, 0.0, free=True)]
        row = build_rows(models, entries)[0]
        self.assertEqual(row.name, "Ling 3.1 Flash Free")

    def test_model_without_documented_price_uses_the_catalog_cost(self):
        models = [_m("a/m1", 2.0, output=4.0, tb=50.0)]  # cached unknown
        rows = build_rows(models, [])
        # no cache discount published → cached reads bill at the input rate
        self.assertAlmostEqual(rows[0].price, 0.2 * 2.0 + 0.6 * 2.0 + 0.2 * 4.0)
        self.assertEqual(rows[0].band, "payg")

    def test_axis_picks_a_single_rate_even_when_a_line_is_documented(self):
        models = [_m("l/ds", 9.9, tb=50.0)]
        entries = [_entry("ds", 1.0, 2.0, cached=0.5, limit=15.0)]
        self.assertEqual(build_rows(models, entries, axis="input")[0].price, 1.0)
        self.assertEqual(build_rows(models, entries, axis="output")[0].price, 2.0)
        self.assertAlmostEqual(
            build_rows(models, entries)[0].price, 0.2 * 1.0 + 0.6 * 0.5 + 0.2 * 2.0
        )

    def test_the_same_variant_from_a_second_tier_does_not_double_the_line(self):
        # `list --source all` hands over Go's and Zen's tables together
        models = [_m("l/ds", 1.74, tb=50.0)]
        entries = [
            _entry(
                "ds",
                0.66,
                1.98,
                cached=0.022,
                limit=15.0,
                name="DS (Off-Peak)",
                tier="go",
            ),
            _entry("ds", 1.74, 3.0, cached=0.04, name="DS (Off-Peak)", tier="zen"),
        ]
        rows = build_rows(models, entries)
        self.assertEqual(len(rows), 1)  # one line, not one per tier
        self.assertAlmostEqual(rows[0].price, 0.2 * 0.66 + 0.6 * 0.022 + 0.2 * 1.98)

    def test_each_variant_is_its_own_line_at_its_own_price(self):
        models = [_m("lab/deepseek-v4-pro", 1.74, tb=50.0)]
        entries = [
            _entry(
                "deepseek-v4-pro",
                0.66,
                1.98,
                cached=0.022,
                limit=15.0,
                name="DeepSeek V4 Pro (Off-Peak)",
            ),
            _entry(
                "deepseek-v4-pro",
                1.32,
                3.96,
                cached=0.044,
                limit=15.0,
                name="DeepSeek V4 Pro (Peak)",
            ),
        ]
        rows = build_rows(models, entries)
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[0].price, rows[1].price)
        self.assertIn("Peak", rows[0].name)
        self.assertIn("Peak", rows[1].name)

    def test_a_model_is_scored_across_every_benchmark_it_carries(self):
        # no family wins: 2.1 and SWE are read on their own scales and the
        # score is the median of the two
        table = {TB: (20.0, 60.0), SWE: (20.0, 60.0)}
        rows = build_rows(
            [_m("a/m1", 1.0, tb=55.8, swe=40.0), _m("a/m2", 1.0, tb=40.0, swe=30.0)],
            table=table,
        )
        row = next(r for r in rows if r.model.model_id == "a/m1")
        self.assertAlmostEqual(row.score, 69.75, places=2)

    def test_terminal_bench_hard_is_not_a_2_x_run(self):
        # TB Hard / TB-Science are separate benchmarks, not versions of
        # Terminal-Bench: a model carrying only those has no primary score
        row = build_rows([_m("a/m1", 1.0, scores={"Terminal-Bench Hard": 20.5})])[0]
        self.assertIsNone(row.score)
        self.assertIsNone(row.benchmark)

    def test_a_space_spelled_terminal_bench_version_still_counts(self):
        # catalogs write both "Terminal-Bench 2.1" and "Terminal Bench 2.1"
        table = {"Terminal Bench 2.1": (40.0, 80.0), SWE: (20.0, 100.0)}
        rows = build_rows(
            [
                _m("a/m1", 1.0, tb=20.0, scores={"Terminal Bench 2.1": 71.0}),
                _m("a/m2", 1.0, tb=100.0, scores={"Terminal Bench 2.1": 50.0}),
            ],
            table=table,
        )
        self.assertEqual(rows[0].benchmark, "Terminal Bench 2.1")
        # 71 of 40-80 is 77.5, paired with 20 of 20-100 = 0: median 38.75
        self.assertEqual(rows[0].score, 38.75)

    def test_hard_does_not_contaminate_the_4_run(self):
        # "Terminal-Bench Hard" is a separate benchmark that merely shares
        # the prefix; the model's score comes from its real runs
        table = {
            "Terminal-Bench Hard": (10.0, 30.0),
            "Terminal-Bench 4.0": (40.0, 60.0),
        }
        rows = build_rows(
            [
                _m(
                    "a/m1",
                    1.0,
                    scores={"Terminal-Bench Hard": 20.5, "Terminal-Bench 4.0": 55.8},
                ),
                _m(
                    "a/m2",
                    1.0,
                    scores={"Terminal-Bench Hard": 15.0, "Terminal-Bench 4.0": 45.0},
                ),
            ],
            table=table,
        )
        rows = order_rows(rows)
        # 20.5 on Hard is 52.5 of that benchmark's range and 55.8 on 4.0 is
        # 79 — the score is the median of the model's own derived scores,
        # not the raw 20.5 the old primary-score rule would have shown
        row = next(r for r in rows if r.model.model_id == "a/m1")
        self.assertAlmostEqual(row.score, 65.75, places=2)
        self.assertGreater(row.score, 55.8)

    def test_a_swe_only_model_is_ordered_on_swe_but_carries_no_score(self):
        table = {SWE: (20.0, 60.0)}
        rows = build_rows(
            [
                _m("a/m1", 1.0, swe=40.0, alone=True),
                _m("a/m2", 1.0, swe=30.0, alone=True),
            ],
            table=table,
        )
        self.assertEqual(rows[0].benchmark, SWE)
        self.assertIsNone(rows[0].score)

    def test_a_swe_measured_model_is_scored_against_the_swe_bounds(self):
        table = {SWE: (20.0, 60.0), TB: (20.0, 100.0)}
        rows = build_rows(
            [_m("a/m1", 1.0, tb=20.0, swe=40.0), _m("a/m2", 1.0, tb=20.0, swe=30.0)],
            table=table,
        )
        by_id = {r.model.model_id: r for r in rows}
        self.assertEqual(by_id["a/m1"].benchmark, SWE)
        # 40 of the SWE range 20-60 is 50, 20 of the 2.x range 20-100 is 0
        self.assertEqual(by_id["a/m1"].score, 25.0)

    def test_both_scores_land_on_one_comparable_scale(self):
        # 4.0 is a harder task set (top score ~58 vs ~87): its raw numbers
        # must not be compared with 2.1's, so each is read on its own scale
        table = {TB: (20.0, 80.0), TB4: (10.0, 60.0)}
        rows = build_rows(
            [
                _m("l/low", 1.0, tb=50.0, tb40=40.0),
                _m("l/high", 1.0, tb=70.0, tb40=55.0),
            ],
            table=table,
        )
        # low: 50 on 2.1 (of 20-80) and 60 on 4.0 (of 10-60) → median 55
        # high: 83.3 and 90 → median 86.7, and it leads on both
        self.assertAlmostEqual(rows[0].score, 55.0)
        self.assertGreater(rows[1].score, rows[0].score)

    def test_a_4_only_model_is_ordered_on_the_4_scale_but_carries_no_score(self):
        # the axis is still Terminal-Bench 4.0, and it is still read on the
        # 4.0 scale - but one benchmark is not a score, so the row is
        # unranked rather than placed on a single measurement
        table = {TB4: (20.0, 60.0)}
        rows = build_rows(
            [
                _m("l/weak", 1.0, tb40=30.0, alone=True),
                _m("l/strong", 1.0, tb40=50.0, alone=True),
            ],
            table=table,
        )
        self.assertEqual(rows[0].benchmark, TB4)
        self.assertEqual([r.score for r in rows], [None, None])

    def test_a_4_measured_model_is_scored_against_the_4_bounds(self):
        # with a second axis to pair it, the 4.0 number is scaled on the 4.0
        # range: 30 of 20-60 is 25, whatever the 2.x scale would say
        table = {TB4: (20.0, 60.0), TB: (20.0, 100.0)}
        rows = build_rows(
            [
                _m("l/weak", 1.0, tb=20.0, tb40=30.0),
                _m("l/strong", 1.0, tb=20.0, tb40=50.0),
            ],
            table=table,
        )
        by_id = {r.model.model_id: r for r in rows}
        self.assertEqual(by_id["l/weak"].benchmark, TB4)
        # 30 of the 4.0 range 20-60 is 25, and 20 on 2.x is 0: the axis and
        # the number both come from Terminal-Bench 4.0
        self.assertEqual(by_id["l/weak"].score, 12.5)


class MergeTest(unittest.TestCase):
    def test_rows_carry_the_provider_they_are_listed_under(self):
        self.assertEqual(build_rows([_m("l/a", 1.0, tb=50.0)])[0].provider, "-")
        self.assertEqual(
            build_rows([_m("l/a", 1.0, tb=50.0)], provider="go")[0].provider, "go"
        )

    def test_a_free_line_keeps_price_zero_across_providers(self):
        # price 0 is per-model rather than per-group, so merging
        # zen and go changes nothing about what a free line costs
        zen = build_rows(
            [_m("l/zfree", 5.0, tb=40.0)],
            [_entry("zfree", 0.0, 0.0, free=True)],
            provider="zen",
        )
        go = build_rows([_m("l/gopaid", 0.2, tb=40.0)], provider="go")
        merged = merge_rows(zen, go)
        self.assertEqual([r.provider for r in merged], ["zen", "go"])
        free = [r for r in merged if r.free]
        paid = [r for r in merged if not r.free]
        self.assertEqual(len(free), 1)
        self.assertEqual(free[0].price, 0.0)
        self.assertEqual(len(paid), 1)
        self.assertAlmostEqual(paid[0].price, 0.2)


class DedupeTest(unittest.TestCase):
    def test_the_same_line_from_two_providers_becomes_one_row(self):
        go = build_rows(
            [_m("l/ds", 1.0, tb=50.0)],
            [_entry("ds", 1.0, 1.0, cached=1.0, limit=60.0, tier="go")],
            provider="go",
        )
        zen = build_rows(
            [_m("l/ds", 1.0, tb=50.0)],
            [_entry("ds", 1.0, 1.0, cached=1.0, tier="zen")],
            provider="zen",
        )
        rows = dedupe_providers(merge_rows(go, zen))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].provider, "go,zen")
        self.assertEqual(rows[0].price, go[0].price)
        # identical cost, but only go funds it from a monthly allowance
        self.assertEqual(rows[0].band, "included")

    def test_lines_that_differ_in_cost_stay_separate(self):
        go = build_rows(
            [_m("l/ds", 1.0, tb=50.0)],
            [
                _entry(
                    "ds",
                    0.66,
                    1.98,
                    cached=0.022,
                    limit=15.0,
                    name="DS (Off-Peak)",
                    tier="go",
                )
            ],
            provider="go",
        )
        zen = build_rows(
            [_m("l/ds", 1.0, tb=50.0)],
            [_entry("ds", 1.74, 3.0, cached=0.04, tier="zen")],
            provider="zen",
        )
        rows = dedupe_providers(merge_rows(go, zen))
        self.assertEqual(len(rows), 2)  # different cost
        self.assertEqual([r.provider for r in rows], ["go", "zen"])

    def test_providers_are_enumerated_in_a_stable_order(self):
        # even with zen handed over first the pair reads 'go,zen'
        zen = build_rows(
            [_m("l/ds", 1.0, tb=50.0)],
            [_entry("ds", 1.0, 1.0, cached=1.0, tier="zen")],
            provider="zen",
        )
        go = build_rows(
            [_m("l/ds", 1.0, tb=50.0)],
            [_entry("ds", 1.0, 1.0, cached=1.0, limit=60.0, tier="go")],
            provider="go",
        )
        rows = dedupe_providers(merge_rows(zen, go))
        self.assertEqual([r.provider for r in rows], ["go,zen"])

    def test_free_lines_from_both_providers_merge_too(self):
        free = [_entry("gift", 0.0, 0.0, free=True)]
        go = build_rows([_m("l/gift", 5.0, tb=40.0)], free, provider="go")
        zen = build_rows([_m("l/gift", 5.0, tb=40.0)], free, provider="zen")
        rows = dedupe_providers(merge_rows(go, zen))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].provider, "go,zen")
        self.assertEqual(rows[0].band, "free")
        self.assertTrue(rows[0].name.endswith("[free]"))


class AxisScoresTest(unittest.TestCase):
    """The console shows every ordering axis's raw score side by side."""

    def test_a_model_carrying_both_axes_shows_both(self):
        m = _m("l/both", 1.0, tb=88.0, swe=64.0)
        self.assertEqual(axis_scores(m), {"tb2": 88.0, "tb4": None, "swe": 64.0})

    def test_tb2_never_shows_the_4_run_or_a_sibling_benchmark(self):
        m = _m(
            "l/four",
            1.0,
            tb40=55.8,
            scores={"Terminal-Bench Hard": 20.5, "Terminal-Bench-Science 0.1": 52.6},
        )
        scores = axis_scores(m)
        self.assertIsNone(scores["tb2"])  # no 2.x run on this model
        self.assertEqual(scores["tb4"], 55.8)

    def test_a_bare_terminal_bench_key_without_a_version_counts_for_nothing(self):
        # 'Terminal-Bench' with no version is not a versioned run; a model
        # carrying only it has no axis score at all
        m = _m("l/bare", 1.0, scores={"Terminal-Bench": 70.0})
        self.assertEqual(axis_scores(m), {"tb2": None, "tb4": None, "swe": None})

    def test_the_swe_axis_takes_the_best_bench_run(self):
        m = _m("l/swe", 1.0, scores={"SWE-Bench Verified": 71.0})
        self.assertEqual(axis_scores(m)["swe"], 71.0)


class GradeTest(unittest.TestCase):
    """The rank marker reads the score, not the position: 90+ is ++, 75+ is
    +, 60+ is o, 50+ is -, below that --, and no score is ·."""

    def test_thresholds(self):
        cases = [
            (100.0, "++"),
            (90.0, "++"),
            (89.9, "+"),
            (75.0, "+"),
            (74.9, "o"),
            (60.0, "o"),
            (59.9, "-"),
            (50.0, "-"),
            (49.9, "--"),
            (0.0, "--"),
        ]
        for score, marker in cases:
            with self.subTest(score=score):
                self.assertEqual(grade_for(score), marker)

    def test_no_score_is_unranked(self):
        self.assertEqual(grade_for(None), "·")

    def test_every_row_in_a_list_can_share_one_band(self):
        # absolute bands, not deciles: nothing forces a loser into existence
        rows = _rows([(90.0, 1.0), (91.0, 1.0), (92.0, 1.0)])
        self.assertEqual([r.grade for r in grade_rows(rows)], ["++", "++", "++"])

    def test_position_never_changes_a_grade(self):
        forward = grade_rows(_rows([(90.0, 1.0), (40.0, 1.0)]))
        backward = grade_rows(_rows([(40.0, 1.0), (90.0, 1.0)]))
        self.assertEqual([r.grade for r in forward], ["++", "--"])
        self.assertEqual([r.grade for r in backward], ["--", "++"])

    def test_unscored_rows_are_unranked_not_last(self):
        # a free model nobody benchmarks is not the bottom of the table,
        # it is simply unranked — shown as · so nobody reads it as a verdict
        models = [_m("l/tb", 1.0, tb=70.0), _m("l/freebie", 5.0)]
        entries = [_entry("freebie", 0.0, 0.0, free=True)]
        graded = grade_rows(
            order_rows(
                build_rows(models, entries, table={TB: (20.0, 80.0), SWE: (20.0, 80.0)})
            )
        )
        self.assertEqual([r.grade for r in graded], ["+", "·"])
        self.assertIsNone(graded[1].score)

    def test_grades_ride_on_the_rows_best_first(self):
        models = [_m(f"l/m{i:02d}", float(i), tb=float(i * i)) for i in range(1, 11)]
        graded = grade_rows(order_rows(build_rows(models)))
        self.assertEqual([r.grade for r in graded][:1], ["++"])
        self.assertEqual(graded[-1].grade, "--")
        # value order: m10 scores 100 at $10 → the best points per dollar,
        # m01 the worst, so the marks follow the value ranking not the score
        self.assertEqual(graded[0].model.model_id, "l/m10")
        self.assertEqual(graded[-1].model.model_id, "l/m01")


class OrderTest(unittest.TestCase):
    def test_rows_lead_in_score_per_dollar_order(self):
        # the SCORE column is one comparable number, so value order is
        # score divided by price - no skeleton, no percentile insertion
        models = [_m("l/b", 1.0, tb=60.0, swe=60.0), _m("l/a", 2.0, tb=80.0, swe=80.0)]
        ordered = order_rows(build_rows(models))
        # b derives 0 on both scales, a derives 100 on both: 0/1 vs 100/2
        self.assertEqual([r.model.model_id for r in ordered], ["l/a", "l/b"])

    def test_axes_do_not_matter_only_score_and_price_do(self):
        # a strong SWE pair outranks a weak Terminal-Bench pair at the same
        # price: the benchmark family never enters the ordering
        models = [
            _m("l/tb", 1.0, tb=20.0, swe=20.0),
            _m("l/swe", 1.0, tb=90.0, swe=90.0),
        ]
        ordered = [r.model.model_id for r in order_rows(build_rows(models))]
        self.assertEqual(ordered, ["l/swe", "l/tb"])

    def test_ties_break_toward_the_higher_score(self):
        models = [
            _m("l/cheap", 1.0, tb=50.0, swe=50.0),
            _m("l/dear", 2.0, tb=100.0, swe=100.0),
        ]
        ordered = [r.model.model_id for r in order_rows(build_rows(models))]
        # 50/1 == 100/2 == 50 per dollar, so the higher score leads
        self.assertEqual(ordered, ["l/dear", "l/cheap"])

    def test_weak_secondary_row_fills_the_tail(self):
        models = [
            _m(f"l/tb{i}", 1.0, tb=t, swe=s)
            for i, (t, s) in enumerate([(90, 90), (70, 70), (50, 50)], 1)
        ]
        models.append(_m("l/sweweak", 1.0, swe=10.0, alone=True))
        ordered = order_rows(build_rows(models))
        self.assertEqual([r.model.model_id for r in ordered][-1], "l/sweweak")
        self.assertIsNone(
            next(r for r in ordered if r.model.model_id == "l/sweweak").score
        )

    def test_a_terminal_bench_hard_row_does_not_split_the_skeleton(self):
        # the ordering's primary lookup must be as strict as the row
        # builder's: TB Hard is its own benchmark, not a 2.x run, so a
        # model carrying only it is unscored for ranking
        models = [_m(f"l/tb{i}", 1.0, tb=t) for i, t in enumerate([90.0, 70.0], 1)]
        models.append(_m("l/hard", 1.0, scores={"Terminal-Bench Hard": 20.5}))
        self.assertEqual(
            [r.model.model_id for r in order_rows(build_rows(models))],
            ["l/tb1", "l/tb2"],
        )  # l/hard has no ranking axis

    def test_rows_with_neither_benchmark_drop_out(self):
        models = [_m("l/tb", 1.0, tb=70.0), _m("l/unbenchmarked", 1.0)]
        ordered = order_rows(build_rows(models))
        self.assertEqual([r.model.model_id for r in ordered], ["l/tb"])

    def test_a_free_row_without_benchmark_scores_is_still_listed(self):
        # Go offers two free models no benchmark covers — maximising free
        # usage means showing them, ranked below everything that is scored
        # (the table needs real spread, or nothing scores at all)
        models = [_m("l/tb", 1.0, tb=70.0), _m("l/freebie", 5.0)]
        entries = [_entry("freebie", 0.0, 0.0, free=True)]
        ordered = order_rows(
            build_rows(models, entries, table={TB: (20.0, 80.0), SWE: (20.0, 80.0)})
        )
        self.assertEqual([r.model.model_id for r in ordered], ["l/tb", "l/freebie"])

    def test_single_axis_rows_sort_after_everything_scored(self):
        # one benchmark is not a score on any axis, 4.0 included: these rows
        # cannot be valued and sort after the scored ones, hot or not
        models = [
            _m(f"l/tb{i}", 1.0, tb=t) for i, t in enumerate([100, 80, 60, 40, 20], 1)
        ]
        models += [
            _m("l/fourhot", 1.0, tb40=88.0, alone=True),
            _m("l/fourcool", 1.0, tb40=22.0, alone=True),
        ]
        ordered = [r.model.model_id for r in order_rows(build_rows(models))]
        self.assertEqual(
            ordered,
            ["l/tb1", "l/tb2", "l/tb3", "l/tb4", "l/tb5", "l/fourhot", "l/fourcool"],
        )

    def test_a_free_single_axis_row_sorts_with_the_unscored(self):
        # free does not promote a row past scored ones: without a score it
        # belongs with the tail, ahead of paid unscored rows
        models = [
            _m("l/tb", 1.0, tb=70.0, swe=70.0),
            _m("l/paidweak", 1.0, tb=10.0, alone=True),
            _m("l/free4", 5.0, tb40=30.0, alone=True),
        ]
        entries = [_entry("free4", 0.0, 0.0, free=True)]
        ordered = order_rows(
            build_rows(
                models,
                entries,
                table={TB: (10.0, 70.0), SWE: (10.0, 70.0), TB4: (10.0, 70.0)},
            )
        )
        self.assertEqual(
            [r.model.model_id for r in ordered], ["l/tb", "l/free4", "l/paidweak"]
        )


class PartitionTest(unittest.TestCase):
    def _grid(self):
        # m01..m10: price i, score i² → score-per-dollar climbs with i
        models = [_m(f"l/m{i:02d}", float(i), tb=float(i * i)) for i in range(1, 11)]
        return order_rows(build_rows(models))

    def test_three_disjoint_tables_and_who_goes_where(self):
        main, worst, pricey = partition_rows(self._grid())
        # pricey still *selects* the three dearest, but every cull table is
        # *shown* worst-scoring first
        self.assertEqual([r.name for r in pricey], ["M08", "M09", "M10"])
        self.assertEqual([r.name for r in worst], ["M01", "M02", "M03"])
        self.assertEqual([r.name for r in main], ["M07", "M06", "M05", "M04"])
        seen = [r.name for group in (main, worst, pricey) for r in group]
        self.assertEqual(sorted(seen), sorted(r.name for r in self._grid()))

    def test_a_model_that_is_both_priciest_and_worst_only_appears_once(self):
        # a is the most expensive AND the lowest scorer: the cost group has
        # first claim, so the worst-score table must not repeat it
        models = [
            _m("l/a", 10.0, tb=1.0),
            _m("l/b", 1.0, tb=100.0),
            _m("l/c", 2.0, tb=90.0),
            _m("l/d", 3.0, tb=80.0),
            _m("l/e", 4.0, tb=70.0),
        ]
        main, worst, pricey = partition_rows(order_rows(build_rows(models)))
        self.assertEqual([r.model.model_id for r in pricey], ["l/a", "l/e"])
        self.assertNotIn("l/a", [r.model.model_id for r in worst])
        self.assertEqual([r.model.model_id for r in worst], ["l/d", "l/c"])
        self.assertEqual([r.model.model_id for r in main], ["l/b"])

    def test_cull_size_is_the_nearest_whole_model_to_thirty_percent(self):
        rows = self._grid()  # 10 rows → 3 each
        self.assertEqual([len(g) for g in partition_rows(rows)], [4, 3, 3])
        few = [_m(f"l/m{i}", float(i), tb=float(i)) for i in range(1, 5)]
        self.assertEqual(
            [len(g) for g in partition_rows(order_rows(build_rows(few)))], [2, 1, 1]
        )  # 30% of 4 = 1.2 → 1, not 2

    def test_a_single_row_is_never_culled(self):
        rows = order_rows(build_rows([_m("l/only", 1.0, tb=50.0)]))
        main, worst, pricey = partition_rows(rows)
        self.assertEqual(len(main), 1)
        self.assertEqual(worst, [])
        self.assertEqual(pricey, [])


class ScorePerPriceRankingTest(unittest.TestCase):
    BENCH = "TB"

    def _m(self, model_id, price, score, output=None):
        from opencode_models.models import Model

        return Model(
            model_id=model_id,
            name=model_id,
            lab="x",
            price_input=price,
            price_output=output,
            price_cached_input=None,
            scores={} if score is None else {"TB": score},
        )

    def test_sorted_by_score_over_price_descending(self):
        out = by_score_per_price(
            [
                self._m("a", 2.0, 80.0),
                self._m("b", 10.0, 90.0),
                self._m("c", 1.0, 40.0),
            ],
            self.BENCH,
        )
        self.assertEqual([m.model_id for m in out], ["a", "c", "b"])

    def test_free_models_rank_by_score(self):
        out = by_score_per_price(
            [
                self._m("paid", 1.0, 50.0),
                self._m("free", 0.0, 30.0),
                self._m("free2", 0.0, 60.0),
            ],
            self.BENCH,
        )
        self.assertEqual([m.model_id for m in out], ["free2", "free", "paid"])

    def test_excludes_models_missing_price_or_score(self):
        out = by_score_per_price(
            [
                self._m("ok", 1.0, 50.0),
                self._m("np", None, 90.0),
                self._m("ns", 1.0, None),
            ],
            self.BENCH,
        )
        self.assertEqual([m.model_id for m in out], ["ok"])

    def test_output_price_axis_changes_ratio(self):
        models = [
            self._m("a", 2.0, 80.0, output=5.0),
            self._m("b", 2.0, 90.0, output=10.0),
        ]
        out = by_score_per_price(models, self.BENCH, price_field="price_output")
        self.assertEqual([m.model_id for m in out], ["a", "b"])

    def test_unknown_price_field_raises(self):
        with self.assertRaises(ValueError):
            by_score_per_price(
                [self._m("a", 1.0, 50.0)], self.BENCH, price_field="price_cache_write"
            )


if __name__ == "__main__":
    unittest.main()


def _rows(scores_and_prices):
    """Rows with set scores, for grading without building anything."""
    from dataclasses import replace

    from opencode_models.models import Model

    rows = []
    for i, (score, price) in enumerate(scores_and_prices):
        model = Model(
            model_id=f"l/m{i:02d}",
            name=f"M{i:02d}",
            lab="l",
            price_input=price,
            price_output=price,
            price_cached_input=price,
            scores={"Terminal-Bench 2.1": score} if score is not None else {},
        )
        rows.append(
            replace(
                Row(
                    model=model,
                    name=f"M{i:02d}",
                    price=price,
                    free=price == 0,
                    band="included",
                    score=score,
                    benchmark="Terminal-Bench 2.1" if score is not None else None,
                    provider="zen",
                ),
                grade="?",
            )
        )
    return rows


class GradeByScoreTest(unittest.TestCase):
    """The marker answers "how strong is this model", not "how cheap".

    Price sorts the table — that is the Pareto view doing its job — but a
    model that scores well should not be graded "middle" for costing money,
    and a cheap model should not be graded "top" for being cheap.
    """

    def _rows(self, scores_and_prices):
        return _rows(scores_and_prices)

    def test_a_strong_but_pricey_model_is_graded_top(self):
        rows = self._rows(
            [(90.0, 100.0), (50.0, 0.0), (40.0, 0.0), (30.0, 0.0), (20.0, 0.0)]
        )
        graded = {r.name: r.grade for r in grade_rows(rows)}
        self.assertEqual(graded["M00"], "++")

    def test_a_cheap_but_weak_model_is_not_graded_top(self):
        rows = self._rows(
            [(90.0, 100.0), (50.0, 0.0), (40.0, 0.0), (30.0, 0.0), (20.0, 0.0)]
        )
        graded = {r.name: r.grade for r in grade_rows(rows)}
        self.assertNotEqual(graded["M01"], "++")
        # and the bands follow the scores, top to bottom
        self.assertEqual(
            [graded[f"M{i:02d}"] for i in range(5)], ["++", "-", "--", "--", "--"]
        )

    def test_the_grades_do_not_depend_on_price_at_all(self):
        cheap = grade_rows(self._rows([(90.0, 100.0), (50.0, 0.0)]))
        dear = grade_rows(self._rows([(90.0, 0.0), (50.0, 100.0)]))
        self.assertEqual([r.grade for r in cheap], [r.grade for r in dear])

    def test_a_row_with_no_score_stays_unranked(self):
        rows = self._rows([(90.0, 1.0), (None, 0.0), (50.0, 1.0)])
        graded = {r.name: r.grade for r in grade_rows(rows)}
        self.assertEqual(graded["M01"], "·")

    def test_grading_leaves_the_order_of_rows_alone(self):
        rows = self._rows([(90.0, 100.0), (50.0, 0.0), (40.0, 0.0)])
        graded = grade_rows(rows)
        self.assertEqual([r.name for r in graded], [r.name for r in rows])
