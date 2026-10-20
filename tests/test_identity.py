"""One model, several plan designations.

``meta/muse-spark-1.3`` and ``meta/muse-spark-1.3-contributor`` are the same
weights offered on two plans at two prices: eleven identical benchmark
values, none differing, $1.25 in on Zen and $0.10 in on Go. The catalog keeps
them as two models, so the console showed two rows for one model — and worse,
two *different scores* (99 and 98), because the contributor record happened
to be missing a Terminal-Bench 4.0 run its twin carried. The gap was a
data-completeness artefact, not a difference in the model.

A designation is therefore not an identity: the benchmarks merge into the one
model, the score is computed once, and each plan's price stays its own line.
"""

import unittest

from opencode_models.data import Model
from opencode_models.identity import (
    MODEL_ALIASES,
    canonical_id,
    merge_aliases,
    plan_ids,
)


def _model(mid, price=1.25, **scores):
    return Model(
        model_id=mid,
        name=mid.split("/")[-1],
        lab=mid.split("/")[0],
        price_input=price,
        price_output=price,
        price_cached_input=price,
        scores=scores,
    )


class IdentityTest(unittest.TestCase):
    def test_a_contributor_designation_is_the_same_model(self):
        self.assertEqual(
            canonical_id("meta/muse-spark-1.3-contributor"), "meta/muse-spark-1.3"
        )
        self.assertEqual(canonical_id("meta/muse-spark-1.3"), "meta/muse-spark-1.3")

    def test_the_plans_of_a_model_include_its_designations(self):
        self.assertEqual(
            plan_ids("meta/muse-spark-1.3"),
            {"meta/muse-spark-1.3", "meta/muse-spark-1.3-contributor"},
        )

    def test_a_model_with_no_designation_is_its_own_plans(self):
        self.assertEqual(plan_ids("openai/gpt-6.1-sol"), {"openai/gpt-6.1-sol"})

    def test_one_model_survives_with_the_benchmarks_of_both(self):
        models = [
            _model(
                "meta/muse-spark-1.3",
                1.25,
                **{"Terminal-Bench 2.1": 88.8, "Terminal-Bench 4.0": 14.55},
            ),
            _model(
                "meta/muse-spark-1.3-contributor", 0.1, **{"Terminal-Bench 2.1": 88.8}
            ),
        ]
        merged = merge_aliases(models)
        self.assertEqual([m.model_id for m in merged], ["meta/muse-spark-1.3"])
        # the contributor record was missing the 4.0 run; the model had it
        self.assertEqual(merged[0].scores["Terminal-Bench 4.0"], 14.55)
        self.assertEqual(merged[0].scores["Terminal-Bench 2.1"], 88.8)

    def test_the_cheaper_price_wins_for_the_catalog_record(self):
        models = [
            _model("meta/muse-spark-1.3", 1.25, **{"Terminal-Bench 2.1": 88.8}),
            _model(
                "meta/muse-spark-1.3-contributor", 0.1, **{"Terminal-Bench 2.1": 88.8}
            ),
        ]
        merged = merge_aliases(models)
        # the documented tier prices replace this in the tables; when they do
        # not, the price someone can actually pay is the honest one
        self.assertEqual(merged[0].price_input, 0.1)

    def test_a_disagreement_is_the_plain_record_winning(self):
        models = [
            _model("meta/muse-spark-1.3", 1.25, **{"Terminal-Bench 2.1": 88.8}),
            _model(
                "meta/muse-spark-1.3-contributor", 0.1, **{"Terminal-Bench 2.1": 70.0}
            ),
        ]
        merged = merge_aliases(models)
        self.assertEqual(merged[0].scores["Terminal-Bench 2.1"], 88.8)

    def test_models_without_a_designation_are_untouched(self):
        models = [
            _model("openai/gpt-6.1-sol", 1.0, **{"Terminal-Bench 2.1": 58.0}),
            _model("xai/grok-4.7", 1.0, **{"Terminal-Bench 2.1": 30.0}),
        ]
        self.assertEqual(
            [m.model_id for m in merge_aliases(models)],
            ["openai/gpt-6.1-sol", "xai/grok-4.7"],
        )

    def test_a_designation_with_no_twin_keeps_its_own_identity(self):
        # renaming a model must not make it disappear
        models = [
            _model(
                "meta/muse-spark-1.4-contributor", 0.1, **{"Terminal-Bench 2.1": 90.0}
            )
        ]
        merged = merge_aliases(models)
        self.assertEqual(
            [m.model_id for m in merged], ["meta/muse-spark-1.4-contributor"]
        )

    def test_every_alias_points_at_a_model_we_can_justify(self):
        for alias, canonical in MODEL_ALIASES.items():
            with self.subTest(alias=alias):
                self.assertTrue(alias.endswith("-contributor"))
                self.assertEqual(canonical_id(canonical), canonical)


if __name__ == "__main__":
    unittest.main()
