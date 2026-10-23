"""Tier membership matching between catalog models and offered slugs."""

import unittest

from opencode_models.availability import members
from opencode_models.models import Model


def _m(model_id, price, score, output=None):
    return Model(
        model_id=model_id,
        name=model_id,
        lab="x",
        price_input=price,
        price_output=output,
        price_cached_input=None,
        scores={} if score is None else {"TB": score},
    )


class MembershipTest(unittest.TestCase):
    def test_free_variant_slug_reaches_the_base_model_but_suffix_confers_no_free_flag(
        self,
    ):
        models = [_m("xiaomi/mimo-v2.6-flash", 0.16, 50.0), _m("lab/other", 1.0, 40.0)]
        got, free = members(models, {"mimo-v2.6-flash-free", "other"})
        # the tier page offers MiMo-V2.6-Flash Free; the catalog knows one
        # model under the base slug — it must not vanish from the list
        self.assertEqual(
            sorted(m.model_id for m in got), ["lab/other", "xiaomi/mimo-v2.6-flash"]
        )
        self.assertEqual(free, set())

    def test_plain_slug_member_is_not_free(self):
        _, free = members([_m("a/m1", 1.0, 50.0)], {"m1"})
        self.assertEqual(free, set())

    def test_zero_priced_model_is_free_whatever_the_slug(self):
        _, free = members([_m("a/m1", 0.0, 50.0)], {"m1"})
        self.assertEqual(free, {"a/m1"})

    def test_no_slugs_means_every_model_in_the_catalog(self):
        models = [_m("a/paid", 1.0, 50.0), _m("a/free", 0.0, 40.0)]
        got, free = members(models, None)
        self.assertEqual(got, models)
        self.assertEqual(free, {"a/free"})

    def test_exact_slug_wins_over_canonical_group(self):
        # base + variant both exist in the catalog: a base roster slug
        # reaches only the base record, a variant roster slug only the
        # variant record.
        models = [
            _m("lab/base", 1.0, 50.0),
            _m("lab/base-contributor", 2.0, 60.0),
        ]
        got, _ = members(models, {"base"})
        self.assertEqual([m.model_id for m in got], ["lab/base"])
        got, _ = members(models, {"base-contributor"})
        self.assertEqual([m.model_id for m in got], ["lab/base-contributor"])
        # a variant roster slug with no exact catalog record falls back to
        # the canonical base model.
        got, _ = members([_m("lab/base", 1.0, 50.0)], {"base-contributor"})
        self.assertEqual([m.model_id for m in got], ["lab/base"])


if __name__ == "__main__":
    unittest.main()
