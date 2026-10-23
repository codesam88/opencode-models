"""Unified pricing snapshot: package parsers match the scripts' behavior.

The package `go_pricing`/`zen_pricing` modules are ports of
`scripts/update_go_pricing.py` / `update_zen_pricing.py`. These tests pin
the port faithful on the frozen page fixtures: same parsed models, same
merge/validate behavior, plus the unified envelope `refresh` writes.
"""

import json
import tempfile
import unittest
from pathlib import Path

from opencode_models import go_pricing, zen_pricing
from tests import test_go_pricing as go_script_tests
from tests import test_zen_pricing as zen_script_tests

ENRICHED = {
    "deprecated",
    "deprecation_date",
    "dropped",
    "endpoint",
    "first_seen",
    "notes",
    "sdk_package",
}


def _page_content(models):
    return {
        slug: {k: v for k, v in rec.items() if k not in ENRICHED}
        for slug, rec in models.items()
    }


class GoPortParityTest(unittest.TestCase):
    def test_parse_matches_script_on_frozen_fixture(self):
        script_models, _ = go_script_tests.parse_fixture()
        package_models, _ = go_pricing.parse_page(go_script_tests.FIXTURE_HTML)
        self.assertEqual(_page_content(package_models), script_models)
        # page-derived enrichment the snapshot schema adds on top
        self.assertEqual(
            package_models["model-a"]["endpoint"], "https://example/model-a"
        )
        self.assertEqual(package_models["model-a"]["sdk_package"], "@ai-sdk/x")
        self.assertFalse(package_models["model-a"]["dropped"])

    def test_merge_matches_script(self):
        script_models, _ = go_script_tests.parse_fixture()
        package_models, _ = go_pricing.parse_page(go_script_tests.FIXTURE_HTML)
        existing = {"model-a": dict(script_models["model-a"], limits={"go": 1})}
        script_merged, _, _ = go_script_tests.go_pricing.merge(
            dict(existing), script_models
        )
        package_merged, _, _ = go_pricing.merge(dict(existing), package_models)
        self.assertEqual(_page_content(package_merged), _page_content(script_merged))

    def test_refresh_writes_unified_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = go_pricing.refresh(
                Path(tmp), get=lambda url: go_script_tests.FIXTURE_HTML
            )
            self.assertEqual(written, ["go.json"])
            doc = json.loads((Path(tmp) / "plans" / "go.json").read_text())
            for key in (
                "source",
                "fetched_at",
                "schema_rev",
                "page_sha256",
                "plans",
            ):
                self.assertIn(key, doc["meta"])
            self.assertEqual(doc["meta"]["schema_rev"], 1)
            self.assertIn("model-a", doc["models"])
            rec = doc["models"]["model-a"]
            for key in (
                "name",
                "free",
                "tiers",
                "limits",
                "requests_per_month",
                "assumptions",
            ):
                self.assertIn(key, rec)


class ZenPortParityTest(unittest.TestCase):
    def test_parse_matches_script_on_frozen_fixture(self):
        script_models, _ = zen_script_tests.parse_fixture()
        package_models, _ = zen_pricing.parse_page(zen_script_tests.FIXTURE_HTML)
        self.assertEqual(_page_content(package_models), script_models)

    def test_refresh_writes_unified_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = zen_pricing.refresh(
                Path(tmp), get=lambda url: zen_script_tests.FIXTURE_HTML
            )
            self.assertEqual(written, ["zen.json"])
            doc = json.loads((Path(tmp) / "plans" / "zen.json").read_text())
            self.assertIn("schema_rev", doc["meta"])
            self.assertIn("model-a", doc["models"])


if __name__ == "__main__":
    unittest.main()
