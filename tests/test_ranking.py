"""Console list: pricing rows, the combined-benchmark order, the cull split."""

import unittest

from opencode_rank.data import Model
from opencode_rank.pricing import PriceEntry
from opencode_rank.ranking import (
    axis_scores,
    build_rows,
    grade_rows,
    grade_symbols,
    dedupe_providers,
    merge_rows,
    order_rows,
    partition_rows,
)

TB = "Terminal-Bench 2.1"
TB4 = "Terminal-Bench 4.0"
SWE = "SWE-Bench Pro"


def _m(model_id, price, tb=None, swe=None, output=None, cached=None, name=None,
       tb40=None, scores=None):
    scores = dict(scores or {})
    if tb is not None:
        scores[TB] = tb
    if swe is not None:
        scores[SWE] = swe
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


def _entry(model_id, inp, out, cached=None, limit=None, free=False,
           name=None, tier="go"):
    return PriceEntry(tier=tier, model_id=model_id,
                      name=name or model_id, input=inp, output=out,
                      cached_read=cached, limit=limit, free=free)


class RowBuildingTest(unittest.TestCase):
    def test_documented_entry_replaces_the_catalog_price(self):
        # catalog says $1.74 (the Zen rate); Go documents $0.66 off-peak
        models = [_m("lab/deepseek-v4-pro", 1.74, tb=50.0)]
        entries = [_entry("deepseek-v4-pro", 0.66, 1.98, cached=0.022,
                          limit=15.0, name="DeepSeek V4 Pro (Off-Peak)")]
        rows = build_rows(models, entries)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].price, 0.2 * 0.66 + 0.6 * 0.022 + 0.2 * 1.98)
        self.assertEqual(rows[0].band, "included")   # Go grants $15/month
        self.assertIn("Off-Peak", rows[0].name)

    def test_free_line_costs_a_tenth_of_the_cheapest_real_price(self):
        models = [_m("lab/paid", 1.0, tb=50.0),
                  _m("lab/gift", 5.0, tb=40.0)]
        entries = [
            _entry("paid", 1.0, 1.0, cached=1.0),          # costs 1.0
            _entry("gift", 0.0, 0.0, cached=0.0, free=True),  # free tier
        ]
        rows = {r.model.model_id: r for r in build_rows(models, entries)}
        self.assertEqual(rows["lab/gift"].price, 0.1)   # 1.0 / 10
        self.assertEqual(rows["lab/gift"].band, "free")
        self.assertTrue(rows["lab/gift"].name.endswith("[free]"))
        self.assertEqual(rows["lab/paid"].price, 1.0)
        self.assertEqual(rows["lab/paid"].band, "payg")

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
        self.assertAlmostEqual(build_rows(models, entries)[0].price,
                               0.2 * 1.0 + 0.6 * 0.5 + 0.2 * 2.0)

    def test_the_same_variant_from_a_second_tier_does_not_double_the_line(self):
        # `list --source all` hands over Go's and Zen's tables together
        models = [_m("l/ds", 1.74, tb=50.0)]
        entries = [
            _entry("ds", 0.66, 1.98, cached=0.022, limit=15.0,
                   name="DS (Off-Peak)", tier="go"),
            _entry("ds", 1.74, 3.0, cached=0.04,
                   name="DS (Off-Peak)", tier="zen"),
        ]
        rows = build_rows(models, entries)
        self.assertEqual(len(rows), 1)          # one line, not one per tier
        self.assertAlmostEqual(rows[0].price, 0.2 * 0.66 + 0.6 * 0.022 + 0.2 * 1.98)

    def test_each_variant_is_its_own_line_at_its_own_price(self):
        models = [_m("lab/deepseek-v4-pro", 1.74, tb=50.0)]
        entries = [
            _entry("deepseek-v4-pro", 0.66, 1.98, cached=0.022, limit=15.0,
                   name="DeepSeek V4 Pro (Off-Peak)"),
            _entry("deepseek-v4-pro", 1.32, 3.96, cached=0.044, limit=15.0,
                   name="DeepSeek V4 Pro (Peak)"),
        ]
        rows = build_rows(models, entries)
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[0].price, rows[1].price)
        self.assertIn("Peak", rows[0].name)
        self.assertIn("Peak", rows[1].name)

    def test_rows_carry_their_best_terminal_bench_score(self):
        models = [_m("a/m1", 1.0, tb=55.8, swe=40.0)]
        row = build_rows(models, [])[0]
        self.assertEqual(row.score, 55.8)          # primary family wins
        self.assertEqual(row.benchmark, TB)

    def test_terminal_bench_hard_is_not_a_2_x_run(self):
        # TB Hard / TB-Science are separate benchmarks, not versions of
        # Terminal-Bench: a model carrying only those has no primary score
        row = build_rows([_m("a/m1", 1.0,
                             scores={"Terminal-Bench Hard": 20.5})])[0]
        self.assertIsNone(row.score)
        self.assertIsNone(row.benchmark)

    def test_a_space_spelled_terminal_bench_version_still_counts(self):
        # catalogs write both "Terminal-Bench 2.1" and "Terminal Bench 2.1"
        row = build_rows([_m("a/m1", 1.0,
                             scores={"Terminal Bench 2.1": 71.0})])[0]
        self.assertEqual(row.score, 71.0)
        self.assertEqual(row.benchmark, "Terminal Bench 2.1")

    def test_a_model_with_only_hard_falls_back_to_its_4_run_or_nothing(self):
        rows = order_rows(build_rows([_m("a/m1", 1.0,
                                          scores={"Terminal-Bench Hard": 20.5,
                                                  "Terminal-Bench 4.0": 55.8})]))
        self.assertEqual(rows[0].score, 55.8)     # ranked on its 4.0 run
        self.assertEqual(rows[0].benchmark, "Terminal-Bench 4.0")

    def test_a_swe_only_row_falls_back_to_the_secondary_family(self):
        models = [_m("a/m1", 1.0, swe=40.0)]
        row = build_rows(models, [])[0]
        self.assertEqual(row.score, 40.0)
        self.assertEqual(row.benchmark, SWE)

    def test_the_4_score_never_wins_the_2_slot(self):
        # 4.0 is a harder task set (top score ~58 vs ~87): a model carrying
        # both ranks on its 2.1 number, not the inflated-looking 4.0 gap
        row = build_rows([_m("l/both", 1.0, tb=50.0, tb40=88.0)])[0]
        self.assertEqual(row.benchmark, TB)
        self.assertEqual(row.score, 50.0)

    def test_a_4_only_model_keeps_its_score_for_display(self):
        row = build_rows([_m("l/four", 1.0, tb40=41.0)])[0]
        self.assertEqual(row.benchmark, TB4)
        self.assertEqual(row.score, 41.0)


