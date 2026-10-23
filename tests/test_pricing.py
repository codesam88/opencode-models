"""Tier price snapshots scraped from the OpenCode Zen/Go docs pages.

The docs are the authority on what a model actually costs inside a Console
tier: the catalog carries one provider list price with no notion of tier,
while Zen and Go bill different rates for the same model (DeepSeek V4 Pro is
$1.74 on Zen, $0.66 off-peak on Go).
"""

import unittest

from opencode_models.pricing import (
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
