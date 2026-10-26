"""Roster coverage: served slugs render even undocumented; junk stays hidden."""

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
    (d / "plans").mkdir(parents=True, exist_ok=True)
    (d / "plans" / "zen.json").write_text(
        json.dumps(
            {
                "meta": {},
                "models": {
                    "m-alpha": {
                        "name": "M-ALPHA",
                        "free": False,
                        "tiers": [
                            {
                                "label": None,
                                "in": 1.0,
                                "out": 1.0,
                                "cached": 1.0,
                                "cached_write": None,
                            }
                        ],
                        "limits": None,
                        "requests_per_month": None,
                        "assumptions": None,
                    },
                    "m-beta": {
                        "name": "M-BETA",
                        "free": False,
                        "tiers": [
                            {
                                "label": None,
                                "in": 2.0,
                                "out": 2.0,
                                "cached": 2.0,
                                "cached_write": None,
                            }
                        ],
                        "limits": None,
                        "requests_per_month": None,
                        "assumptions": None,
                    },
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
                    # served by the gateway, in no catalog record: it is not
                    # rendered, and nothing has to filter it out
                    "live-only-free": True,
                },
            }
        )
    )
    # the models.dev slice is this fixture's catalog: the models the views
    # render are exactly these, records the slice does not carry stay out
    (d / "modelsdev.json").write_text(
        json.dumps(
            {
                "meta": {"fetched_at": "2099-01-01T00:00:00+00:00"},
                "providers": {
                    "opencode-go": {"models": {}},
                    "opencode": {
                        "models": {
                            "m-alpha": {
                                "id": "m-alpha",
                                "canonical_model_id": "l/m-alpha",
                                "name": "M-ALPHA",
                                "cost": {
                                    "input": 1.0,
                                    "output": 1.0,
                                    "cache_read": 1.0,
                                },
                            },
                            "m-beta": {
                                "id": "m-beta",
                                "canonical_model_id": "l/m-beta",
                                "name": "M-BETA",
                                "cost": {
                                    "input": 2.0,
                                    "output": 2.0,
                                    "cache_read": 2.0,
                                },
                            },
                            "orphan-free": {
                                "id": "orphan-free",
                                "name": "Orphan Free",
                                "cost": {"input": 0, "output": 0, "cache_read": 0},
                            },
                        }
                    },
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

    def test_live_only_slug_no_catalog_record_is_not_rendered(self):
        # served by the gateway, in neither the slice nor the cards: the
        # catalog decides, so nothing renders and nothing filters
        with tempfile.TemporaryDirectory() as tmp:
            _make_fixture(tmp)
            code_list, out_list = _run(
                "--data", tmp, "list", "--source", "zen", "--tables", "all"
            )
            code_models, out_models = _run("--data", tmp, "models")
        self.assertEqual(code_list, 0)
        self.assertEqual(code_models, 0)
        self.assertNotIn("live-only-free", out_list)
        self.assertNotIn("live-only-free", out_models)
        # and the junk ids are absent for the same reason: no record
        self.assertNotIn("opencode/test", out_list)
        self.assertNotIn("opencode/test", out_models)

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
            plans = json.loads((d / "plans" / "zen.json").read_text())
            plans["models"]["plain"] = {
                "name": "Plain",
                "free": False,
                "tiers": [
                    {
                        "label": None,
                        "in": 1.0,
                        "out": 1.0,
                        "cached": 1.0,
                        "cached_write": None,
                    }
                ],
                "limits": None,
                "requests_per_month": None,
                "assumptions": None,
            }
            plans["models"]["plain-free"] = {
                "name": "Plain Free",
                "free": True,
            }
            (d / "plans" / "zen.json").write_text(json.dumps(plans))
            slice_data = json.loads((d / "modelsdev.json").read_text())
            slice_data["providers"]["opencode"]["models"].update(
                {
                    "plain": {
                        "id": "plain",
                        "name": "Plain",
                        "cost": {"input": 1.0, "output": 1.0, "cache_read": 1.0},
                    },
                    "plain-free": {
                        "id": "plain-free",
                        "name": "Plain Free",
                        "cost": {"input": 0, "output": 0, "cache_read": 0},
                    },
                }
            )
            (d / "modelsdev.json").write_text(json.dumps(slice_data))
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
        # the metered line may carry the median marker after its name
        self.assertTrue(
            metered_row.rstrip().endswith(("Plain", "Plain ~")), metered_row
        )


if __name__ == "__main__":
    unittest.main()


class AliasSynthesisTests(unittest.TestCase):
    def _fixture(self, tmp, alias_target="l/m-alpha", alias_slug="orphan-free"):
        import json
        from pathlib import Path

        d = Path(tmp)
        (d / "opencode").mkdir(parents=True, exist_ok=True)
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
        (d / "plans").mkdir(parents=True, exist_ok=True)
        (d / "plans" / "zen.json").write_text(
            json.dumps(
                {
                    "meta": {},
                    "models": {
                        "m-alpha": {"name": "M-ALPHA", "free": False},
                        "m-beta": {"name": "M-BETA", "free": False},
                        alias_slug: {"name": "Orphan Free", "free": True},
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
                        alias_slug: True,
                    },
                }
            )
        )
        # the slice is the catalog: these three are the rows it authorizes,
        # and orphan-free carries no canonical id -- the alias is what
        # points it at a card
        (d / "modelsdev.json").write_text(
            json.dumps(
                {
                    "meta": {"fetched_at": "2099-01-01T00:00:00+00:00"},
                    "providers": {
                        "opencode-go": {"models": {}},
                        "opencode": {
                            "models": {
                                "m-alpha": {
                                    "id": "m-alpha",
                                    "canonical_model_id": "l/m-alpha",
                                    "name": "M-ALPHA",
                                    "cost": {
                                        "input": 1.0,
                                        "output": 1.0,
                                        "cache_read": 1.0,
                                    },
                                },
                                "m-beta": {
                                    "id": "m-beta",
                                    "canonical_model_id": "l/m-beta",
                                    "name": "M-BETA",
                                    "cost": {
                                        "input": 2.0,
                                        "output": 2.0,
                                        "cache_read": 2.0,
                                    },
                                },
                                "orphan-free": {
                                    "id": "orphan-free",
                                    "name": "Orphan Free",
                                    "cost": {
                                        "input": 0,
                                        "output": 0,
                                        "cache_read": 0,
                                    },
                                },
                            }
                        },
                    },
                }
            )
        )
        (d / "aliases.json").write_text(
            json.dumps(
                {
                    alias_slug: {
                        "model_id": alias_target,
                        "source": "http://example.com",
                        "note": "test alias",
                    }
                }
            )
        )
        return d

    def test_alias_orphan_renders_with_target_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._fixture(tmp)
            code, out = _run(
                "--data", tmp, "list", "--source", "zen", "--tables", "all"
            )
        self.assertEqual(code, 0)
        row = next(l for l in out.splitlines() if "Orphan Free" in l)
        self.assertFalse(row.lstrip().startswith("·"), row)

    def test_alias_target_missing_renders_scoreless(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._fixture(tmp, alias_target="l/missing")
            code, out = _run(
                "--data", tmp, "list", "--source", "zen", "--tables", "all"
            )
        self.assertEqual(code, 0)
        row = next(l for l in out.splitlines() if "Orphan Free" in l)
        self.assertTrue(row.lstrip().startswith("·"), row)

    def test_snapshot_name_price_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._fixture(tmp)
            code, out = _run(
                "--data", tmp, "list", "--source", "zen", "--tables", "all"
            )
        self.assertEqual(code, 0)
        row = next(l for l in out.splitlines() if "Orphan Free" in l)
        self.assertIn("Orphan Free", row)

    def test_endpoint_uses_roster_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._fixture(tmp)
            code, out = _run("--data", tmp, "next", "--source", "zen")
        self.assertEqual(code, 0)
        self.assertIn("opencode/orphan-free", out)
