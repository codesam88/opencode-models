"""Pareto frontier over (price, score): cheap-and-strong models survive."""

import unittest

from modelpareto.pareto import frontier


def _m(model_id, price, score):
    """Minimal Model-shaped object for frontier input."""
    from modelpareto.data import Model

    return Model(
        model_id=model_id,
        name=model_id,
        lab="x",
        price_input=price,
        price_output=None,
        price_cached_input=None,
        scores={} if score is None else {"B": score},
    )


class FrontierTest(unittest.TestCase):
    BENCH = "B"

    def test_empty_input(self):
        self.assertEqual(frontier([], self.BENCH), [])

    def test_dominated_model_is_excluded(self):
        # c costs more and scores lower than a → dominated
        a = _m("a", 1.0, 80.0)
        c = _m("c", 5.0, 60.0)
        self.assertEqual(frontier([a, c], self.BENCH), [a])

    def test_both_extremes_survive(self):
        # a is cheapest, b scores highest — neither dominates the other
        a = _m("a", 1.0, 60.0)
        b = _m("b", 10.0, 90.0)
        self.assertEqual(frontier([a, b], self.BENCH), [a, b])

    def test_models_without_price_are_excluded(self):
        self.assertEqual(frontier([_m("np", None, 99.0)], self.BENCH), [])

    def test_models_without_score_are_excluded(self):
        self.assertEqual(frontier([_m("ns", 1.0, None)], self.BENCH), [])

    def test_zero_price_is_kept_not_divided_by(self):
        # free models must not blow up ratio math downstream
        free = _m("free", 0.0, 50.0)
        self.assertEqual(frontier([free], self.BENCH), [free])

    def test_result_sorted_by_price_ascending(self):
        a = _m("a", 10.0, 95.0)
        b = _m("b", 1.0, 60.0)
        c = _m("c", 3.0, 80.0)
        out = frontier([a, b, c], self.BENCH)
        self.assertEqual([m.model_id for m in out], ["b", "c", "a"])

    def test_only_models_with_the_named_benchmark_compete(self):
        on_bench = _m("ob", 5.0, 50.0)
        other = _m("other", 1.0, None)
        out = frontier([on_bench, other], self.BENCH)
        self.assertEqual(out, [on_bench])

    def test_exact_price_and_score_duplicate_is_dominated(self):
        # equal in both dimensions → the first occurrence wins
        a = _m("a", 2.0, 70.0)
        dup = _m("dup", 2.0, 70.0)
        self.assertEqual(frontier([a, dup], self.BENCH), [a])


if __name__ == "__main__":
    unittest.main()
