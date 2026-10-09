"""Console list: pricing rows, the combined-benchmark order, the cull split."""

import unittest

from modelpareto.data import Model
from modelpareto.pricing import PriceEntry
from modelpareto.ranking import build_rows, merge_rows, order_rows, partition_rows

TB = "Terminal-Bench 2.1"
SWE = "SWE-Bench Pro"


def _m(model_id, price, tb=None, swe=None, output=None, cached=None, name=None):
    scores = {}
    if tb is not None:
        scores[TB] = tb
    if swe is not None:
        scores[SWE] = swe
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

    def test_a_swe_only_row_falls_back_to_the_secondary_family(self):
        models = [_m("a/m1", 1.0, swe=40.0)]
        row = build_rows(models, [])[0]
        self.assertEqual(row.score, 40.0)
        self.assertEqual(row.benchmark, SWE)


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


class PartitionTest(unittest.TestCase):
    def _grid(self):
        # m01..m10: price i, score i² → score-per-dollar climbs with i
        models = [_m(f"l/m{i:02d}", float(i), tb=float(i * i))
                  for i in range(1, 11)]
        return order_rows(build_rows(models))

    def test_three_disjoint_tables_and_who_goes_where(self):
        main, worst, pricey = partition_rows(self._grid())
        self.assertEqual([r.name for r in pricey], ["M10", "M09", "M08"])
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
