"""Snapshot generation for the Zen plan pricing page, offline in tests.

The script parses the Endpoints + Pricing + Deprecated tables from the
OpenCode Zen docs page, maps display names to slugs via the Endpoints
table, and merges into a JSON snapshot. Merge is extend-not-replace: new
slugs are added, changed records updated, page-missing slugs kept with
`dropped: true`. Zen has no allowance windows and no monthly caps, so
limits/requests_per_month/assumptions are all None. Tests use a small
frozen HTML fixture mirroring the real page structure; no test touches
the network.
"""

import json
import tempfile
import unittest
from pathlib import Path

from opencode_models import zen_pricing

FIXTURE_HTML = """<!DOCTYPE html>
<html><body>
<h2>Endpoints</h2>
<table><thead><tr><th>Model</th><th>Model ID</th><th>Endpoint</th><th>AI SDK Package</th></tr></thead>
<tbody>
<tr><td>Model A</td><td><code>model-a</code></td><td><code>https://example/model-a</code></td><td><code>@ai-sdk/x</code></td></tr>
<tr><td>Model B</td><td><code>model-b</code></td><td><code>https://example/model-b</code></td><td><code>@ai-sdk/x</code></td></tr>
<tr><td>Free Model</td><td><code>free-model</code></td><td><code>https://example/free-model</code></td><td><code>@ai-sdk/x</code></td></tr>
<tr><td>Discounted Model</td><td><code>discounted-model</code></td><td><code>https://example/discounted</code></td><td><code>@ai-sdk/x</code></td></tr>
</tbody></table>

<h2>Pricing</h2>
<table><thead><tr><th>Model</th><th>Input</th><th>Output</th><th>Cached Read</th><th>Cached Write</th></tr></thead>
<tbody>
<tr><td>Model A</td><td>$0.15</td><td>$0.50</td><td>$0.03</td><td>-</td></tr>
<tr><td>Model B (&le; 256K tokens)</td><td>$0.40</td><td>$1.60</td><td>$0.04</td><td>$0.50</td></tr>
<tr><td>Model B (&gt; 256K tokens)</td><td>$1.20</td><td>$4.80</td><td>$0.12</td><td>$1.50</td></tr>
<tr><td>Free Model</td><td>Free</td><td>Free</td><td>Free</td><td>-</td></tr>
<tr><td>Discounted Model</td><td><del>$1.36</del> $0.68</td><td><del>$4.18</del> $2.09</td><td><del>$0.14</del> $0.07</td><td>-</td></tr>
</tbody></table>

<h2>Deprecated</h2>
<table><thead><tr><th>Model</th><th>Deprecation date</th></tr></thead>
<tbody>
<tr><td>Model A</td><td>July 23, 2026</td></tr>
<tr><td>Model Legacy</td><td>August 5, 2026</td></tr>
</tbody></table>
</body></html>
"""


def parse_fixture():
    return zen_pricing.parse_page(FIXTURE_HTML)


class ParseTest(unittest.TestCase):
    def test_parses_priced_model(self):
        models, warnings = parse_fixture()
        self.assertEqual(
            sorted(models),
            ["discounted-model", "free-model", "model-a", "model-b"],
        )
        self.assertEqual(
            models["model-a"],
            {
                "name": "Model A",
                "free": False,
                "tiers": [
                    {
                        "label": None,
                        "in": 0.15,
                        "out": 0.5,
                        "cached": 0.03,
                        "cached_write": None,
                    }
                ],
                "limits": None,
                "requests_per_month": None,
                "assumptions": None,
                "dropped": False,
                "first_seen": None,
                "deprecated": True,
                "deprecation_date": "July 23, 2026",
                "endpoint": "https://example/model-a",
                "sdk_package": "@ai-sdk/x",
                "notes": None,
            },
        )

    def test_tiered_rows_collapse_to_one_slug(self):
        models, _ = parse_fixture()
        b = models["model-b"]
        self.assertEqual(b["name"], "Model B")
        self.assertEqual(len(b["tiers"]), 2)
        self.assertEqual(b["tiers"][0]["label"], "≤ 256K tokens")
        self.assertEqual(b["tiers"][0]["in"], 0.4)
        self.assertEqual(b["tiers"][1]["label"], "> 256K tokens")
        self.assertEqual(b["tiers"][1]["cached_write"], 1.5)

    def test_free_model(self):
        models, _ = parse_fixture()
        free = models["free-model"]
        self.assertTrue(free["free"])
        self.assertEqual(free["tiers"], [])
        self.assertIsNone(free["limits"])
        self.assertIsNone(free["requests_per_month"])
        self.assertIsNone(free["assumptions"])

    def test_strikethrough_keeps_current_values(self):
        models, _ = parse_fixture()
        d = models["discounted-model"]
        self.assertEqual(d["tiers"][0]["in"], 0.68)
        self.assertEqual(d["tiers"][0]["out"], 2.09)
        self.assertEqual(d["tiers"][0]["cached"], 0.07)

    def test_deprecated_slug_still_priced_warns(self):
        models, warnings = parse_fixture()
        self.assertTrue(
            any("model-a" in w and "deprecat" in w.lower() for w in warnings)
        )

    def test_unmapped_model_name_fails_parse(self):
        broken = FIXTURE_HTML.replace(
            "<td>Model B</td><td><code>model-b</code></td>",
            "<td>Model B</td><td><code>model-b</code></td>".replace(
                "Model B", "Model Z", 1
            ),
        )
        # simpler: break pricing name
        broken = FIXTURE_HTML.replace(
            "<tr><td>Model A</td><td>$0.15</td>",
            "<tr><td>Model Z</td><td>$0.15</td>",
        )
        with self.assertRaises(ValueError):
            zen_pricing.parse_page(broken)


