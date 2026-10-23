"""Functional core for the Go plan allowance model, offline in tests.

Small hand-built fixtures (no network, no file reads): a plain priced
model, a tiered-variant model, a free model, and a priced model with
missing token assumptions, plus a deviant model for the estimate gate.
"""

import unittest

from opencode_models.allowance import (
    WINDOW_FRACTIONS,
    cost_per_request,
    credit_ranking,
    merge_snapshots,
    remaining,
    request_cap,
    validate_estimates,
    window_cap,
)

PLAIN = {
    "name": "Plain",
    "free": False,
    "tiers": [
        {"label": None, "in": 0.1, "out": 0.2, "cached": 0.002, "cached_write": None},
    ],
    "limits": {"go": 60, "go_plus": 120},
    "requests_per_month": {"go": 226600, "go_plus": 453200},
    "assumptions": {"in": 620, "cached": 71400, "out": 300},
}
# cpr = (620*0.1 + 71400*0.002 + 300*0.2) / 1e6 = 0.0002648

TIERED = {
    "name": "Tiered",
    "free": False,
    "tiers": [
        {
            "label": "<= 100K tokens",
            "in": 0.1,
            "out": 0.5,
            "cached": 0.01,
            "cached_write": 0.125,
        },
        {
            "label": "> 100K tokens",
            "in": 0.5,
            "out": 2.5,
            "cached": 0.05,
            "cached_write": 0.625,
        },
    ],
    "limits": {"go": 15, "go_plus": 60},
    "requests_per_month": {"go": 19230, "go_plus": 76920},
    "assumptions": {"in": 1100, "cached": 55000, "out": 240},
}
# base-tier cpr = (1100*0.1 + 55000*0.01 + 240*0.5) / 1e6 = 0.00078

FREE = {
    "name": "Freebie",
    "free": True,
    "tiers": [],
    "limits": {"go": None, "go_plus": None},
    "requests_per_month": {"go": None, "go_plus": None},
    "assumptions": None,
}

NO_ASSUMPTIONS = {
    "name": "No Assumptions",
    "free": False,
    "tiers": [
        {"label": None, "in": 4.0, "out": 20.0, "cached": 0.2, "cached_write": None},
    ],
    "limits": {"go": 60, "go_plus": 240},
    "requests_per_month": {"go": 2120, "go_plus": 8480},
    "assumptions": None,
}

DEVIANT = {
    "name": "Deviant",
    "free": False,
    "tiers": [
        {"label": None, "in": 0.95, "out": 4.0, "cached": 0.19, "cached_write": None},
    ],
    "limits": {"go": 60, "go_plus": 180},
    "requests_per_month": {"go": 6750, "go_plus": 20250},
    "assumptions": {"in": 870, "cached": 55000, "out": 200},
}
# cpr = (870*0.95 + 55000*0.19 + 200*4.0) / 1e6 = 0.0120765,
# computed = 60 / 0.0120765 ~= 4968 vs published 6750 -> ~26% deviation


class CostPerRequestTests(unittest.TestCase):
    def test_plain_model(self):
        self.assertAlmostEqual(
            cost_per_request(PLAIN["tiers"], PLAIN["assumptions"]), 0.0002648
        )

    def test_uses_base_tier(self):
        self.assertAlmostEqual(
            cost_per_request(TIERED["tiers"], TIERED["assumptions"]), 0.00078
        )

    def test_free_model_has_no_cost(self):
        self.assertIsNone(cost_per_request(FREE["tiers"], FREE["assumptions"]))

    def test_missing_assumptions_has_no_cost(self):
        self.assertIsNone(
            cost_per_request(NO_ASSUMPTIONS["tiers"], NO_ASSUMPTIONS["assumptions"])
        )

    def test_missing_cached_rate_bills_at_input_rate(self):
        # no cache discount: cached reads cost what input costs
        tiers = [{"label": None, "in": 1.0, "out": 5.0, "cached": None}]
        assumptions = {"in": 1000, "cached": 9000, "out": 500}
        self.assertAlmostEqual(
            cost_per_request(tiers, assumptions),
            (1000 * 1.0 + 9000 * 1.0 + 500 * 5.0) / 1e6,
        )