class MergeTest(unittest.TestCase):
    def test_rows_carry_the_provider_they_are_listed_under(self):
        self.assertEqual(build_rows([_m("l/a", 1.0, tb=50.0)])[0].provider, "-")
        self.assertEqual(
            build_rows([_m("l/a", 1.0, tb=50.0)], provider="go")[0].provider, "go")

    def test_free_lines_share_one_price_scale_across_providers(self):
        # zen sells a model for free, go sells another at $0.20: the free
        # line has to be 1/10 of the cheapest price in the *whole* view,
        # not of whatever its own provider group happened to contain
        zen = build_rows([_m("l/zfree", 5.0, tb=40.0)],
                         [_entry("zfree", 0.0, 0.0, free=True)], provider="zen")
        go = build_rows([_m("l/gopaid", 0.2, tb=40.0)], provider="go")
        merged = merge_rows(zen, go)
        self.assertEqual([r.provider for r in merged], ["zen", "go"])
        free = [r for r in merged if r.free]
        paid = [r for r in merged if not r.free]
        self.assertEqual(len(free), 1)
        self.assertAlmostEqual(free[0].price, 0.02)   # 0.2 / 10
        self.assertEqual(len(paid), 1)
        self.assertAlmostEqual(paid[0].price, 0.2)


class DedupeTest(unittest.TestCase):
    def test_the_same_line_from_two_providers_becomes_one_row(self):
        go = build_rows([_m("l/ds", 1.0, tb=50.0)],
                        [_entry("ds", 1.0, 1.0, cached=1.0, limit=60.0,
                                tier="go")], provider="go")
        zen = build_rows([_m("l/ds", 1.0, tb=50.0)],
                         [_entry("ds", 1.0, 1.0, cached=1.0, tier="zen")],
                         provider="zen")
        rows = dedupe_providers(merge_rows(go, zen))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].provider, "go,zen")
        self.assertEqual(rows[0].price, go[0].price)
        # identical cost, but only go funds it from a monthly allowance
        self.assertEqual(rows[0].band, "included")

    def test_lines_that_differ_in_cost_stay_separate(self):
        go = build_rows([_m("l/ds", 1.0, tb=50.0)],
                        [_entry("ds", 0.66, 1.98, cached=0.022, limit=15.0,
                                name="DS (Off-Peak)", tier="go")],
                        provider="go")
        zen = build_rows([_m("l/ds", 1.0, tb=50.0)],
                         [_entry("ds", 1.74, 3.0, cached=0.04, tier="zen")],
                         provider="zen")
        rows = dedupe_providers(merge_rows(go, zen))
        self.assertEqual(len(rows), 2)                 # different cost
        self.assertEqual([r.provider for r in rows], ["go", "zen"])

    def test_providers_are_enumerated_in_a_stable_order(self):
        # even with zen handed over first the pair reads 'go,zen'
        zen = build_rows([_m("l/ds", 1.0, tb=50.0)],
                         [_entry("ds", 1.0, 1.0, cached=1.0, tier="zen")],
                         provider="zen")
        go = build_rows([_m("l/ds", 1.0, tb=50.0)],
                        [_entry("ds", 1.0, 1.0, cached=1.0, limit=60.0,
                                tier="go")], provider="go")
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
        self.assertEqual(axis_scores(m), {"tb2": 88.0, "tb4": None,
                                          "swe": 64.0})

    def test_tb2_never_shows_the_4_run_or_a_sibling_benchmark(self):
        m = _m("l/four", 1.0, tb40=55.8,
               scores={"Terminal-Bench Hard": 20.5,
                       "Terminal-Bench-Science 0.1": 52.6})
        scores = axis_scores(m)
        self.assertIsNone(scores["tb2"])      # no 2.x run on this model
        self.assertEqual(scores["tb4"], 55.8)

    def test_a_bare_terminal_bench_key_without_a_version_counts_for_nothing(self):
        # 'Terminal-Bench' with no version is not a versioned run; a model
        # carrying only it has no axis score at all
        m = _m("l/bare", 1.0, scores={"Terminal-Bench": 70.0})
        self.assertEqual(axis_scores(m), {"tb2": None, "tb4": None,
                                          "swe": None})

    def test_the_swe_axis_takes_the_best_bench_run(self):
        m = _m("l/swe", 1.0, scores={"SWE-Bench Verified": 71.0})
        self.assertEqual(axis_scores(m)["swe"], 71.0)


