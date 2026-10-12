"""Tier price snapshots scraped from the OpenCode Zen/Go docs pages.

The docs are the authority on what a model actually costs inside a Console
tier: the catalog carries one provider list price with no notion of tier,
while Zen and Go bill different rates for the same model (DeepSeek V4 Pro is
$1.74 on Zen, $0.66 off-peak on Go).
"""

import tempfile
import unittest
from pathlib import Path

from opencode_rank.pricing import (
    PriceEntry,
    load_prices,
    parse_pricing,
    refresh_prices,
    weighted_cost,
)

ENDPOINTS = """
<table>
<thead><tr><th>Model</th><th>Model ID</th><th>Endpoint</th></tr></thead>
<tbody>
<tr><td>DeepSeek V4 Pro</td><td>deepseek-v4-pro</td><td>https://x/v1</td></tr>
<tr><td>Step 5 Preview Free</td><td>step-5-preview-free</td><td>https://x/v1</td></tr>
<tr><td>Mistral Large 4</td><td>mistral-large-4</td><td>https://x/v1</td></tr>
</tbody>
</table>
"""

PRICING = """
<table>
<thead><tr><th>Model</th><th>Input</th><th>Output</th><th>Cached Read</th><th>Monthly limit</th></tr></thead>
<tbody>
<tr><td>DeepSeek V4 Pro (Off-Peak)</td><td>$0.66</td><td>$1.98</td><td>$0.022</td><td><strong>$15</strong></td></tr>
<tr><td>DeepSeek V4 Pro (Peak)</td><td>$1.32</td><td>$3.96</td><td>$0.044</td><td><strong>$15</strong></td></tr>
<tr><td>Step 5 Preview Free</td><td>Free</td><td>Free</td><td>Free</td><td><strong>Unlimited</strong></td></tr>
<tr><td>Mistral Large 4 (50% off)</td><td><del>$1.36</del> $0.68</td><td><del>$4.18</del> $2.09</td><td><del>$0.14</del> $0.07</td><td><strong>$60</strong></td></tr>
</tbody>
</table>
"""


class ParsePricingTest(unittest.TestCase):
    def setUp(self):
        self.entries = parse_pricing(ENDPOINTS + PRICING, tier="go")

    def test_each_priced_variant_is_its_own_entry(self):
        off_peak = [e for e in self.entries if "Off-Peak" in e.name]
        peak = [e for e in self.entries if "(Peak)" in e.name]
        self.assertEqual(len(off_peak), 1)
        self.assertEqual(len(peak), 1)
        # one model, two line entries carrying different rates
        self.assertEqual(off_peak[0].model_id, "deepseek-v4-pro")
        self.assertEqual(peak[0].model_id, "deepseek-v4-pro")
        self.assertEqual(off_peak[0].input, 0.66)
        self.assertEqual(peak[0].input, 1.32)
        self.assertEqual(off_peak[0].output, 1.98)

    def test_free_row_is_flagged_and_costs_nothing(self):
        free = [e for e in self.entries if e.model_id == "step-5-preview-free"]
        self.assertEqual(len(free), 1)
        self.assertTrue(free[0].free)
        self.assertEqual(free[0].input, 0.0)
        self.assertEqual(free[0].output, 0.0)

    def test_discount_takes_the_price_behind_the_strikethrough(self):
        mistral = [e for e in self.entries if e.model_id == "mistral-large-4"]
        self.assertEqual(len(mistral), 1)
        self.assertEqual(mistral[0].input, 0.68)
        self.assertEqual(mistral[0].output, 2.09)

    def test_monthly_limit_is_parsed_per_entry(self):
        by_name = {e.name: e for e in self.entries}
        self.assertEqual(by_name["DeepSeek V4 Pro (Off-Peak)"].limit, 15.0)
        self.assertEqual(by_name["Step 5 Preview Free"].limit, None)  # "Unlimited"

    def test_a_repeated_pricing_table_is_counted_once(self):
        # the Go page renders its table once per plan tab (Go / Go Plus) —
        # token prices are identical, so the second copy must not double rows
        entries = parse_pricing(ENDPOINTS + PRICING + PRICING, tier="go")
        self.assertEqual(len(entries), len(self.entries))

    def test_plan_tabs_collapse_to_one_row_with_the_best_limit(self):
        # same prices under both plans, but Go Plus raises the monthly limit
        plus = PRICING.replace("<strong>$60</strong>", "<strong>$180</strong>")
        entries = parse_pricing(ENDPOINTS + PRICING + plus, tier="go")
        mistral = [e for e in entries if e.model_id == "mistral-large-4"]
        self.assertEqual(len(mistral), 1)  # one line, not one per plan
        self.assertEqual(mistral[0].limit, 180.0)  # keep the bigger allowance

    def test_rows_without_an_endpoint_id_are_kept_unresolved(self):
        orphan = parse_pricing(
            "<table><tr><th>Model</th><th>Input</th><th>Output</th></tr>"
            "<tr><td>Mystery</td><td>$1</td><td>$2</td></tr></table>",
            tier="zen",
        )
        self.assertEqual(orphan[0].model_id, None)
        self.assertEqual(orphan[0].input, 1.0)


