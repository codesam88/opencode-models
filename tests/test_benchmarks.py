"""Which benchmarks exist across the dataset, and how well they cover it."""

import unittest

from opencode_rank.benchmarks import available
from opencode_rank.data import Model


def _m(model_id, scores):
    return Model(
        model_id=model_id,
        name=model_id,
        lab="x",
        price_input=1.0,
        price_output=None,
        price_cached_input=None,
        scores=scores,
    )


class AvailableTest(unittest.TestCase):
    def test_counts_models_per_benchmark(self):
        models = [
            _m("a", {"TB": 50.0, "SWE": 80.0}),
            _m("b", {"TB": 60.0}),
        ]
        self.assertEqual(available(models), [("TB", 2), ("SWE", 1)])

    def test_sorted_by_coverage_then_name(self):
        models = [
            _m("a", {"Z-Bench": 1.0, "TB": 1.0}),
            _m("b", {"TB": 1.0}),
            _m("c", {"TB": 1.0}),
        ]
        self.assertEqual(available(models), [("TB", 3), ("Z-Bench", 1)])

    def test_equal_coverage_breaks_tie_by_name(self):
        models = [_m("a", {"Z-Bench": 1.0, "Alpha": 1.0})]
        self.assertEqual(available(models), [("Alpha", 1), ("Z-Bench", 1)])

    def test_coverage_beats_alphabetical_order(self):
        # "Alpha" sorts first by name but has worse coverage — it must not win
        models = [
            _m("a", {"Alpha": 1.0}),
            _m("b", {"Beta": 1.0}),
            _m("c", {"Beta": 1.0}),
        ]
        self.assertEqual(available(models), [("Beta", 2), ("Alpha", 1)])

    def test_empty_dataset(self):
        self.assertEqual(available([]), [])


if __name__ == "__main__":
    unittest.main()
