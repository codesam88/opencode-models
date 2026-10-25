"""Snapshot generation for the Go plan limits page, offline in tests.

The script parses the two plan tabs (pricing + published request estimates),
maps display names to slugs via the Endpoints table, and merges into a
committed JSON snapshot. Merge is extend-not-replace: new slugs are added,
changed records updated, page-missing slugs kept with `dropped: true`.
A validation gate reproduces the page's own monthly request estimates from
limits + prices + token assumptions (10% tolerance) and must fail shut.
Tests use a small frozen HTML fixture mirroring the real page structure;
no test touches the network.
"""

import json
import tempfile
import unittest
from pathlib import Path

from opencode_models import go_pricing

# Frozen fixture mirrors the live page's structure: two plan tabpanels
# (data-plan-panel="go" / "go-plus"), each holding one pricing table and
# one requests table; an assumptions <ul> with an em-dash bullet list
# (including a shared "Model B/Model C" bullet); an Endpoints table for
# slug mapping. Deliberate quirks copied from the real page: tiered rows
# whose labels carry a parenthetical suffix but share one monthly limit,
# a free row ("Free" prices, "Unlimited" limit + small badge), and a
# "2x limited time" row whose current values sit in <strong> next to
# struck-through originals in <del>.
FIXTURE_HTML = """<!DOCTYPE html>
<html><body>
<h2 id="usage-limits">Usage limits</h2>
<div role="tabpanel" id="go-pricing-panel-go" aria-labelledby="go-pricing-tab-go" data-plan-panel="go">
<table><thead><tr><th>Model</th><th>Input</th><th>Output</th><th>Cached Read</th><th>Cached Write</th><th>Monthly limit</th></tr></thead>
<tbody>
<tr><td>Model A</td><td>$0.15</td><td>$0.50</td><td>$0.03</td><td>-</td><td><strong>$60</strong></td></tr>
<tr><td>Model B (&le; 256K tokens)</td><td>$0.40</td><td>$1.60</td><td>$0.04</td><td>$0.50</td><td><strong>$60</strong></td></tr>
<tr><td>Model B (&gt; 256K tokens)</td><td>$1.20</td><td>$4.80</td><td>$0.12</td><td>$1.50</td><td><strong>$60</strong></td></tr>
<tr><td>Model C</td><td>$1.00</td><td>$2.70</td><td>$0.10</td><td>-</td><td><strong>$15</strong></td></tr>
<tr><td>Free Model</td><td>Free</td><td>Free</td><td>Free</td><td>-</td><td><strong>Unlimited</strong><br/><small>limited time</small></td></tr>
<tr><td>Gemini 4 Argon Contributor</td><td>$4.00</td><td>$20.00</td><td>$0.20</td><td>-</td><td><del>$30</del> <strong>$60</strong><br/><small>2x &middot; Limited time</small></td></tr>
</tbody></table>
</div>
<div role="tabpanel" id="go-pricing-panel-go-plus" aria-labelledby="go-pricing-tab-go-plus" hidden data-plan-panel="go-plus">
<table><thead><tr><th>Model</th><th>Input</th><th>Output</th><th>Cached Read</th><th>Cached Write</th><th>Monthly limit</th></tr></thead>
<tbody>
<tr><td>Model A</td><td>$0.15</td><td>$0.50</td><td>$0.03</td><td>-</td><td><strong>$180</strong></td></tr>
<tr><td>Model B (&le; 256K tokens)</td><td>$0.40</td><td>$1.60</td><td>$0.04</td><td>$0.50</td><td><strong>$180</strong></td></tr>
<tr><td>Model B (&gt; 256K tokens)</td><td>$1.20</td><td>$4.80</td><td>$0.12</td><td>$1.50</td><td><strong>$180</strong></td></tr>
<tr><td>Model C</td><td>$1.00</td><td>$2.70</td><td>$0.10</td><td>-</td><td><strong>$60</strong></td></tr>
<tr><td>Free Model</td><td>Free</td><td>Free</td><td>Free</td><td>-</td><td><strong>Unlimited</strong><br/><small>limited time</small></td></tr>
<tr><td>Gemini 4 Argon Contributor</td><td>$4.00</td><td>$20.00</td><td>$0.20</td><td>-</td><td><del>$120</del> <strong>$240</strong><br/><small>2x &middot; Limited time</small></td></tr>
</tbody></table>
</div>
<h3 id="estimated-requests">Estimated requests</h3>
<div role="tabpanel" id="go-requests-panel-go" aria-labelledby="go-requests-tab-go" data-plan-panel="go">
<table><thead><tr><th>Model</th><th>Requests per 5 hours</th><th>Requests per week</th><th>Requests per month</th></tr></thead>
<tbody>
<tr><td>Model A</td><td>6,320</td><td>15,790</td><td>31,580</td></tr>
<tr><td>Model B</td><td>4,300</td><td>10,800</td><td>21,600</td></tr>
<tr><td>Model C</td><td>1,890</td><td>4,730</td><td>2,410</td></tr>
<tr><td>Free Model</td><td>Unlimited</td><td>Unlimited</td><td>Unlimited</td></tr>
<tr><td>Gemini 4 Argon Contributor<br/><small>2x &middot; Limited time</small></td><td><del>220</del><br/><strong>440</strong></td><td><del>540</del><br/><strong>1,080</strong></td><td><del>1,060</del><br/><strong>2,120</strong></td></tr>
</tbody></table>
</div>
<div role="tabpanel" id="go-requests-panel-go-plus" aria-labelledby="go-requests-tab-go-plus" hidden data-plan-panel="go-plus">
<table><thead><tr><th>Model</th><th>Requests per 5 hours</th><th>Requests per week</th><th>Requests per month</th></tr></thead>
<tbody>
<tr><td>Model A</td><td>18,960</td><td>47,370</td><td>94,740</td></tr>
<tr><td>Model B</td><td>12,900</td><td>32,400</td><td>64,800</td></tr>
<tr><td>Model C</td><td>3,860</td><td>4,820</td><td>9,660</td></tr>
<tr><td>Free Model</td><td>Unlimited</td><td>Unlimited</td><td>Unlimited</td></tr>
<tr><td>Gemini 4 Argon Contributor<br/><small>2x &middot; Limited time</small></td><td><del>880</del><br/><strong>1,760</strong></td><td><del>2,160</del><br/><strong>4,320</strong></td><td><del>4,240</del><br/><strong>8,480</strong></td></tr>
</tbody></table>
</div>
<p>The estimates use the following token counts per request; actual usage varies.</p>
<ul>
<li>Model A &mdash; 1,000 input, 55,000 cached, 200 output tokens per request</li>
<li>Model B/Model C &mdash; 500 input, 57,000 cached, 190 output tokens per request</li>
</ul>
<h2 id="endpoints">Endpoints</h2>
<p>You can also access Go models through the following API endpoints.</p>
<table><thead><tr><th>Model</th><th>Model ID</th><th>Endpoint</th><th>AI SDK Package</th></tr></thead>
<tbody>
<tr><td>Model A</td><td><code>model-a</code></td><td><code>https://example/model-a</code></td><td><code>@ai-sdk/x</code></td></tr>
<tr><td>Model B</td><td><code>model-b</code></td><td><code>https://example/model-b</code></td><td><code>@ai-sdk/x</code></td></tr>
<tr><td>Model C</td><td><code>model-c</code></td><td><code>https://example/model-c</code></td><td><code>@ai-sdk/x</code></td></tr>
<tr><td>Free Model</td><td><code>free-model</code></td><td><code>https://example/free-model</code></td><td><code>@ai-sdk/x</code></td></tr>
<tr><td>Gemini 4 Argon Contributor</td><td><code>gemini-4-argon-contributor</code></td><td><code>https://example/model-d</code></td><td><code>@ai-sdk/x</code></td></tr>
</tbody></table>
</body></html>
"""


