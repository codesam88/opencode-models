"""`auth` verification separated from prompting.

`verify_key` is the pure decision — key in, verified counts or a
rejection reason out — with the transport injectable, so it is
testable without a terminal, a saved key, or the network. Prompting,
saving, and printing stay in `run`.
"""

import unittest
from unittest import mock

from opencode_models.commands import auth as auth_cmd
from opencode_models.commands.auth import verify_key


class VerifyKeyTest(unittest.TestCase):
    def test_verified_counts_models_and_tiers(self):
        with (
            mock.patch.object(
                auth_cmd,
                "fetch_served",
                return_value={"zen": {"m1", "m2"}, "go": set()},
            ),
            mock.patch.object(
                auth_cmd, "fetch_live", return_value={"zen": {}, "go": {}}
            ),
        ):
            result = verify_key("oc_sk_test", get=object())
        self.assertTrue(result.ok)
        self.assertEqual(result.model_count, 2)
        self.assertEqual(result.tier_count, 1)
        self.assertIsNone(result.error)

    def test_rejection_carries_the_reason(self):
        with (
            mock.patch.object(
                auth_cmd, "fetch_served", side_effect=OSError("unreachable")
            ),
            mock.patch.object(auth_cmd, "fetch_live", return_value={}),
        ):
            result = verify_key("oc_sk_test", get=object())
        self.assertFalse(result.ok)
        self.assertIn("unreachable", result.error)

    def test_empty_served_set_verifies_zero(self):
        with (
            mock.patch.object(auth_cmd, "fetch_served", return_value={}),
            mock.patch.object(auth_cmd, "fetch_live", return_value={}),
        ):
            result = verify_key("oc_sk_test", get=object())
        self.assertTrue(result.ok)
        self.assertEqual(result.model_count, 0)
        self.assertEqual(result.tier_count, 0)


if __name__ == "__main__":
    unittest.main()