class GradeTest(unittest.TestCase):
    """The rank marker: ++ top decile, + next tenth, o middle, - bottom fifth."""

    def test_a_twenty_row_list_splits_ten_ten_sixty_twenty(self):
        symbols = grade_symbols(20)
        self.assertEqual(symbols.count("++"), 2)    # top 10%
        self.assertEqual(symbols.count("+"), 2)     # next 10%
        self.assertEqual(symbols.count("-"), 4)     # bottom 20%
        self.assertEqual(symbols.count("o"), 12)
        self.assertEqual(symbols[0], "++")
        self.assertEqual(symbols[1], "++")
        self.assertEqual(symbols[2], "+")
        self.assertEqual(symbols[-1], "-")

    def test_short_lists_still_mark_every_band(self):
        # five rows cannot fill four bands exactly, but none may vanish
        symbols = grade_symbols(5)
        self.assertEqual(symbols.count("++"), 1)
        self.assertEqual(symbols.count("-"), 1)
        self.assertEqual(len(symbols), 5)
        self.assertEqual(symbols[0], "++")
        self.assertEqual(symbols[-1], "-")

    def test_a_lone_row_is_its_own_best(self):
        self.assertEqual(grade_symbols(1), ["++"])
        self.assertEqual(grade_symbols(0), [])

    def test_bands_never_overlap_on_tiny_lists(self):
        for n in range(1, 40):
            symbols = grade_symbols(n)
            self.assertEqual(len(symbols), n, n)
            self.assertEqual(symbols.count("++"), max(1, round(0.1 * n)), n)
            if n > 1:   # the lone row is its own best, so nothing is `-`
                self.assertEqual(symbols.count("-"),
                                 max(1, round(0.2 * n)), n)

    def test_unscored_rows_are_unranked_not_last(self):
        # a free model nobody benchmarks is not the bottom of the table,
        # it is simply unranked — shown as · so nobody reads it as a verdict
        models = [_m("l/tb", 1.0, tb=70.0), _m("l/freebie", 5.0)]
        entries = [_entry("freebie", 0.0, 0.0, free=True)]
        graded = grade_rows(order_rows(build_rows(models, entries)))
        self.assertEqual([r.grade for r in graded], ["++", "·"])
        self.assertIsNone(graded[1].score)

    def test_grades_ride_on_the_rows_best_first(self):
        models = [_m(f"l/m{i:02d}", float(i), tb=float(i * i))
                  for i in range(1, 11)]
        graded = grade_rows(order_rows(build_rows(models)))
        self.assertEqual([r.grade for r in graded][:1], ["++"])
        self.assertEqual(graded[-1].grade, "-")
        # value order: m10 scores 100 at $10 → the best points per dollar,
        # m01 the worst, so the marks follow the value ranking not the score
        self.assertEqual(graded[0].model.model_id, "l/m10")
        self.assertEqual(graded[-1].model.model_id, "l/m01")


