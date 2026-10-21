"""Live availability from the Console config API.

``GET /api/v2/config`` (service-account key) is the workspace's live model
set, per provider — the same plans the docs describe, but as the API serves
them right now rather than as the pricing pages last said. Each model carries
its enabled/disabled flag through the snapshot, so the console can show
status even offline.
"""

import json
import tempfile
import unittest
from pathlib import Path

from opencode_models.live import fetch_live, load_live, save_live

PAYLOAD = {
    "providers": {
        "opencode": {
            "models": {
                "alpha": {"cost": [], "disabled": True},
                "beta-free": {"cost": []},
            }
        },
        "opencode-go": {"models": {"alpha": {"cost": [], "disabled": True}}},
        "other": {"models": {"zzz": {}}},
    }
}


class FetchLiveTest(unittest.TestCase):
    def test_providers_map_to_tiers_with_disabled_status(self):
        self.assertEqual(
            fetch_live(lambda url: json.dumps(PAYLOAD)),
            {"zen": {"alpha": True, "beta-free": False}, "go": {"alpha": True}},
        )

    def test_a_provider_without_models_counts_as_empty_not_missing(self):
        payload = {"providers": {"opencode": {}, "opencode-go": {"models": {}}}}
        self.assertEqual(
            fetch_live(lambda url: json.dumps(payload)), {"zen": {}, "go": {}}
        )

    def test_save_and_load_round_trip_keeps_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            save_live(d, {"zen": {"alpha": True, "beta": False}})
            self.assertEqual(
                load_live(d), {"zen": {"alpha": True, "beta": False}, "go": None}
            )

    def test_a_pre_status_snapshot_loads_as_unknown(self):
        # files written before the flag existed stored a bare slug list;
        # their status is unknown, not invented
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            out = d / "available"
            out.mkdir(parents=True)
            (out / "zen.json").write_text(
                json.dumps({"meta": {}, "models": ["old-model"]})
            )
            self.assertEqual(load_live(d), {"zen": {"old-model": None}, "go": None})

    def test_no_snapshot_is_unknown_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_live(Path(tmp)), {"zen": None, "go": None})


if __name__ == "__main__":
    unittest.main()


class FetchServedTest(unittest.TestCase):
    def _get(self, payload):
        from opencode_models.live import SERVED_URLS

        def get(url):
            assert url in SERVED_URLS.values(), url
            return json.dumps(payload(url))

        return get

    def test_served_lists_are_parsed_per_tier_from_data_ids(self):
        from opencode_models.live import fetch_served

        def payload(url):
            ids = (
                ["m-one", "m-two"]
                if url.endswith("/v1/models") and "/go/" not in url
                else ["g-one"]
            )
            return {"data": [{"id": i, "object": "model"} for i in ids]}

        self.assertEqual(
            fetch_served(self._get(payload)),
            {"zen": {"m-one", "m-two"}, "go": {"g-one"}},
        )

    def test_malformed_entries_are_skipped(self):
        from opencode_models.live import fetch_served

        def payload(url):
            return {"data": [{"id": "ok"}, {"no": "id"}, "bare", None, {"id": ""}]}

        self.assertEqual(
            fetch_served(self._get(payload)), {"zen": {"ok"}, "go": {"ok"}}
        )
