"""Unified snapshot refresh: envelope, schema keys, and writer behavior.

Parsing, merging, and validation live with the per-tier suites
(`test_go_pricing`, `test_zen_pricing`); these tests pin what `refresh`
writes: the decided envelope and the per-model record shape.
"""

import json
import tempfile
import unittest
from pathlib import Path

from opencode_models import go_pricing, zen_pricing
from tests import test_go_pricing as go_fixture
from tests import test_zen_pricing as zen_fixture

ENVELOPE_KEYS = ("source", "fetched_at", "schema_rev", "page_sha256", "plans")
RECORD_KEYS = (
    "name",
    "free",
    "tiers",
    "limits",
    "requests_per_month",
    "assumptions",
)


class GoRefreshTest(unittest.TestCase):
    def test_refresh_writes_unified_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = go_pricing.refresh(
                Path(tmp), get=lambda url: go_fixture.FIXTURE_HTML
            )
            self.assertEqual(written, ["go.json"])
            doc = json.loads((Path(tmp) / "plans" / "go.json").read_text())
            for key in ENVELOPE_KEYS:
                self.assertIn(key, doc["meta"])
            self.assertEqual(doc["meta"]["schema_rev"], 1)
            self.assertEqual(doc["meta"]["plans"], ["go", "go_plus"])
            self.assertIn("model-a", doc["models"])
            for key in RECORD_KEYS:
                self.assertIn(key, doc["models"]["model-a"])

    def test_rerun_preserves_first_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            go_pricing.refresh(Path(tmp), get=lambda url: go_fixture.FIXTURE_HTML)
            path = Path(tmp) / "plans" / "go.json"
            doc = json.loads(path.read_text())
            doc["models"]["model-a"]["first_seen"] = "2026-01-01T00:00:00+00:00"
            path.write_text(json.dumps(doc))
            go_pricing.refresh(Path(tmp), get=lambda url: go_fixture.FIXTURE_HTML)
            again = json.loads(path.read_text())
            self.assertEqual(
                again["models"]["model-a"]["first_seen"], "2026-01-01T00:00:00+00:00"
            )


class ZenRefreshTest(unittest.TestCase):
    def test_refresh_writes_unified_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = zen_pricing.refresh(
                Path(tmp), get=lambda url: zen_fixture.FIXTURE_HTML
            )
            self.assertEqual(written, ["zen.json"])
            doc = json.loads((Path(tmp) / "plans" / "zen.json").read_text())
            for key in ENVELOPE_KEYS:
                self.assertIn(key, doc["meta"])
            self.assertIn("model-a", doc["models"])


if __name__ == "__main__":
    unittest.main()