def parse_fixture():
    return go_pricing.parse_page(FIXTURE_HTML)


class ParseTest(unittest.TestCase):
    def test_parses_priced_model(self):
        models, _ = parse_fixture()
        self.assertEqual(
            sorted(models),
            [
                "free-model",
                "gemini-4-argon-contributor",
                "model-a",
                "model-b",
                "model-c",
            ],
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
                    }
                ],
                "limits": {"go": 60, "go_plus": 180},
                "requests_per_month": {"go": 31580, "go_plus": 94740},
                "assumptions": {"in": 1000, "cached": 55000, "out": 200},
                "dropped": False,
                "first_seen": None,
            },
        )

    def test_tiered_rows_collapse_to_one_slug_with_shared_limit(self):
        models, _ = parse_fixture()
        b = models["model-b"]
        self.assertEqual(b["name"], "Model B")
        self.assertEqual(len(b["tiers"]), 2)
        self.assertEqual(b["tiers"][0]["label"], "≤ 256K tokens")
        self.assertEqual(b["tiers"][0]["in"], 0.4)
        self.assertEqual(b["tiers"][1]["label"], "> 256K tokens")
        self.assertEqual(b["tiers"][1]["cached"], 0.12)
        self.assertEqual(b["limits"], {"go": 60, "go_plus": 180})
        self.assertEqual(b["requests_per_month"], {"go": 21600, "go_plus": 64800})

    def test_shared_assumption_bullet_applies_to_both_models(self):
        models, _ = parse_fixture()
        self.assertEqual(
            models["model-c"]["assumptions"], models["model-b"]["assumptions"]
        )
        self.assertEqual(
            models["model-c"]["tiers"],
            [
                {
                    "label": None,
                    "in": 1.0,
                    "out": 2.7,
                    "cached": 0.1,
                }
            ],
        )

    def test_abbreviated_slash_bullet_expands_from_first_name(self):
        # The live page writes "GLM-5.3/5.2 - ..." where only the first
        # entry is a full display name; the suffix glues onto its last
        # segment.
        html = (
            FIXTURE_HTML.replace("Model B/Model C &mdash;", "Model B/B-C &mdash;")
            .replace("<td><code>model-c</code></td>", "<td><code>model-b-c</code></td>")
            .replace("<tr><td>Model C</td>", "<tr><td>Model B-C</td>")
        )
        models, _ = go_pricing.parse_page(html)
        self.assertEqual(
            models["model-b-c"]["assumptions"], models["model-b"]["assumptions"]
        )

    def test_free_model_has_no_limits_or_estimates(self):
        models, _ = parse_fixture()
        free = models["free-model"]
        self.assertTrue(free["free"])
        self.assertEqual(free["tiers"], [])
        self.assertIsNone(free["limits"])
        self.assertEqual(free["requests_per_month"], {"go": None, "go_plus": None})
        self.assertIsNone(free["assumptions"])

    def test_strikethrough_keeps_current_values(self):
        # "2x limited time" rows show the superseded number in <del>; the
        # live value is the sibling <strong>.
        models, warnings = parse_fixture()
        d = models["gemini-4-argon-contributor"]
        self.assertEqual(d["limits"], {"go": 60, "go_plus": 240})
        self.assertEqual(d["requests_per_month"], {"go": 2120, "go_plus": 8480})
        self.assertIsNone(d["assumptions"])
        self.assertTrue(any("gemini-4-argon-contributor" in w for w in warnings))

    def test_unmapped_model_name_fails_parse(self):
        broken = FIXTURE_HTML.replace(
            "<td>Model C</td><td>$1.00</td>", "<td>Model Z</td><td>$1.00</td>"
        )
        with self.assertRaises(ValueError):
            go_pricing.parse_page(broken)


