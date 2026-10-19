"""Live availability from the Console config API.

``GET /api/v2/config`` (service-account key) is the workspace's live model
set, per provider — the same plans the docs describe, but as the API serves
them right now rather than as the pricing pages last said.
"""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from opencode_rank.live import LIVE_TIERS, fetch_live, load_live, save_live


PAYLOAD = {
    "providers": {
        "opencode": {"models": {"alpha": {"cost": []}, "beta-free": {"cost": []}}},
        "opencode-go": {"models": {"alpha": {"cost": []}}},
        "other": {"models": {"zzz": {}}},
    }
}


class FetchLiveTest(unittest.TestCase):
    def test_providers_map_to_tiers_and_unknown_ones_are_ignored(self):
        self.assertEqual(fetch_live(lambda url: json.dumps(PAYLOAD)),
                         {"zen": {"alpha", "beta-free"}, "go": {"alpha"}})

    def test_a_provider_without_models_counts_as_empty_not_missing(self):
        payload = {"providers": {"opencode": {}, "opencode-go": {"models": {}}}}
        self.assertEqual(fetch_live(lambda url: json.dumps(payload)),
                         {"zen": set(), "go": set()})

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            save_live(d, {"zen": {"alpha"}})
            self.assertEqual(load_live(d), {"zen": {"alpha"}, "go": None})

    def test_no_snapshot_is_unknown_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_live(Path(tmp)),
                             {"zen": None, "go": None})


if __name__ == "__main__":
    unittest.main()