class OrderTest(unittest.TestCase):
    def test_terminal_bench_rows_lead_in_value_order(self):
        models = [_m("l/b", 1.0, tb=60.0), _m("l/a", 2.0, tb=80.0)]
        ordered = order_rows(build_rows(models))
        # b: 60/1 = 60, a: 80/2 = 40 → b leads on value despite the lower score
        self.assertEqual([r.model.model_id for r in ordered], ["l/b", "l/a"])

    def test_swe_only_row_is_greedily_placed_at_its_percentile(self):
        # skeleton: five Terminal-Bench models; one strong SWE-only model must
        # land near the top rather than below every TB row
        models = [_m(f"l/tb{i}", 1.0, tb=t, swe=s)
                  for i, (t, s) in enumerate(
                      [(100, 90), (80, 70), (60, 50), (40, 30), (20, 10)], 1)]
        models.append(_m("l/swen", 1.0, swe=85.0))
        ordered = [r.model.model_id for r in order_rows(build_rows(models))]
        # 85 ranks 2nd of 6 on SWE → 1/5 of the way down the TB skeleton
        self.assertEqual(ordered, ["l/tb1", "l/swen", "l/tb2", "l/tb3",
                                   "l/tb4", "l/tb5"])

    def test_weak_secondary_row_fills_the_tail(self):
        models = [_m(f"l/tb{i}", 1.0, tb=t, swe=s)
                  for i, (t, s) in enumerate([(90, 90), (70, 70), (50, 50)], 1)]
        models.append(_m("l/sweweak", 1.0, swe=10.0))
        ordered = [r.model.model_id for r in order_rows(build_rows(models))]
        self.assertEqual(ordered[-1], "l/sweweak")

    def test_a_terminal_bench_hard_row_does_not_split_the_skeleton(self):
        # the ordering's primary lookup must be as strict as the row
        # builder's: TB Hard is its own benchmark, not a 2.x run, so a
        # model carrying only it is unscored for ranking
        models = [_m(f"l/tb{i}", 1.0, tb=t)
                  for i, t in enumerate([90.0, 70.0], 1)]
        models.append(_m("l/hard", 1.0,
                         scores={"Terminal-Bench Hard": 20.5}))
        self.assertEqual([r.model.model_id
                          for r in order_rows(build_rows(models))],
                         ["l/tb1", "l/tb2"])   # l/hard has no ranking axis

    def test_rows_with_neither_benchmark_drop_out(self):
        models = [_m("l/tb", 1.0, tb=70.0), _m("l/unbenchmarked", 1.0)]
        ordered = order_rows(build_rows(models))
        self.assertEqual([r.model.model_id for r in ordered], ["l/tb"])

    def test_a_free_row_without_benchmark_scores_is_still_listed(self):
        # Go offers two free models no benchmark covers — maximising free
        # usage means showing them, ranked below everything that is scored
        models = [_m("l/tb", 1.0, tb=70.0), _m("l/freebie", 5.0)]
        entries = [_entry("freebie", 0.0, 0.0, free=True)]
        ordered = order_rows(build_rows(models, entries))
        self.assertEqual([r.model.model_id for r in ordered],
                         ["l/tb", "l/freebie"])

    def test_tb4_only_rows_fill_in_at_their_percentile(self):
        # 4.0 scores are their own axis: inserted like SWE rows, never
        # sorted against 2.1 numbers
        models = [_m(f"l/tb{i}", 1.0, tb=t)
                  for i, t in enumerate([100, 80, 60, 40, 20], 1)]
        models += [_m("l/fourhot", 1.0, tb40=88.0),
                   _m("l/fourcool", 1.0, tb40=22.0)]
        ordered = [r.model.model_id for r in order_rows(build_rows(models))]
        # 22 is last of the two 4.0 rows → the bottom slot
        self.assertEqual(ordered, ["l/fourhot", "l/tb1", "l/tb2", "l/tb3",
                                   "l/tb4", "l/fourcool", "l/tb5"])

    def test_a_free_tb4_row_is_listed_once(self):
        # a free 4.0-only row is placed as a filler — not also appended to
        # the free tail that follows the scored rows
        models = [_m("l/tb", 1.0, tb=70.0), _m("l/free4", 5.0, tb40=30.0)]
        entries = [_entry("free4", 0.0, 0.0, free=True)]
        ordered = order_rows(build_rows(models, entries))
        self.assertEqual([r.model.model_id for r in ordered],
                         ["l/free4", "l/tb"])