def _legacy_models():
    old_a = {
        "name": "Model A",
        "free": False,
        "tiers": [
            {
                "label": None,
                "in": 0.15,
                "out": 0.5,
                "cached": 0.03,
            }
        ],
        "limits": {"go": 30, "go_plus": 90},
        "requests_per_month": {"go": 15790, "go_plus": 47370},
        "assumptions": {"in": 1000, "cached": 55000, "out": 200},
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
            }
        ],
        "limits": {"go": 15, "go_plus": 60},
        "requests_per_month": {"go": 1000, "go_plus": 4000},
        "assumptions": {"in": 1000, "cached": 50000, "out": 200},
    }
    return {"model-a": old_a, "model-legacy": legacy}


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "go.json"
        self.path.write_text(json.dumps({"models": _legacy_models()}))

    def _existing(self):
        return json.loads(self.path.read_text())["models"]

    def test_update_add_and_keep_dropped_never_delete(self):
        parsed, _ = parse_fixture()
        merged, counts, warnings = go_pricing.merge(self._existing(), parsed)
        self.assertEqual(counts, {"added": 4, "updated": 1, "dropped": 1})
        # updated: model-a picked up the page's new limits
        self.assertEqual(merged["model-a"]["limits"], {"go": 60, "go_plus": 180})
        # added: everything new from the page
        self.assertIn("model-b", merged)
        self.assertIn("free-model", merged)
        # dropped: page-missing slug kept, flagged, warned — never deleted
        kept = merged["model-legacy"]
        self.assertTrue(kept["dropped"])
        self.assertEqual(kept["name"], "Model Legacy")
        self.assertEqual(len(merged), 6)
        self.assertTrue(any("model-legacy" in w for w in warnings))

    def test_rerun_is_idempotent(self):
        parsed, _ = parse_fixture()
        once, _, _ = go_pricing.merge(self._existing(), parsed)
        twice, counts, warnings = go_pricing.merge(once, parsed)
        self.assertEqual(counts, {"added": 0, "updated": 0, "dropped": 0})
        self.assertEqual(twice, once)
        self.assertTrue(any("model-legacy" in w for w in warnings))

    def test_returning_dropped_slug_clears_flag(self):
        parsed, _ = parse_fixture()
        once, _, _ = go_pricing.merge(self._existing(), parsed)
        self.assertTrue(once["model-legacy"]["dropped"])
        # the model reappears on the page with unchanged data: flag cleared
        parsed["model-legacy"] = _legacy_models()["model-legacy"]
        twice, counts, _ = go_pricing.merge(once, parsed)
        self.assertNotIn("dropped", twice["model-legacy"])
        # unchanged content returns without an update count; only the
        # stale flag goes away
        self.assertEqual(counts, {"added": 0, "updated": 0, "dropped": 0})