class _Legacy:
    pass


def _legacy_models():
    old_a = {
        "name": "Model A",
        "free": False,
        "tiers": [
            {
                "label": None,
                "in": 0.2,
                "out": 0.5,
                "cached": 0.03,
                "cached_write": None,
            }
        ],
        "limits": None,
        "requests_per_month": None,
        "assumptions": None,
    }
    legacy = {
        "name": "Model Legacy",
        "free": False,
        "tiers": [
            {
                "label": None,
                "in": 1.0,
                "out": 3.0,
                "cached": 0.1,
                "cached_write": None,
            }
        ],
        "limits": None,
        "requests_per_month": None,
        "assumptions": None,
    }
    return {"model-a": old_a, "model-legacy": legacy}


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "zen.json"
        self.path.write_text(json.dumps({"models": _legacy_models()}))

    def _existing(self):
        return json.loads(self.path.read_text())["models"]

    def test_update_add_and_keep_dropped_never_delete(self):
        parsed, _ = parse_fixture()
        merged, counts, warnings = zen_pricing.merge(self._existing(), parsed)
        self.assertEqual(counts, {"added": 3, "updated": 1, "dropped": 1})
        self.assertEqual(merged["model-a"]["tiers"][0]["in"], 0.15)
        self.assertIn("model-b", merged)
        self.assertIn("free-model", merged)
        kept = merged["model-legacy"]
        self.assertTrue(kept["dropped"])
        self.assertEqual(kept["name"], "Model Legacy")
        self.assertEqual(len(merged), 5)
        self.assertTrue(any("model-legacy" in w for w in warnings))

    def test_rerun_is_idempotent(self):
        parsed, _ = parse_fixture()
        once, _, _ = zen_pricing.merge(self._existing(), parsed)
        twice, counts, warnings = zen_pricing.merge(once, parsed)
        self.assertEqual(counts, {"added": 0, "updated": 0, "dropped": 0})
        self.assertEqual(twice, once)
        self.assertTrue(any("model-legacy" in w for w in warnings))

    def test_returning_dropped_slug_clears_flag(self):
        parsed, _ = parse_fixture()
        once, _, _ = zen_pricing.merge(self._existing(), parsed)
        self.assertTrue(once["model-legacy"]["dropped"])
        parsed["model-legacy"] = _legacy_models()["model-legacy"]
        twice, counts, _ = zen_pricing.merge(once, parsed)
        self.assertNotIn("dropped", twice["model-legacy"])
        self.assertEqual(counts, {"added": 0, "updated": 0, "dropped": 0})


class GateTest(unittest.TestCase):
    def test_gate_passes_on_good_data(self):
        models, _ = parse_fixture()
        errors, _, _ = zen_pricing.validate(models)
        self.assertEqual(errors, [])

    def test_gate_fails_on_corrupted_limits(self):
        models, _ = parse_fixture()
        models["model-a"]["limits"] = 600
        errors, _, _ = zen_pricing.validate(models)
        self.assertTrue(any("model-a" in e for e in errors))


class ShippedGateTest(unittest.TestCase):
    def test_shipped_fallback_passes_the_gate(self):
        fallback = (
            Path(__file__).resolve().parent.parent
            / "opencode_models"
            / "data"
            / "plans"
            / "zen.json"
        )
        models = zen_pricing._read_models(fallback)
        errors, _, _ = zen_pricing.validate(models)
        self.assertEqual(errors, [], errors)

    def test_user_agent_is_derived_from_package_metadata(self):
        from unittest import mock

        with mock.patch.object(zen_pricing, "version", return_value="9.9.9"):
            self.assertEqual(zen_pricing._user_agent(), "opencode-models/9.9.9")
        self.assertTrue(zen_pricing.USER_AGENT.startswith("opencode-models/"))


if __name__ == "__main__":
    unittest.main()