class CapTests(unittest.TestCase):
    def test_request_cap(self):
        cpr = cost_per_request(PLAIN["tiers"], PLAIN["assumptions"])
        self.assertAlmostEqual(request_cap(60, cpr), 60 / 0.0002648)

    def test_request_cap_without_cost_is_none(self):
        self.assertIsNone(request_cap(60, None))

    def test_window_fractions(self):
        self.assertEqual(
            WINDOW_FRACTIONS, {"rolling": 0.2, "weekly": 0.5, "monthly": 1.0}
        )
        cpr = cost_per_request(PLAIN["tiers"], PLAIN["assumptions"])
        monthly = request_cap(60, cpr)
        self.assertAlmostEqual(window_cap(60, cpr, "rolling"), 0.2 * monthly)
        self.assertAlmostEqual(window_cap(60, cpr, "weekly"), 0.5 * monthly)
        self.assertAlmostEqual(window_cap(60, cpr, "monthly"), monthly)

    def test_window_cap_unknown_window_raises(self):
        with self.assertRaises(ValueError):
            window_cap(60, 0.001, "daily")

    def test_window_cap_without_cost_is_none(self):
        self.assertIsNone(window_cap(60, None, "weekly"))


class RemainingTests(unittest.TestCase):
    def test_partial_spend(self):
        self.assertAlmostEqual(remaining(60, "weekly", 10.0), 20.0)

    def test_overspend_floors_at_zero(self):
        self.assertEqual(remaining(60, "rolling", 999.0), 0.0)

    def test_never_negative(self):
        self.assertGreaterEqual(remaining(15, "monthly", 15.0 + 1e-9), 0.0)


class CreditRankingTests(unittest.TestCase):
    def test_orders_by_cost_ascending_excluding_free_and_unknown(self):
        models = {
            "deviant": DEVIANT,
            "plain": PLAIN,
            "tiered": TIERED,
            "free": FREE,
            "unknown": NO_ASSUMPTIONS,
        }
        self.assertEqual(credit_ranking(models), ["plain", "tiered", "deviant"])


class ValidateEstimatesTests(unittest.TestCase):
    def test_matching_models_pass(self):
        records, passed = validate_estimates({"plain": PLAIN, "tiered": TIERED})
        self.assertTrue(passed)
        self.assertTrue(all(r["status"] == "ok" for r in records))

    def test_deviant_model_fails(self):
        records, passed = validate_estimates({"deviant": DEVIANT})
        self.assertFalse(passed)
        self.assertEqual(records[0]["status"], "failed")
        self.assertGreater(records[0]["deviation"], 0.10)

    def test_known_exception_does_not_fail(self):
        records, passed = validate_estimates(
            {"deviant": DEVIANT}, known_exceptions=frozenset({"deviant"})
        )
        self.assertTrue(passed)
        self.assertEqual(records[0]["status"], "excepted")

    def test_free_and_assumptionless_are_unchecked_never_failed(self):
        records, passed = validate_estimates({"free": FREE, "unknown": NO_ASSUMPTIONS})
        self.assertTrue(passed)
        self.assertTrue(all(r["status"] == "unchecked" for r in records))
        self.assertTrue(all(r["deviation"] is None for r in records))


class MergeSnapshotsTests(unittest.TestCase):
    def test_extend_not_replace(self):
        existing = {"plain": dict(PLAIN), "stale": dict(PLAIN)}
        updated_plain = dict(PLAIN)
        updated_plain["limits"] = {"go": 80, "go_plus": 160}
        parsed = {"plain": updated_plain, "fresh": dict(TIERED)}
        merged, counts, warnings = merge_snapshots(existing, parsed)
        self.assertEqual(merged["plain"]["limits"], {"go": 80, "go_plus": 160})
        self.assertIn("fresh", merged)
        self.assertTrue(merged["stale"]["dropped"])
        self.assertIn("stale", merged)  # never delete keys
        self.assertEqual(counts, {"added": 1, "updated": 1, "dropped": 1})
        self.assertEqual(len(warnings), 1)
        self.assertIn("stale", warnings[0])

    def test_unchanged_records_are_untouched(self):
        existing = {"plain": dict(PLAIN)}
        merged, counts, warnings = merge_snapshots(existing, {"plain": dict(PLAIN)})
        self.assertNotIn("dropped", merged["plain"])
        self.assertEqual(counts, {"added": 0, "updated": 0, "dropped": 0})
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