class GateTest(unittest.TestCase):
    def test_gate_passes_on_good_data(self):
        models, _ = parse_fixture()
        errors, _, _ = go_pricing.validate(models)
        self.assertEqual(errors, [])

    def test_gate_fails_on_corrupted_limit(self):
        models, _ = parse_fixture()
        models["model-a"]["limits"]["go"] = 600
        errors, _, _ = go_pricing.validate(models)
        self.assertTrue(any("model-a" in e for e in errors))

    def test_gate_fails_on_corrupted_published_estimate(self):
        models, _ = parse_fixture()
        models["model-b"]["requests_per_month"]["go_plus"] = 100
        errors, _, _ = go_pricing.validate(models)
        self.assertTrue(any("model-b" in e for e in errors))

    def test_known_deviation_exception_is_bounded(self):
        # kimi-k2.7-code: the page's own estimate (6,750) deviates 26% from
        # the math (docs/model-go-plan.md) — allowed by name, still bounded.
        kimi = {
            "name": "Kimi K2.7 Code",
            "free": False,
            "tiers": [
                {
                    "label": None,
                    "in": 0.95,
                    "out": 4.0,
                    "cached": 0.19,
                }
            ],
            "limits": {"go": 60, "go_plus": 180},
            "requests_per_month": {"go": 6750, "go_plus": 20250},
            "assumptions": {"in": 870, "cached": 55000, "out": 200},
        }
        errors, _, _ = go_pricing.validate({"kimi-k2.7-code": kimi})
        self.assertEqual(errors, [])
        # A deviation beyond the exception cap still fails.
        kimi["requests_per_month"]["go"] = 30000
        errors, _, _ = go_pricing.validate({"kimi-k2.7-code": kimi})
        self.assertTrue(any("kimi-k2.7-code" in e for e in errors))

    def test_priced_model_without_assumptions_warns_instead_of_failing(self):
        # without token assumptions reproduction is impossible by
        # definition, so it can never signal a broken parser: warn loudly,
        # keep the model with assumptions None, and let the refresh land.
        models, _ = parse_fixture()
        errors, warnings, _ = go_pricing.validate(models)
        self.assertEqual(errors, [])
        self.assertTrue(any("gemini-4-argon-contributor" in w for w in warnings))


class ShippedGateTest(unittest.TestCase):
    def test_shipped_fallback_passes_the_gate(self):
        # the in-wheel copy must always be valid: pin it, offline
        fallback = (
            Path(__file__).resolve().parent.parent
            / "opencode_models"
            / "data"
            / "plans"
            / "go.json"
        )
        models = go_pricing._read_models(fallback)
        errors, _, checked = go_pricing.validate(models)
        self.assertEqual(errors, [], errors)
        self.assertGreater(len(checked), 40)

    def test_user_agent_is_derived_from_package_metadata(self):
        from unittest import mock

        with mock.patch.object(go_pricing, "version", return_value="9.9.9"):
            self.assertEqual(go_pricing._user_agent(), "opencode-models/9.9.9")
        self.assertTrue(go_pricing.USER_AGENT.startswith("opencode-models/"))


if __name__ == "__main__":
    unittest.main()