class PartitionTest(unittest.TestCase):
    def _grid(self):
        # m01..m10: price i, score i² → score-per-dollar climbs with i
        models = [_m(f"l/m{i:02d}", float(i), tb=float(i * i))
                  for i in range(1, 11)]
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
        models = [_m("l/a", 10.0, tb=1.0), _m("l/b", 1.0, tb=100.0),
                  _m("l/c", 2.0, tb=90.0), _m("l/d", 3.0, tb=80.0),
                  _m("l/e", 4.0, tb=70.0)]
        main, worst, pricey = partition_rows(order_rows(build_rows(models)))
        self.assertEqual([r.model.model_id for r in pricey], ["l/a", "l/e"])
        self.assertNotIn("l/a", [r.model.model_id for r in worst])
        self.assertEqual([r.model.model_id for r in worst], ["l/d", "l/c"])
        self.assertEqual([r.model.model_id for r in main], ["l/b"])

    def test_cull_size_is_the_nearest_whole_model_to_thirty_percent(self):
        rows = self._grid()                      # 10 rows → 3 each
        self.assertEqual([len(g) for g in partition_rows(rows)], [4, 3, 3])
        few = [_m(f"l/m{i}", float(i), tb=float(i)) for i in range(1, 5)]
        self.assertEqual([len(g) for g in partition_rows(order_rows(build_rows(few)))],
                         [2, 1, 1])              # 30% of 4 = 1.2 → 1, not 2

    def test_a_single_row_is_never_culled(self):
        rows = order_rows(build_rows([_m("l/only", 1.0, tb=50.0)]))
        main, worst, pricey = partition_rows(rows)
        self.assertEqual(len(main), 1)
        self.assertEqual(worst, [])
        self.assertEqual(pricey, [])


if __name__ == "__main__":
    unittest.main()