class WeightedCostTest(unittest.TestCase):
    """Representative cost: 20% input, 60% cached read, 20% output."""

    def test_mixes_the_three_token_classes_by_weight(self):
        # 0.2·1.0 + 0.6·0.10 + 0.2·2.0 = 0.2 + 0.06 + 0.4
        self.assertAlmostEqual(weighted_cost(1.0, 2.0, 0.10), 0.66)

    def test_absent_cache_discount_is_billed_at_the_input_rate(self):
        # no cached price published → every cache read costs a full input token
        # 0.2·1.0 + 0.6·1.0 + 0.2·2.0 = 1.2
        self.assertAlmostEqual(weighted_cost(1.0, 2.0, None), 1.2)

    def test_absent_output_price_renormalises_the_remaining_weights(self):
        # only input+cached remain (0.8 of the mass): (0.2·1.0 + 0.6·0) / 0.8
        self.assertAlmostEqual(weighted_cost(1.0, None, 0.0), 0.25)

    def test_free_entry_costs_nothing(self):
        self.assertEqual(weighted_cost(0.0, 0.0, 0.0), 0.0)


class SnapshotTest(unittest.TestCase):
    def _pages(self, url):
        return ENDPOINTS + PRICING

    def test_refresh_writes_a_snapshot_that_load_reads_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = refresh_prices(Path(tmp), get=self._pages)
            self.assertEqual(sorted(saved), ["go_prices.json", "zen_prices.json"])
            loaded = load_prices(Path(tmp))
        self.assertEqual(sorted(loaded), ["go", "zen"])
        entry = [e for e in loaded["go"] if e.model_id == "deepseek-v4-pro"
                 and "Off-Peak" in e.name][0]
        self.assertEqual(entry.input, 0.66)
        self.assertEqual(entry.limit, 15.0)
        self.assertEqual(entry.tier, "go")

    def test_a_page_we_cannot_parse_does_not_clobber_a_good_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            refresh_prices(directory, get=lambda url: ENDPOINTS + PRICING)
            before = (directory / "go_prices.json").read_text()
            written = refresh_prices(directory, get=lambda url: "<html>moved</html>")
            self.assertEqual(written, [])                       # nothing new
            self.assertEqual((directory / "go_prices.json").read_text(), before)

    def test_missing_snapshot_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_prices(Path(tmp)), {})

    def test_snapshot_roundtrips_as_price_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            refresh_prices(Path(tmp), get=self._pages)
            entries = load_prices(Path(tmp))["zen"]
        self.assertTrue(all(isinstance(e, PriceEntry) for e in entries))


if __name__ == "__main__":
    unittest.main()


class FreeTierNominalTest(unittest.TestCase):
    """Free models cost the console nothing per token but are not free to
    serve, and they are not equally expensive: Step-5-Free burns ~70x what
    Muse-Spark-Contributor-Free does per request (LIMITS.md). The nominal
    rates below turn that into a price the list view can divide by, so free
    tiers get different score/$ instead of sharing one."""

    def test_the_most_expensive_free_model_costs_most(self):
        from opencode_rank.pricing import nominal_free_rate
        self.assertGreater(nominal_free_rate("stepfun/step-5-preview"),
                           nominal_free_rate("meta/muse-spark-1.3-contributor"))

    def test_a_free_suffix_reaches_the_same_model(self):
        from opencode_rank.pricing import nominal_free_rate
        self.assertEqual(nominal_free_rate("step-5-preview-free"),
                         nominal_free_rate("stepfun/step-5-preview"))

    def test_every_nominal_rate_stays_below_the_cheapest_paid_rate(self):
        # free must stay cheaper than paid on average, or the list view
        # stops preferring it: cheapest paid is zen jev-1.13 at $0.034/1M
        from opencode_rank.pricing import (FREE_TIER_NOMINAL_RATES,
                                           nominal_free_rate)
        for slug in list(FREE_TIER_NOMINAL_RATES) + ["something-new-free"]:
            with self.subTest(slug=slug):
                self.assertLess(nominal_free_rate(slug), 0.034)

    def test_an_unknown_free_model_gets_the_anchor_rate(self):
        from opencode_rank.pricing import (FREE_TIER_DEFAULT_RATE,
                                           nominal_free_rate)
        self.assertEqual(nominal_free_rate("lab/mystery-free"),
                         FREE_TIER_DEFAULT_RATE)
