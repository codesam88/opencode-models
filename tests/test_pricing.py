"""Representative per-token cost: the fixed 20/60/20 blend."""

import unittest

from opencode_models.pricing import (
    weighted_cost,
)


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
