"""Roster coverage: docs-documented orphans render; junk stays hidden."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from opencode_models.cli import main

os.environ.pop("OPENCODE_API_KEY", None)
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="opencode-models-test-home-")


def _run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


def _make_fixture(tmp):
    d = Path(tmp)
    (d / "opencode").mkdir(parents=True, exist_ok=True)
    # two scored catalog models so scales and `next` have something to work on
    for mid, price, tb, swe in (
        ("l/m-alpha", 1.0, 90.0, 80.0),
        ("l/m-beta", 2.0, 70.0, 60.0),
    ):
        (d / "opencode" / f"{mid.replace('/', '__')}.json").write_text(
            json.dumps(
                {
                    "model": {
                        "id": mid,
                        "name": mid.split("/")[1].upper(),
                        "lab": "l",
                        "priceUsdPer1M": {
                            "input": price,
                            "output": price,
                            "cachedInput": price,
                        },
                        "benchmarks": [
                            {
                                "name": "Terminal-Bench",
                                "version": "2.1",
                                "score": tb,
                                "metric": "acc",
                                "source": "",
                            },
                            {
                                "name": "SWE-Bench",
                                "version": "Verified",
                                "score": swe,
                                "metric": "acc",
                                "source": "",
                            },
                        ],
                    }
                }
            )
        )
    (d / "tier_prices").mkdir(parents=True, exist_ok=True)
    (d / "tier_prices" / "zen_prices.json").write_text(
        json.dumps(
            [
                {
                    "tier": "zen",
                    "model_id": "m-alpha",
                    "name": "M-ALPHA",
                    "input": 1.0,
                    "output": 1.0,
                    "cached_read": 1.0,
                    "limit": None,
                    "free": False,
                },
                {
                    "tier": "zen",
                    "model_id": "m-beta",
                    "name": "M-BETA",
                    "input": 2.0,
                    "output": 2.0,
                    "cached_read": 2.0,
                    "limit": None,
                    "free": False,
                },
                {
                    "tier": "zen",
                    "model_id": "orphan-free",
                    "name": "Orphan Free",
                    "input": 0.0,
                    "output": 0.0,
                    "cached_read": 0.0,
                    "limit": None,
                    "free": True,
                },
            ]
        )
    )
    (d / "plans").mkdir(parents=True, exist_ok=True)
    (d / "plans" / "zen.json").write_text(
        json.dumps(
            {
                "meta": {},
                "models": {
                    "m-alpha": {"name": "M-ALPHA", "free": False},
                    "m-beta": {"name": "M-BETA", "free": False},
                    "orphan-free": {"name": "Orphan Free", "free": True},
                },
            }
        )
    )
    (d / "available").mkdir(parents=True, exist_ok=True)
    (d / "available" / "zen.json").write_text(
        json.dumps(
            {
                "meta": {},
                "models": {
                    "m-alpha": True,
                    "m-beta": True,
                    "orphan-free": True,
                    "test": True,
                },
            }
        )
    )
    return d


class ModelsRosterCoverageTest(unittest.TestCase):
    def test_docs_documented_orphan_renders_in_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_fixture(tmp)
            code, out = _run(
                "--data", tmp, "list", "--source", "zen", "--tables", "all"
            )
        self.assertEqual(code, 0)
        self.assertIn("Orphan Free", out)
        # no catalog record: unknown benchmarks, shown as insufficient data
        row = next(l for l in out.splitlines() if "Orphan Free" in l)
        self.assertTrue(row.lstrip().startswith("·"), row)

    def test_live_only_junk_slug_renders_nowhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_fixture(tmp)
            code_list, out_list = _run(
                "--data", tmp, "list", "--source", "zen", "--tables", "all"
            )
            code_models, out_models = _run("--data", tmp, "models")
            code_next, out_next = _run("--data", tmp, "next", "--source", "zen")
        self.assertNotIn("opencode/test", out_list)
        self.assertNotIn("opencode/test", out_models)
        self.assertNotIn("opencode/test", out_next)

    def test_models_shows_documented_orphan_by_snapshot_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_fixture(tmp)
            code, out = _run("--data", tmp, "models")
        self.assertEqual(code, 0)
        self.assertIn("Orphan Free", out)
        self.assertIn("opencode/orphan-free", out)

    def test_next_excludes_scoreless_rows_without_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_fixture(tmp)
            code, out = _run("--data", tmp, "next", "--source", "zen")
        self.assertNotIn("Traceback", out)
        self.assertNotIn("orphan-free", out)

    def test_base_and_free_variant_render_as_separate_rows(self):
        # the docs list both jev-1.13 and jev-1.13-free with no catalog
        # record either way: distinct documented offerings, one row each.
        import json

        with tempfile.TemporaryDirectory() as tmp:
            _make_fixture(tmp)
            d = Path(tmp)
            prices = json.loads((d / "tier_prices" / "zen_prices.json").read_text())
            prices.append(
                {
                    "tier": "zen",
                    "model_id": "plain",
                    "name": "Plain",
                    "input": 1.0,
                    "output": 1.0,
                    "cached_read": 1.0,
                    "limit": None,
                    "free": False,
                }
            )
            prices.append(
                {
                    "tier": "zen",
                    "model_id": "plain-free",
                    "name": "Plain Free",
                    "input": 0.0,
                    "output": 0.0,
                    "cached_read": 0.0,
                    "limit": None,
                    "free": True,
                }
            )
            (d / "tier_prices" / "zen_prices.json").write_text(json.dumps(prices))
            plans = json.loads((d / "plans" / "zen.json").read_text())
            plans["models"]["plain"] = {"name": "Plain", "free": False}
            plans["models"]["plain-free"] = {
                "name": "Plain Free",
                "free": True,
            }
            (d / "plans" / "zen.json").write_text(json.dumps(plans))
            available = json.loads((d / "available" / "zen.json").read_text())
            available["models"]["plain"] = True
            available["models"]["plain-free"] = True
            (d / "available" / "zen.json").write_text(json.dumps(available))
            code, out = _run(
                "--data", tmp, "list", "--source", "zen", "--tables", "all"
            )
        self.assertEqual(code, 0)
        rows = [l for l in out.splitlines() if "Plain" in l]
        self.assertEqual(len(rows), 2, rows)
        free_row = next(l for l in rows if "Plain Free" in l)
        metered_row = next(l for l in rows if "Plain Free" not in l)
        self.assertTrue(free_row.lstrip().startswith("·"), free_row)
        self.assertTrue(metered_row.rstrip().endswith("Plain"), metered_row)


if __name__ == "__main__":
    unittest.main()
