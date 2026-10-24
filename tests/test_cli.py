"""CLI: fetch data, rank models."""

import contextlib
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import opencode_models.availability as availability
from opencode_models.cli import main

#: Fixture tests must be hermetic: a key in the environment — or in the
#: user config file — would send `list`/`next` to the live config API and
#: filter the fixtures away. Tests that need keyed behaviour set it
#: explicitly via patch.dict.
os.environ.pop("OPENCODE_API_KEY", None)
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="opencode-models-test-home-")


def _write_sample(data_dir: Path):
    import json

    def model_doc(mid, price, score):
        return json.dumps(
            {
                "model": {
                    "id": mid,
                    "name": mid.split("/")[1].upper(),
                    "lab": mid.split("/")[0],
                    "priceUsdPer1M": {
                        "input": price,
                        "output": None,
                        "cachedInput": None,
                    },
                    "benchmarks": (
                        []
                        if score is None
                        else [
                            {
                                "name": "TB",
                                "score": score,
                                "metric": "acc",
                                "source": "",
                            }
                        ]
                    ),
                }
            }
        )

    (_models(data_dir) / "a__m1.json").write_text(model_doc("a/m1", 1.0, 50.0))
    (_models(data_dir) / "b__m2.json").write_text(model_doc("b/m2", 3.0, 90.0))
    (_models(data_dir) / "c__m3.json").write_text(model_doc("c/m3", 9.0, 40.0))


def _models(d: Path) -> Path:
    """Model fixtures live under <data>/opencode/, like production data."""
    models = d / "opencode"
    models.mkdir(parents=True, exist_ok=True)
    return models


def _plan_doc(entries, tier):
    """PriceEntry-style rows -> unified plans snapshot document.

    Same slugs, rates, free flags and limits, in the `plans/<tier>.json`
    shape production readers consume.
    """
    models = {}
    for e in entries:
        slug = e["model_id"]
        label = None
        m = re.search(r"\(([^)]*)\)\s*$", e.get("name") or "")
        if m:
            label = m.group(1)
        rec = models.setdefault(
            slug,
            {
                "name": e.get("name") or slug,
                "free": False,
                "tiers": [],
                "limits": None,
                "requests_per_month": None,
                "assumptions": None,
            },
        )
        if e.get("free"):
            rec["free"] = True
        else:
            rec["tiers"].append(
                {
                    "label": label,
                    "in": e.get("input"),
                    "out": e.get("output"),
                    "cached": e.get("cached_read"),
                    "cached_write": None,
                }
            )
        if e.get("limit") is not None:
            if tier == "go":
                rec["limits"] = {"go": e["limit"], "go_plus": e["limit"]}
            else:
                rec["limits"] = {tier: e["limit"]}
    return {
        "meta": {
            "source": "test",
            "fetched_at": "2099-01-01T00:00:00+00:00",
            "schema_rev": 1,
            "plans": [tier],
        },
        "models": models,
    }


def _offline_get(url: str) -> str:
    """A transport that never leaves the machine, for `fetch` tests."""
    if url.endswith("/sitemap.xml"):
        return (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://opencode.ai/data/x/y</loc></url></urlset>"
        )
    return "{}"


def _write_grid(data_dir: Path):
    """Ten models: price i, Terminal-Bench score i² — value climbs with i.

    Two scored benchmarks each, because a model measured on one is not
    ranked at all (scoring.MIN_AXES) and every test here wants an ordering
    to talk about. The second axis is half the first, so the axes do not
    agree and a row cannot be read off either one.
    """
    import json

    for i in range(1, 11):
        doc = json.dumps(
            {
                "model": {
                    "id": f"l/m{i:02d}",
                    "name": f"M{i:02d}",
                    "lab": "l",
                    "priceUsdPer1M": {
                        "input": float(i),
                        "output": None,
                        "cachedInput": None,
                    },
                    "benchmarks": [
                        {
                            "name": "Terminal-Bench",
                            "version": "2.1",
                            "score": float(i * i),
                            "metric": "acc",
                            "source": "",
                        },
                        {
                            "name": "SWE-Bench",
                            "version": "Verified",
                            "score": float(i * i) / 2,
                            "metric": "acc",
                            "source": "",
                        },
                    ],
                }
            }
        )
        (_models(data_dir) / f"l__m{i:02d}.json").write_text(doc)


class CliTest(unittest.TestCase):
    def setUp(self):
        self._rp = __import__("unittest.mock", fromlist=["patch"]).patch(
            "opencode_models.cli.sync_mod.refresh_plan",
            return_value=("fresh", (), None),
        )
        self._rp.start()
        self.addCleanup(self._rp.stop)

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def test_fetch_sitemap_failure_warns_and_keeps_records(self):
        import opencode_models.cli as cli

        with tempfile.TemporaryDirectory() as tmp:
            models_dir = Path(tmp) / "opencode"
            models_dir.mkdir()
            kept = models_dir / "old__m.json"
            kept.write_text("{}")
            orig = cli.http_get

            def fake_get(url):
                if url.endswith("/sitemap.xml"):
                    raise OSError("sitemap down")
                return "{}"

            cli.http_get = fake_get
            try:
                with mock.patch.dict("os.environ"):
                    import os

                    os.environ.pop("OPENCODE_API_KEY", None)
                    code, out = self._run("--data", tmp, "fetch")
            finally:
                cli.http_get = orig
            self.assertEqual(code, 0)
            self.assertIn("keeping existing records", out)
            self.assertTrue(kept.exists())

    def test_fetch_uses_injected_fetcher_for_tests(self):
        calls = []

        def fake_get(url):
            calls.append(url)
            if url.endswith("/sitemap.xml"):
                return (
                    '<urlset xmlns="http://www.sitemaps.org/schemas/'
                    'sitemap/0.9"><url><loc>https://opencode.ai/data/'
                    "x/y</loc></url></urlset>"
                )
            return "{}"

        import opencode_models.cli as cli

        with tempfile.TemporaryDirectory() as tmp:
            orig = cli.http_get
            cli.http_get = fake_get
            try:
                with mock.patch.dict("os.environ"):
                    import os

                    os.environ.pop("OPENCODE_API_KEY", None)
                    code, _ = self._run("--data", tmp, "fetch")
            finally:
                cli.http_get = orig
            saved = (_models(Path(tmp)) / "x__y.json").exists()
        self.assertEqual(code, 0)
        self.assertTrue(saved)

    def test_fetch_snapshots_unified_pricing(self):
        import json

        from tests import test_go_pricing as go_fixture
        from tests import test_zen_pricing as zen_fixture

        sitemap = (
            '<urlset xmlns="http://www.sitemaps.org/schemas/'
            'sitemap/0.9"><url><loc>https://opencode.ai/data/'
            "x/y</loc></url></urlset>"
        )

        def fake_get(url):
            if url.endswith("/sitemap.xml"):
                return sitemap
            if "console/go" in url:
                return go_fixture.FIXTURE_HTML
            if url.endswith("/docs/zen/"):
                return zen_fixture.FIXTURE_HTML
            return "{}"

        import opencode_models.cli as cli

        with tempfile.TemporaryDirectory() as tmp:
            orig = cli.http_get
            cli.http_get = fake_get
            try:
                code, _ = self._run("--data", tmp, "fetch")
            finally:
                cli.http_get = orig
            go_models = json.loads((Path(tmp) / "plans" / "go.json").read_text())[
                "models"
            ]
            zen_models = json.loads((Path(tmp) / "plans" / "zen.json").read_text())[
                "models"
            ]
        self.assertEqual(code, 0)
        self.assertIn("model-a", go_models)
        self.assertIn("model-a", zen_models)

    def test_default_data_dir_honours_the_environment(self):
        import os

        import opencode_models.cli as cli

        with mock.patch.dict(os.environ, {"OPENCODE_MODELS_DATA": "/tmp/x"}):
            self.assertEqual(cli.default_data_dir(), Path("/tmp/x"))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                cli.default_data_dir(),
                Path(
                    os.environ.get(
                        "XDG_DATA_HOME", str(Path.home() / ".local" / "share")
                    )
                )
                / "opencode-models",
            )

    def test_missing_data_dir_is_a_clear_error(self):
        code, out = self._run("--data", "/nonexistent/xyz", "list")
        self.assertEqual(code, 1)
        self.assertIn("no data", out.lower())

    def test_scores_reports_nothing_cached_and_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertIn("0", out)  # no cached arena scores yet
        self.assertNotIn("failed", out.lower())

    def test_fetch_refreshes_the_arena_cache(self):
        import opencode_models.arena as arena

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_sample(d)
            import opencode_models.cli as cli

            orig = arena.lookup_arena_elo
            arena.lookup_arena_elo = lambda get: {"m1": 1500.0, "m2": 1400.0}
            transport = cli.http_get
            cli.http_get = _offline_get
            try:
                code, out = self._run("--data", str(d), "fetch")
            finally:
                arena.lookup_arena_elo = orig
                cli.http_get = transport
            from opencode_models.arena import ArenaCache

            cached = ArenaCache(d / "aiarena").load()
        self.assertEqual(code, 0)
        self.assertEqual(cached, {"m1": 1500.0, "m2": 1400.0})
        self.assertIn("arena", out.lower())

    def test_a_failed_arena_lookup_does_not_fail_the_fetch(self):
        import opencode_models.arena as arena

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_sample(d)
            orig = arena.lookup_arena_elo

            def boom(get):
                raise OSError("arena down")

            arena.lookup_arena_elo = boom
            import opencode_models.cli as cli

            transport = cli.http_get
            cli.http_get = _offline_get
            try:
                code, out = self._run("--data", str(d), "fetch")
            finally:
                arena.lookup_arena_elo = orig
                cli.http_get = transport
        self.assertEqual(code, 0)
        self.assertIn("arena", out.lower())
        self.assertIn("warning", out.lower())

    def test_scores_reads_the_cache_and_never_the_network(self):
        import opencode_models.arena as arena

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_sample(d)
            orig = arena.lookup_arena_elo

            def boom(get):
                raise AssertionError("scores must not reach the network")

            arena.lookup_arena_elo = boom
            try:
                code, _out = self._run("--data", str(d), "scores")
            finally:
                arena.lookup_arena_elo = orig
        self.assertEqual(code, 0)

    def test_list_source_sorted_by_score_per_price(self):
        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"m1", "m2", "m3"}
            try:
                code, out = self._run(
                    "--data",
                    tmp,
                    "list",
                    "--source",
                    "zen",
                    "--benchmark",
                    "TB",
                    "--tables",
                    "all",
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        # m1: 50/1=50, m2: 90/3=30, m3: 40/9≈4.4 — but the list now culls:
        # m2 stays in best value, m1 is the worst score, m3 the highest cost
        self.assertLess(out.index("M2"), out.index("M1"))
        self.assertLess(out.index("M1"), out.index("M3"))
        self.assertIn("best value", out)
        self.assertIn("worst score", out)
        self.assertIn("highest cost", out)

    def test_list_filters_to_source_members_only(self):
        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"a/m1"}
            try:
                code, out = self._run(
                    "--data",
                    tmp,
                    "list",
                    "--source",
                    "zen",
                    "--benchmark",
                    "TB",
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        self.assertIn("M1", out)
        self.assertNotIn("M2", out)

    def test_list_shows_score_cost_value_and_plan_columns(self):
        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"m1"}
            try:
                code, out = self._run(
                    "--data",
                    tmp,
                    "list",
                    "--source",
                    "zen",
                    "--benchmark",
                    "TB",
                    "--tables",
                    "all",
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        line = next(l for l in out.splitlines() if l.rstrip().endswith("M1"))
        # the calculated score, the benchmark tag, $1.00 as 100 cents
        self.assertRegex(line, r"\s50\s+TB\s+100\s+50\s+zen\s+M1$")

    def test_numbers_are_shown_as_rounded_integers(self):
        import json

        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "l__cheap.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "l/cheap",
                            "name": "CHEAP",
                            "lab": "l",
                            "priceUsdPer1M": {
                                "input": 0.0612,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 88.8,
                                    "metric": "acc",
                                    "source": "",
                                },
                                {
                                    "name": "SWE-Bench",
                                    "version": "Verified",
                                    "score": 88.8,
                                    "metric": "acc",
                                    "source": "",
                                },
                            ],
                        }
                    }
                )
            )
            # a peer, so Terminal-Bench has a range to read CHEAP's 88.8 on
            (_models(d) / "l__dear.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "l/dear",
                            "name": "DEAR",
                            "lab": "l",
                            "priceUsdPer1M": {
                                "input": 9.0,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 40.0,
                                    "metric": "acc",
                                    "source": "",
                                },
                                {
                                    "name": "SWE-Bench",
                                    "version": "Verified",
                                    "score": 40.0,
                                    "metric": "acc",
                                    "source": "",
                                },
                            ],
                        }
                    }
                )
            )
            # a third line, so the 30% cull leaves best value non-empty
            (_models(d) / "l__mid.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "l/mid",
                            "name": "MID",
                            "lab": "l",
                            "priceUsdPer1M": {
                                "input": 1.0,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 60.0,
                                    "metric": "acc",
                                    "source": "",
                                },
                                {
                                    "name": "SWE-Bench",
                                    "version": "Verified",
                                    "score": 60.0,
                                    "metric": "acc",
                                    "source": "",
                                },
                            ],
                        }
                    }
                )
            )
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"cheap", "dear", "mid"}
            try:
                code, out = self._run("--data", tmp, "list")
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        self.assertIn("¢/req", out)  # the column says what it counts
        line = next(l for l in out.splitlines() if l.rstrip().endswith("CHEAP"))
        # the calculated score (100, the best of the 40-88.8 range);
        # $0.0612 shows as 6 cents
        self.assertRegex(line, r"\s100\s+6\s+1634\s+go,zen\s+CHEAP$")
        self.assertNotIn("0.0612", line)  # cents, never dollars

    def test_list_output_price_uses_output_axis(self):
        import json

        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            doc = json.dumps(
                {
                    "model": {
                        "id": "a/m1",
                        "name": "M1",
                        "lab": "a",
                        "priceUsdPer1M": {
                            "input": 1.0,
                            "output": 5.0,
                            "cachedInput": None,
                        },
                        "benchmarks": [
                            {"name": "TB", "score": 50.0, "metric": "acc", "source": ""}
                        ],
                    }
                }
            )
            (_models(d) / "a__m1.json").write_text(doc)
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"m1"}
            try:
                code, out = self._run(
                    "--data",
                    tmp,
                    "list",
                    "--source",
                    "zen",
                    "--benchmark",
                    "TB",
                    "--cost",
                    "output",
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        # output price $5.00 shown as 500 cents; ratio 50/5 → 10
        line = next(l for l in out.splitlines() if l.rstrip().endswith("M1"))
        self.assertRegex(line, r"\s50\s+TB\s+500\s+10\s+zen\s+M1$")

    def test_list_unknown_source_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            code, out = self._run("--data", tmp, "list", "--source", "nope")
        self.assertEqual(code, 1)
        self.assertIn("zen", out)

    def test_next_prints_an_ordered_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run(
                "--data", tmp, "next", "--count", "3", "--source", "all"
            )
        self.assertEqual(code, 0)
        import re as _re

        self.assertRegex(
            out,
            _re.compile(
                r"^Next \d+ recommended models, >75% overall "
                r"score, free and paid picks interleaved, "
                r"alternating Zen/Go plans\.",
                _re.MULTILINE,
            ),
        )
        self.assertNotIn("LIMITS.md", out)
        self.assertIn("Endpoint", out)
        self.assertIn("Band", out)

    def test_next_lists_the_endpoint_not_the_display_name(self):
        from types import SimpleNamespace

        from opencode_models.cli import _endpoint
        from opencode_models.models import Model
        from opencode_models.ranking import Row

        def row(provider, mid, endpoint_id=None):
            model = Model(
                model_id=mid,
                name="Display Name",
                lab="l",
                price_input=1.0,
                price_output=1.0,
                price_cached_input=1.0,
                scores={},
            )
            return Row(
                model=model,
                name="Display Name",
                price=1.0,
                free=False,
                band="included",
                score=80.0,
                benchmark=None,
                provider=provider,
                grade="+",
                endpoint_id=endpoint_id,
            )

        # the plan-local id under a provider prefix, never the display name
        self.assertEqual(
            _endpoint(row("go", "stepfun/step-5-preview", "step-5-preview-free")),
            "opencode-go/step-5-preview-free",
        )
        self.assertEqual(
            _endpoint(row("zen", "meta/muse-spark-1.3", "muse-spark-1.3")),
            "opencode/muse-spark-1.3",
        )
        # both plans list it: Go wins the prefix, one line stays one line
        self.assertEqual(
            _endpoint(row("go,zen", "stepfun/step-5-preview", "step-5-preview-free")),
            "opencode-go/step-5-preview-free",
        )
        # no documented entry: the catalog slug still identifies the model
        self.assertEqual(_endpoint(row("-", "l/m1")), "opencode/m1")
        # legacy rows carrying a documented entry object resolve through it
        import dataclasses

        legacy = dataclasses.replace(
            row("go", "stepfun/step-5-preview"),
            entry=SimpleNamespace(model_id="step-5-preview-free"),
        )
        self.assertEqual(_endpoint(legacy), "opencode-go/step-5-preview-free")

    def test_next_endpoint_column_carries_no_display_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run(
                "--data", tmp, "next", "--count", "2", "--source", "all"
            )
        self.assertEqual(code, 0)
        for line in out.splitlines():
            if line.strip().startswith(("1", "2")):
                self.assertNotIn("M10", line)

    def test_next_keeps_the_billing_prose_out_of_the_output(self):
        # LIMITS.md is the place for what an allowance costs; the view must
        # not restate it on every run
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "next", "--source", "all")
        self.assertEqual(code, 0)
        for boilerplate in (
            "allowance runs out",
            "rate-limited",
            "costs nothing",
            "Zen balance",
        ):
            self.assertNotIn(boilerplate, out)

    def test_next_names_the_band_rather_than_a_sentence(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            (_models(Path(tmp)) / "l__zfree.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "l/zfree",
                            "name": "ZFREE",
                            "lab": "l",
                            "priceUsdPer1M": {
                                "input": 0.0,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 60.0,
                                    "metric": "acc",
                                    "source": "",
                                },
                                {
                                    "name": "SWE-Bench",
                                    "version": "Verified",
                                    "score": 60.0,
                                    "metric": "acc",
                                    "source": "",
                                },
                            ],
                        }
                    }
                )
            )
            code, out = self._run("--data", tmp, "next", "--source", "all")
        self.assertEqual(code, 0)
        # a BAND cell, not the headline prose (which no longer names one)
        picks = out.split("Band", 1)[1]
        zfree = next(l for l in picks.splitlines() if "zfree" in l)
        self.assertRegex(zfree, r"\b(catalog|free|included|payg)\s*$")

    def test_next_has_no_trailer(self):
        # headline and rules merged into the first line; nothing dangles
        # at the bottom of the view
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "next", "--source", "all")
        self.assertEqual(code, 0)
        self.assertNotIn("LIMITS.md", out.splitlines()[-1])
        head = next(l for l in out.splitlines() if l.startswith("Next "))
        self.assertIn("Zen/Go", head)

    def test_next_shows_a_dash_for_free_lines(self):
        from opencode_models.cli import _pick_value
        from opencode_models.models import Model
        from opencode_models.ranking import Row

        def row(price, score, free=False):
            model = Model(
                model_id="l/m",
                name="M",
                lab="l",
                price_input=price,
                price_output=price,
                price_cached_input=price,
                scores={},
            )
            return Row(
                model=model,
                name="M",
                price=price,
                free=free,
                band="included",
                score=score,
                benchmark=None,
                provider="go",
                grade="o",
                entry=None,
            )

        # a free line is metered at $0, so it reads "-" and sorts ahead
        # of every cost-based line instead of dividing by a nominal
        self.assertEqual(_pick_value(row(0.0, 95.0, free=True)), "-")
        self.assertEqual(_pick_value(row(0.0, 90.0, free=True)), "-")
        self.assertEqual(_pick_value(row(1.0, None)), "-")
        self.assertEqual(_pick_value(row(0.5, 50.0)), "100")

    def test_next_numbers_the_picks_in_order(self):
        import re

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run(
                "--data", tmp, "next", "--count", "2", "--source", "all"
            )
        self.assertEqual(code, 0)
        picks = re.findall(r"^\s+(\d) ", out, re.M)
        self.assertEqual(picks, [str(i) for i in range(1, len(picks) + 1)])

    def test_next_picks_from_the_ordered_ranking_not_catalog_order(self):
        # the grid's best value is the last model written, so a `next` that
        # skipped the ordering would recommend the first one instead
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "next", "--source", "all")
        self.assertEqual(code, 0)
        self.assertIn("opencode/m10", out)  # score 100 at $10 is the best ratio
        self.assertNotIn("m01", out.lower())

    def test_next_gates_on_strength_not_on_the_rank_marker(self):
        # the grid scores i², so the two strongest are M09 and M10 — and
        # neither is necessarily ++, because the marker divides by price
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "next", "--source", "all")
        self.assertEqual(code, 0)
        self.assertIn("opencode/m10", out)
        self.assertNotIn("m01", out.lower())  # weakest, however cheap

    def test_next_labels_a_catalog_only_row_as_such(self):
        # the grid models are in neither tier, so calling them Zen-billed
        # would be a guess
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "next", "--source", "all")
        self.assertEqual(code, 0)
        self.assertIn("catalog", out)

    def test_next_rejects_an_unknown_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            code, out = self._run("--data", tmp, "next", "--source", "nope")
        self.assertEqual(code, 1)
        self.assertIn("zen", out)

    def test_next_needs_a_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run("--data", str(Path(tmp) / "gone"), "next")
        self.assertEqual(code, 1)
        self.assertIn("fetch", out)

    def test_list_source_all_includes_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            code, out = self._run(
                "--data",
                tmp,
                "list",
                "--tables",
                "all",
                "--source",
                "all",
                "--benchmark",
                "TB",
            )
        self.assertEqual(code, 0)
        self.assertIn("M1", out)
        self.assertIn("M2", out)
        self.assertIn("M3", out)

    def test_the_three_tables_are_visually_separated(self):
        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            slugs = {f"m{i:02d}" for i in range(1, 11)}
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: slugs
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--tables", "all", "--source", "zen"
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\n\n──"), 3)  # blank line before each table
        for title in ("best value", "worst score", "highest cost"):
            self.assertRegex(out, rf"(?m)^── [^\n]*{title}")
        # one column header per table: score, cost, plan, in order
        header = re.compile(r"(?m)^\s*Score\s+¢/req\s+Score/\$\s+Prov\s+Model\s*$")
        self.assertEqual(len(header.findall(out)), 3)

    def test_headers_are_bold_when_the_output_is_a_terminal(self):
        import opencode_models.cli as cli

        with mock.patch.object(sys.stdout, "isatty", return_value=True):
            self.assertEqual(cli._style("hi"), "\033[1mhi\033[0m")
        # the plain branch must not depend on the ambient terminal either:
        # under a pty this would otherwise read as bold and fail
        with mock.patch.object(sys.stdout, "isatty", return_value=False):
            self.assertEqual(cli._style("hi"), "hi")  # plain when redirected

    def test_list_defaults_to_both_providers_merged(self):
        import json

        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)

            def model_doc(mid, price, score):
                return json.dumps(
                    {
                        "model": {
                            "id": mid,
                            "name": mid.split("/")[1].upper(),
                            "lab": "l",
                            "priceUsdPer1M": {
                                "input": price,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": score,
                                    "metric": "acc",
                                    "source": "",
                                }
                            ],
                        }
                    }
                )

            # ds: documented by both providers at different rates; the two
            # fillers are pricey enough to be culled and weak enough that
            # best value keeps both ds lines
            (_models(d) / "l__ds.json").write_text(model_doc("l/ds", 9.9, 50.0))
            (_models(d) / "l__e1.json").write_text(model_doc("l/e1", 5.0, 10.0))
            (_models(d) / "l__e2.json").write_text(model_doc("l/e2", 6.0, 20.0))
            plans_dir = d / "plans"
            plans_dir.mkdir()
            # the snapshot is also the availability record now, so it lists
            # every model these plans offer, not only the priced one
            (plans_dir / "zen.json").write_text(
                json.dumps(
                    _plan_doc(
                        [
                            dict(
                                tier="zen",
                                model_id="ds",
                                name="DS",
                                input=2.0,
                                output=2.0,
                                cached_read=2.0,
                                limit=None,
                                free=False,
                            )
                        ]
                        + [
                            dict(
                                tier="zen",
                                model_id=mid,
                                name=mid.upper(),
                                input=9.0,
                                output=9.0,
                                cached_read=9.0,
                                limit=None,
                                free=False,
                            )
                            for mid in ("e1", "e2")
                        ],
                        "zen",
                    )
                )
            )
            (plans_dir / "go.json").write_text(
                json.dumps(
                    _plan_doc(
                        [
                            dict(
                                tier="go",
                                model_id="ds",
                                name="DS",
                                input=1.0,
                                output=1.0,
                                cached_read=1.0,
                                limit=60.0,
                                free=False,
                            )
                        ]
                        + [
                            dict(
                                tier="go",
                                model_id=mid,
                                name=mid.upper(),
                                input=9.0,
                                output=9.0,
                                cached_read=9.0,
                                limit=None,
                                free=False,
                            )
                            for mid in ("e1", "e2")
                        ],
                        "go",
                    )
                )
            )
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"ds", "e1", "e2"}
            try:
                code, out = self._run("--data", tmp, "list")  # no --source
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(
            len(
                re.findall(
                    r"(?m)^\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+"
                    r"DS$",
                    out,
                )
            ),
            2,
        )
        self.assertTrue(any(line.endswith("go  DS") for line in lines))  # provider
        self.assertTrue(any(line.endswith("zen  DS") for line in lines))  # before name
        self.assertIn("Prov  Model", out)  # column header
        self.assertIn("included usage", out)  # go grants a monthly cap
        self.assertIn("pay-as-you-go", out)  # zen does not

    def test_list_all_adds_a_dash_provider_for_catalog_only_models(self):
        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"m1", "m2"}
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--tables", "all", "--benchmark", "TB"
                )
                self.assertEqual(code, 0)
                self.assertNotIn("M3", out)  # default = tiers only
                code, out = self._run(
                    "--data",
                    tmp,
                    "list",
                    "--source",
                    "all",
                    "--tables",
                    "all",
                    "--benchmark",
                    "TB",
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        self.assertIn("M1", out)
        self.assertTrue(
            any(line.endswith("-  M3") for line in out.splitlines()),
            "M3 is in neither tier, so it is listed under '-'",
        )

    def test_list_collapses_a_pair_priced_the_same_into_one_line(self):
        import json

        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "l__ds.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "l/ds",
                            "name": "DS",
                            "lab": "l",
                            "priceUsdPer1M": {
                                "input": 9.9,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 50.0,
                                    "metric": "acc",
                                    "source": "",
                                },
                                {
                                    "name": "SWE-Bench",
                                    "version": "Verified",
                                    "score": 50.0,
                                    "metric": "acc",
                                    "source": "",
                                },
                            ],
                        }
                    }
                )
            )
            plans_dir = d / "plans"
            plans_dir.mkdir()
            same = {
                "model_id": "ds",
                "name": "DS",
                "input": 1.0,
                "output": 1.0,
                "cached_read": 1.0,
                "free": False,
            }
            (plans_dir / "go.json").write_text(
                json.dumps(_plan_doc([dict(same, tier="go", limit=60.0)], "go"))
            )
            (plans_dir / "zen.json").write_text(
                json.dumps(_plan_doc([dict(same, tier="zen", limit=None)], "zen"))
            )
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"ds"}
            try:
                code, out = self._run("--data", tmp, "list")
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        lines = [l for l in out.splitlines() if l.rstrip().endswith("DS")]
        self.assertEqual(len(lines), 1, out)  # one line, not one per provider
        self.assertIn("go,zen", lines[0])

    def test_free_lines_show_a_zero_price_tag(self):
        import json

        import opencode_models.availability as availability

        def doc(mid, price, score):
            return json.dumps(
                {
                    "model": {
                        "id": mid,
                        "name": mid.split("/")[1].upper(),
                        "lab": "l",
                        "priceUsdPer1M": {
                            "input": price,
                            "output": None,
                            "cachedInput": None,
                        },
                        "benchmarks": [
                            {
                                "name": "Terminal-Bench",
                                "version": "2.1",
                                "score": score,
                                "metric": "acc",
                                "source": "",
                            }
                        ],
                    }
                }
            )

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "l__gift.json").write_text(doc("l/gift", 5.0, 40.0))
            (_models(d) / "l__paid.json").write_text(doc("l/paid", 1.0, 50.0))
            plans_dir = d / "plans"
            plans_dir.mkdir()
            (plans_dir / "go.json").write_text(
                json.dumps(
                    _plan_doc(
                        [
                            {
                                "tier": "go",
                                "model_id": "gift",
                                "name": "Gift",
                                "input": 0.0,
                                "output": 0.0,
                                "cached_read": 0.0,
                                "limit": None,
                                "free": True,
                            },
                        ],
                        "go",
                    )
                )
            )
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"gift", "paid"}
            try:
                code, out = self._run("--data", tmp, "list", "--tables", "all")
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        gift = [l for l in out.splitlines() if "GIFT [free]" in l]
        self.assertEqual(len(gift), 1, out)
        self.assertNotIn("0.1", gift[0])  # price is 0, not a catalog rate
        self.assertRegex(gift[0], r"-\s+0\s+-\s+go\s+GIFT \[free\]$")

    def test_columns_stay_aligned_whatever_the_cell_widths(self):
        """Column widths are measured once, across the whole view.

        Fixed widths break the moment a cell outgrows its column — and the
        widest cells here (price '0.263175', provider 'go,zen') land in the
        *highest cost* table, so only a view-wide measurement keeps every
        table lined up with its own header.
        """
        import json

        import opencode_models.availability as availability

        def doc(mid, price, score):
            return json.dumps(
                {
                    "model": {
                        "id": mid,
                        "name": mid.split("/")[1].upper(),
                        "lab": "l",
                        "priceUsdPer1M": {
                            "input": price,
                            "output": None,
                            "cachedInput": None,
                        },
                        "benchmarks": [
                            {
                                "name": "Terminal-Bench",
                                "version": "2.1",
                                "score": score,
                                "metric": "acc",
                                "source": "",
                            }
                        ],
                    }
                }
            )

        prices = {  # price per $1M, cheapest last
            "gift": (5.0, 40.0),  # zero-priced in zen → shown at 0
            "narrow": (0.0612, 50.0),
            "mid": (0.1518, 55.0),
            "mid2": (0.165, 60.0),
            "wide": (0.263175, 65.0),  # widest price AND widest provider
            "top": (5.0, 70.0),
        }
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for mid, (price, score) in prices.items():
                (_models(d) / f"l__{mid}.json").write_text(
                    doc(f"l/{mid}", price, score)
                )
            plans_dir = d / "plans"
            plans_dir.mkdir()
            (plans_dir / "go.json").write_text(
                json.dumps(
                    _plan_doc(
                        [
                            {
                                "tier": "go",
                                "model_id": "wide",
                                "name": "Wide",
                                "input": 0.263175,
                                "output": 0.263175,
                                "cached_read": 0.263175,
                                "limit": 60.0,
                                "free": False,
                            },
                        ],
                        "go",
                    )
                )
            )
            (plans_dir / "zen.json").write_text(
                json.dumps(
                    _plan_doc(
                        [
                            {
                                "tier": "zen",
                                "model_id": mid,
                                "name": mid.title(),
                                "input": price,
                                "output": price,
                                "cached_read": price,
                                "limit": None,
                                "free": mid == "gift",
                            }
                            for mid, (price, _) in prices.items()
                        ],
                        "zen",
                    )
                )
            )
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: (
                {"wide"} if name == "go" else set(prices)
            )
            try:
                code, out = self._run("--data", tmp, "list", "--tables", "all")
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        lines = out.splitlines()
        header = next(l for l in lines if l.strip().startswith("Score"))
        model_col = header.index("Model")
        # one row from each of the three tables, widest cells checked last
        for name in ("MID", "GIFT [free]", "TOP", "WIDE"):
            row = next(l for l in lines if l.rstrip().endswith(name))
            self.assertEqual(row.index(name), model_col, f"{name}: {row!r}")
        wide = next(l for l in lines if l.rstrip().endswith("WIDE"))
        self.assertIn("go,zen", wide)  # the whole token, not a truncation

    def test_models_with_different_coverage_share_one_score_column(self):
        import json

        import opencode_models.availability as availability

        def doc(mid, tb, swe):
            return json.dumps(
                {
                    "model": {
                        "id": mid,
                        "name": mid.split("/")[1].upper(),
                        "lab": "l",
                        "priceUsdPer1M": {
                            "input": 1.0,
                            "output": None,
                            "cachedInput": None,
                        },
                        # the same two axes each, or neither scale has spread
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

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "l__two.json").write_text(doc("l/two", 50.0, 40.0))
            (_models(d) / "l__four.json").write_text(doc("l/four", 90.0, 80.0))
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"two", "four"}
            try:
                code, out = self._run("--data", tmp, "list", "--tables", "all")
            finally:
                availability.load_roster = orig
            self.assertEqual(code, 0)
            two = next(l for l in out.splitlines() if l.rstrip().endswith("TWO"))
            four = next(l for l in out.splitlines() if l.rstrip().endswith("FOUR"))
            # one SCORE column: the weaker model derives 0 on both
            # scales, the stronger 100 on both
            self.assertEqual(two.split()[1], "0")
            self.assertEqual(four.split()[1], "100")
            self.assertTrue(two.startswith(" "))  # marker prefix
            # --benchmark mode still shows one score plus the axis tag
            availability.load_roster = lambda _d, name: {"four"}
            try:
                code, single = self._run(
                    "--data",
                    tmp,
                    "list",
                    "--tables",
                    "all",
                    "--benchmark",
                    "Terminal-Bench 2.1",
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        self.assertRegex(
            next(l for l in single.splitlines() if l.rstrip().endswith("FOUR")),
            r"\s90\s+TB\s+100\s+",
        )

    def test_list_shows_only_best_value_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "list", "--source", "all")
        self.assertEqual(code, 0)
        self.assertIn("best value", out)
        self.assertNotIn("worst score", out)
        self.assertNotIn("highest cost", out)

    def test_best_value_alone_still_culls_the_weak_and_dear(self):
        # hiding the side tables must not re-admit their rows: the cull is
        # what keeps the worst-scoring and priciest models out of the view
        import opencode_models.availability as _avail

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            orig = _avail.load_roster
            _avail.load_roster = lambda _d, name: {f"m{i:02d}" for i in range(1, 11)}
            try:
                code, out = self._run("--data", tmp, "list", "--source", "all")
            finally:
                _avail.load_roster = orig
        self.assertEqual(code, 0)
        self.assertNotIn("worst score", out)
        self.assertNotIn("highest cost", out)
        self.assertNotIn("M01", out)  # weakest
        self.assertNotIn("M10", out)  # priciest
        self.assertIn("M05", out)

    def test_the_default_view_culls_exactly_as_the_three_table_view(self):
        # same rows in best value either way — only the reporting differs
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            _c1, one = self._run("--data", tmp, "list", "--source", "all")
            _c2, three = self._run(
                "--data", tmp, "list", "--source", "all", "--tables", "all"
            )

        def best_value(text):
            body = text.split("── best value")[1].split("──")[0]
            return sorted(set(re.findall(r"\bM\d\d\b", body)))

        self.assertEqual(best_value(one), best_value(three))

    def test_tables_all_restores_the_three_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run(
                "--data", tmp, "list", "--source", "all", "--tables", "all"
            )
        self.assertEqual(code, 0)
        for title in ("best value", "worst score", "highest cost"):
            self.assertIn(title, out)

    def test_tables_rejects_an_unknown_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_:
                    self._run(
                        "--data", tmp, "list", "--source", "all", "--tables", "side"
                    )
        self.assertEqual(exit_.exception.code, 2)

    def test_list_prints_three_tables_in_order(self):
        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            slugs = {f"m{i:02d}" for i in range(1, 11)}
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: slugs
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--tables", "all", "--source", "zen"
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        self.assertEqual(out.count("Prov"), 3)  # one column header per table
        self.assertLess(out.index("best value"), out.index("worst score"))
        self.assertLess(out.index("worst score"), out.index("highest cost"))

    def test_list_culls_are_disjoint_and_cover_every_line(self):
        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            slugs = {f"m{i:02d}" for i in range(1, 11)}
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: slugs
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--tables", "all", "--source", "zen"
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        for i in range(1, 11):  # each line printed exactly once
            self.assertEqual(out.count(f"M{i:02d}"), 1, f"M{i:02d}")

    def test_list_splits_best_value_into_free_included_and_payg(self):
        import json

        import opencode_models.availability as availability

        def doc(mid, price, score):
            return json.dumps(
                {
                    "model": {
                        "id": mid,
                        "name": mid.split("/")[1].upper(),
                        "lab": mid.split("/")[0],
                        "priceUsdPer1M": {
                            "input": price,
                            "output": None,
                            "cachedInput": None,
                        },
                        "benchmarks": [
                            {
                                "name": "Terminal-Bench",
                                "version": "2.1",
                                "score": score,
                                "metric": "acc",
                                "source": "",
                            }
                        ],
                    }
                }
            )

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for mid, price, score in [
                ("l/p1", 2.0, 20.0),
                ("l/p2", 3.0, 30.0),
                ("l/p3", 4.0, 40.0),
                ("l/p4", 5.0, 50.0),
                ("l/p5", 6.0, 60.0),
                ("l/inc", 1.0, 70.0),
                ("l/gift", 5.0, 55.0),
            ]:
                (_models(d) / f"{mid.replace('/', '__')}.json").write_text(
                    doc(mid, price, score)
                )
            plans_dir = d / "plans"
            plans_dir.mkdir()
            (plans_dir / "go.json").write_text(
                json.dumps(
                    _plan_doc(
                        [
                            {
                                "tier": "go",
                                "model_id": "inc",
                                "name": "Inc",
                                "input": 1.0,
                                "output": 1.0,
                                "cached_read": 1.0,
                                "limit": 60.0,
                                "free": False,
                            },
                            {
                                "tier": "go",
                                "model_id": "gift",
                                "name": "Gift",
                                "input": 0.0,
                                "output": 0.0,
                                "cached_read": 0.0,
                                "limit": None,
                                "free": True,
                            },
                            # the snapshot is also the availability record: the fillers
                            # this test needs are offered even though they are unpriced
                        ]
                        + [
                            {
                                "tier": "go",
                                "model_id": mid,
                                "name": mid.upper(),
                                "input": 9.0,
                                "output": 9.0,
                                "cached_read": 9.0,
                                "limit": None,
                                "free": False,
                            }
                            for mid in ("p1", "p2", "p3", "p4", "p5")
                        ],
                        "go",
                    )
                )
            )
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {
                "p1",
                "p2",
                "p3",
                "p4",
                "p5",
                "inc",
                "gift",
            }
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--tables", "all", "--source", "go"
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        self.assertLess(out.index("free — use first"), out.index("included usage"))
        self.assertLess(out.index("included usage"), out.index("pay-as-you-go"))
        self.assertLess(out.index("pay-as-you-go"), out.index("worst score"))
        self.assertIn("GIFT [free]", out)  # tagged (zero price tag is
        # covered by test_free_lines_show_a_zero_price_tag)

    def test_list_shows_the_representative_cost_not_the_bare_input(self):
        import json

        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # three models, each measured on both axes: one benchmark is not a
            # score, so these are about the cost arithmetic, not the evidence
            for mid, name, price_in, price_out, cached, score in [
                ("a/m1", "M1", 1.0, 2.0, 0.1, 50.0),
                ("a/m2", "M2", 1.0, 1.0, 1.0, 25.0),
                ("a/m3", "M3", 0.5, 0.5, 0.5, 37.5),
            ]:
                (_models(d) / f"{mid.replace('/', '__')}.json").write_text(
                    json.dumps(
                        {
                            "model": {
                                "id": mid,
                                "name": name,
                                "lab": "a",
                                "priceUsdPer1M": {
                                    "input": price_in,
                                    "output": price_out,
                                    "cachedInput": cached,
                                },
                                "benchmarks": [
                                    {
                                        "name": "Terminal-Bench",
                                        "version": "2.1",
                                        "score": score,
                                        "metric": "acc",
                                        "source": "",
                                    },
                                    {
                                        "name": "SWE-Bench",
                                        "version": "Verified",
                                        "score": score,
                                        "metric": "acc",
                                        "source": "",
                                    },
                                ],
                            }
                        }
                    )
                )
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"m1", "m2", "m3"}
            try:
                code, out = self._run("--data", tmp, "list", "--source", "zen")
            finally:
                availability.load_roster = orig
            self.assertEqual(code, 0)
            # 0.2 in + 0.6 cached + 0.2 out of (1.0, 0.1, 2.0) = 0.66,
            # shown as 66 cents rather than the bare $1.00 input rate;
            # the score is the calculated 100 (best of 25-50 on both).
            line = next(l for l in out.splitlines() if l.rstrip().endswith("M1"))
            self.assertRegex(line, r"\s100\s+66\s+152\s+zen\s+M1$")

    def test_list_gives_each_priced_variant_its_own_line(self):
        import json

        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "l__ds.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "l/ds",
                            "name": "DS",
                            "lab": "l",
                            "priceUsdPer1M": {
                                "input": 1.74,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 50.0,
                                    "metric": "acc",
                                    "source": "",
                                }
                            ],
                        }
                    }
                )
            )
            plans_dir = d / "plans"
            plans_dir.mkdir()
            (plans_dir / "go.json").write_text(
                json.dumps(
                    _plan_doc(
                        [
                            {
                                "tier": "go",
                                "model_id": "ds",
                                "name": "DS (Off-Peak)",
                                "input": 0.66,
                                "output": 1.98,
                                "cached_read": 0.022,
                                "limit": 15.0,
                                "free": False,
                            },
                            {
                                "tier": "go",
                                "model_id": "ds",
                                "name": "DS (Peak)",
                                "input": 1.32,
                                "output": 3.96,
                                "cached_read": 0.044,
                                "limit": 15.0,
                                "free": False,
                            },
                        ],
                        "go",
                    )
                )
            )
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"ds"}
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--tables", "all", "--source", "go"
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        self.assertIn("(Off-Peak)", out)
        self.assertIn("(Peak)", out)

    if __name__ == "__main__":
        unittest.main()


class OverridesCommandTest(unittest.TestCase):
    """`conflicts` shows which value was used, not which one was rejected."""

    VENDOR = "https://example.invalid/blog/introducing"
    BOARD = "https://www.tbench.ai/leaderboard/terminal-bench/4.0"

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def _data(self, tmp):
        import json

        d = Path(tmp)
        (_models(d) / "a__m1.json").write_text(
            json.dumps(
                {
                    "model": {
                        "id": "a/m1",
                        "name": "M1",
                        "lab": "a",
                        "priceUsdPer1M": {
                            "input": 1.0,
                            "output": None,
                            "cachedInput": None,
                        },
                        "benchmarks": [
                            {
                                "name": "Terminal-Bench",
                                "version": "4.0",
                                "score": 55.8,
                                "metric": "acc",
                                "source": self.VENDOR,
                            }
                        ],
                    }
                }
            )
        )
        overrides = d / "overrides"
        overrides.mkdir(parents=True, exist_ok=True)
        (overrides / "a__m1.json").write_text(
            json.dumps(
                {
                    "model": {
                        "id": "a/m1",
                        "note": "board run",
                        "benchmarks": [
                            {
                                "name": "Terminal-Bench",
                                "version": "4.0",
                                "score": 57.88,
                                "metric": "acc",
                                "source": self.BOARD,
                            }
                        ],
                    }
                }
            )
        )
        return d

    def _data_losing(self, tmp):
        import json

        d = Path(tmp)
        (_models(d) / "a__m2.json").write_text(
            json.dumps(
                {
                    "model": {
                        "id": "a/m2",
                        "name": "M2",
                        "lab": "a",
                        "priceUsdPer1M": {
                            "input": 1.0,
                            "output": None,
                            "cachedInput": None,
                        },
                        "benchmarks": [
                            {
                                "name": "Terminal-Bench",
                                "version": "4.0",
                                "score": 58.18,
                                "metric": "acc",
                                "source": self.BOARD,
                            }
                        ],
                    }
                }
            )
        )
        overrides = d / "overrides"
        overrides.mkdir(parents=True, exist_ok=True)
        (overrides / "a__m2.json").write_text(
            json.dumps(
                {
                    "model": {
                        "id": "a/m2",
                        "benchmarks": [
                            {
                                "name": "Terminal-Bench",
                                "version": "4.0",
                                "score": 50.0,
                                "metric": "acc",
                                "source": self.VENDOR,
                            }
                        ],
                    }
                }
            )
        )
        return d

    def _row(self, out, model):
        return next(l for l in out.splitlines() if model in l)

    def test_it_is_a_table_with_headings(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._data(tmp)
            code, out = self._run("--data", str(d), "overrides")
        self.assertEqual(code, 0)
        header = next(l for l in out.splitlines() if "Used" in l)
        for heading in ("Model", "Benchmark", "Catalog", "Reported", "Used"):
            self.assertIn(heading, header)

    def test_the_used_column_holds_the_value_the_console_keeps(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._data(tmp)  # a board value beats a vendor one
            _code, out = self._run("--data", str(d), "overrides")
        row = self._row(out, "a/m1")
        self.assertIn("57.88", row)  # the board number
        header = next(l for l in out.splitlines() if "Used" in l)
        at = header.index("Used")
        self.assertEqual(row[at : at + 5].strip(), "57.88")

    def test_the_used_column_shows_the_kept_value_when_the_other_loses(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._data_losing(tmp)  # a vendor claim cannot displace a board
            _code, out = self._run("--data", str(d), "overrides")
        row = self._row(out, "a/m2")
        header = next(l for l in out.splitlines() if "Used" in l)
        at = header.index("Used")
        self.assertEqual(row[at : at + 5].strip(), "58.18")  # upstream, kept

    def test_the_wording_does_not_say_a_value_was_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._data(tmp)
            _code, out = self._run("--data", str(d), "overrides")
        self.assertNotIn("override wins", out)
        self.assertNotIn("override noted", out)
        self.assertNotIn("→", out)

    def test_each_source_carries_its_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._data(tmp)
            _code, out = self._run("--data", str(d), "overrides")
        self.assertIn("self", out)
        self.assertIn("board", out)

    def test_an_override_matching_no_model_is_reported(self):
        # the signature of a typo, or of a model pulled from the registry
        import json

        with tempfile.TemporaryDirectory() as tmp:
            d = self._data(tmp)
            (d / "overrides" / "a__ghost.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "a/ghost",
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "4.0",
                                    "score": 1.0,
                                    "metric": "acc",
                                    "source": self.BOARD,
                                }
                            ],
                        }
                    }
                )
            )
            _code, out = self._run("--data", str(d), "overrides")
        self.assertIn("a/ghost", out)
        self.assertIn("no upstream", out)

    def test_it_still_counts_what_it_saw(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._data(tmp)
            _code, out = self._run("--data", str(d), "overrides")
        self.assertRegex(out, r"1 curated disagreement|0 curated disagreements")


class ListViewContractTest(unittest.TestCase):
    def setUp(self):
        self._rp = __import__("unittest.mock", fromlist=["patch"]).patch(
            "opencode_models.cli.sync_mod.refresh_plan",
            return_value=("fresh", (), None),
        )
        self._rp.start()
        self.addCleanup(self._rp.stop)

    """What the printed `list` guarantees, read back off the console text.

    These tests recompute every number from the modules rather than pinning
    literals, so they fail when the view drifts from the logic - the ordering
    no longer matching the shown columns, a grade band leaking across scores,
    a row vanishing - instead of when a fixture value changes.
    """

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def _data(self, tmp):
        import json

        import opencode_models.availability as availability

        _write_grid(Path(tmp))
        docs = {
            # scored and free: infinite value, must lead best value
            "l/zfree": (
                0.0,
                [("Terminal-Bench", "2.1", 60.0), ("SWE-Bench", "Verified", 30.0)],
            ),
            # one axis: unranked tail, still listed
            "l/single": (5.0, [("Terminal-Bench", "2.1", 30.0)]),
            # no benchmark at all: dropped, not listed anywhere
            "l/nobench": (5.0, []),
        }
        for mid, (price, benches) in docs.items():
            (_models(Path(tmp)) / f"{mid.replace('/', '__')}.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": mid,
                            "name": mid.split("/")[1].upper(),
                            "lab": "l",
                            "priceUsdPer1M": {
                                "input": price,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": b[0],
                                    "version": b[1],
                                    "score": b[2],
                                    "metric": "acc",
                                    "source": "",
                                }
                                for b in benches
                            ],
                        }
                    }
                )
            )
        slugs = {f"m{i:02d}" for i in range(1, 11)} | {"zfree", "single", "nobench"}
        orig = availability.load_roster
        availability.load_roster = lambda _d, name: set(slugs)
        self.addCleanup(setattr, availability, "load_roster", orig)

    def _section_rows(self, out, title):
        # rows under a ── title ── line, stopping at the next title: the
        # naive split on the dashes cuts at the title's own closing pair
        # and yields nothing, which three of these tests then passed
        # vacuously. This one fails loudly instead when the title is absent.
        lines = out.splitlines()
        start = next(i for i, l in enumerate(lines) if f"── {title}" in l)
        rows = []
        for line in lines[start + 1 :]:
            if line.startswith("──"):
                break
            parts = line.split()
            if len(parts) >= 5 and parts[0] in ("++", "+", "o", "--", "·"):
                rows.append(
                    {
                        "grade": parts[0],
                        "score": parts[1],
                        "cents": parts[2],
                        "name": " ".join(parts[4:]),
                    }
                )
        return rows

    def _best_value_rows(self, out):
        rows = self._section_rows(out, "best value")
        assert rows, "no rows parsed from best value - parser or view broken"
        return rows

    def _expected(self, tmp):
        from opencode_models.models import load_models
        from opencode_models.pricing import weighted_cost
        from opencode_models.scoring import combined, derived_scores, scales

        models = {m.model_id: m for m in load_models(_models(Path(tmp)))}
        table = scales(list(models.values()))
        expected = {}
        for mid, model in models.items():
            derived = derived_scores(model, table)
            score = combined(derived)
            cost = weighted_cost(
                model.price_input, model.price_output, model.price_cached_input
            )
            expected[mid] = (score, cost)
        return expected

    def test_best_value_is_ordered_by_score_per_dollar(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(tmp)
            code, out = self._run(
                "--data", tmp, "list", "--source", "all", "--tables", "all"
            )
        self.assertEqual(code, 0)
        # the headline states the ordering the rows are checked against:
        # a lying headline would pass every measurement below
        head = next(l for l in out.splitlines() if "lines ·" in l)
        self.assertIn("score per $", head)
        rows = self._best_value_rows(out)
        values = []
        for row in rows:
            if row["score"] == "-":
                values.append(-1.0)  # unranked sorts after everything valued
            elif row["cents"] == "0":
                values.append(float("inf"))
            else:
                values.append(float(row["score"]) / float(row["cents"]))
        self.assertEqual(values, sorted(values, reverse=True))

    def test_score_cost_and_grade_match_the_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(tmp)
            code, out = self._run(
                "--data", tmp, "list", "--source", "all", "--tables", "all"
            )
            expected = self._expected(tmp)
        self.assertEqual(code, 0)
        names = {f"M{i:02d}": f"l/m{i:02d}" for i in range(1, 11)}
        names.update({"ZFREE": "l/zfree", "SINGLE": "l/single"})
        for row in self._best_value_rows(out):
            if row["name"] not in names:
                continue
            score, cost = expected[names[row["name"]]]
            with self.subTest(model=row["name"]):
                self.assertEqual(
                    row["score"], "-" if score is None else f"{round(score)}"
                )
                self.assertEqual(row["cents"], f"{round(cost * 100)}")
        # grade bands follow scores, top to bottom, across the whole view:
        # sorting every graded row by its score must yield the bands in
        # order. (Tables print best, worst, cost - concatenating them is
        # not rank order, so sort first.)
        scored = [
            (r["grade"], float(r["score"]))
            for table in ("best value", "worst score", "highest cost")
            for r in self._table_rows(out, table)
            if r["score"] != "-"
        ]
        order = {"++": 0, "+": 1, "o": 2, "--": 3}
        by_score = sorted(scored, key=lambda gs: -gs[1])
        self.assertEqual(
            [g for g, _ in by_score],
            sorted([g for g, _ in by_score], key=order.__getitem__),
        )
        by_grade = {}
        for grade, score in scored:
            by_grade.setdefault(grade, []).append(score)
        bands = [g for g in ("++", "+", "o", "--") if g in by_grade]
        from itertools import pairwise

        for upper, lower in pairwise(bands):
            self.assertGreater(min(by_grade[upper]), max(by_grade[lower]))

    def _table_rows(self, out, title):
        return self._section_rows(out, title)

    def test_worst_score_holds_the_lowest_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(tmp)
            code, out = self._run(
                "--data", tmp, "list", "--source", "all", "--tables", "all"
            )
        self.assertEqual(code, 0)
        best = [
            float(r["score"]) for r in self._best_value_rows(out) if r["score"] != "-"
        ]
        worst = [
            float(r["score"])
            for r in self._table_rows(out, "worst score")
            if r["score"] != "-"
        ]
        self.assertTrue(worst)
        self.assertLessEqual(max(worst), min(best))

    def test_highest_cost_holds_the_priciest_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(tmp)
            code, out = self._run(
                "--data", tmp, "list", "--source", "all", "--tables", "all"
            )
        self.assertEqual(code, 0)
        best = [float(r["cents"]) for r in self._best_value_rows(out)]
        pricey = [float(r["cents"]) for r in self._table_rows(out, "highest cost")]
        self.assertTrue(pricey)
        self.assertGreaterEqual(min(pricey), max(best))

    def test_measured_rows_appear_and_unmeasured_ones_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(tmp)
            code, out = self._run(
                "--data", tmp, "list", "--source", "all", "--tables", "all"
            )
        self.assertEqual(code, 0)
        self.assertIn("ZFREE", out)  # free and scored: must lead, not vanish
        self.assertIn("SINGLE", out)  # one axis: unranked tail, still listed
        self.assertNotIn("NOBENCH", out)  # nothing reaches it: dropped


class AvailabilityTest(unittest.TestCase):
    """A model the plans no longer offer is not shown, scored or ranked.

    Providers unlist models and move them between plans, and the committed
    membership lists drift: 13 Go slugs were still on offer after the
    provider's own docs dropped them.
    """

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def _data(self, tmp, *, offered, shipped):
        import json

        d = Path(tmp)
        # the unlisted model is the *strongest* one, so nothing but the
        # availability filter can hide it: a weak row would be culled out of
        # best value anyway and the test would pass for the wrong reason
        for mid, score in [
            ("a/kept", 80.0),
            ("a/gone", 95.0),
            ("a/third", 60.0),
            ("a/fourth", 50.0),
        ]:
            (_models(d) / f"{mid.replace('/', '__')}.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": mid,
                            "name": mid.split("/")[1].upper(),
                            "lab": "a",
                            "priceUsdPer1M": {
                                "input": 1.0,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": score,
                                    "metric": "acc",
                                    "source": "",
                                },
                                {
                                    "name": "SWE-Bench",
                                    "version": "Verified",
                                    "score": score / 2,
                                    "metric": "acc",
                                    "source": "",
                                },
                            ],
                        }
                    }
                )
            )
        plans = d / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "zen.json").write_text(
            json.dumps(
                _plan_doc(
                    [
                        {
                            "tier": "zen",
                            "model_id": m,
                            "name": m,
                            "input": 1.0,
                            "output": 1.0,
                            "cached_read": None,
                            "limit": None,
                            "free": False,
                        }
                        for m in offered
                    ],
                    "zen",
                )
            )
        )
        # no go snapshot at all: unknown, not "offers nothing"
        import opencode_models.availability as availability

        orig = availability.load_roster
        availability.load_roster = lambda _d, tier: set(shipped)
        self.addCleanup(setattr, availability, "load_roster", orig)
        return d

    def test_a_free_model_listed_only_under_its_free_slug_is_shown(self):
        # same shape as the production miss: the snapshot carries
        # "step-5-preview-free" while the catalog carries
        # "stepfun/step-5-preview" — the model is offered, and its free row
        # must appear rather than vanish with every other free line
        import json

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for mid, score in [
                ("stepfun/step-5-preview", 80.0),
                ("stepfun/other", 60.0),
                ("stepfun/third", 40.0),
            ]:
                (_models(d) / f"{mid.replace('/', '__')}.json").write_text(
                    json.dumps(
                        {
                            "model": {
                                "id": mid,
                                "name": mid.split("/")[1].upper(),
                                "lab": "stepfun",
                                "priceUsdPer1M": {
                                    "input": 1.0,
                                    "output": None,
                                    "cachedInput": None,
                                },
                                "benchmarks": [
                                    {
                                        "name": "Terminal-Bench",
                                        "version": "2.1",
                                        "score": score,
                                        "metric": "acc",
                                        "source": "",
                                    },
                                    {
                                        "name": "SWE-Bench",
                                        "version": "Verified",
                                        "score": score / 2,
                                        "metric": "acc",
                                        "source": "",
                                    },
                                ],
                            }
                        }
                    )
                )
            plans = d / "plans"
            plans.mkdir(parents=True, exist_ok=True)
            (plans / "zen.json").write_text(
                json.dumps(
                    _plan_doc(
                        [
                            {
                                "tier": "zen",
                                "model_id": m,
                                "name": m,
                                "input": 1.0,
                                "output": 1.0,
                                "cached_read": None,
                                "limit": None,
                                "free": m == "step-5-preview-free",
                            }
                            for m in ("step-5-preview-free", "other", "third")
                        ],
                        "zen",
                    )
                )
            )
            import opencode_models.availability as availability

            orig = availability.load_roster
            availability.load_roster = lambda _d, tier: {
                "step-5-preview-free",
                "other",
                "third",
            }
            try:
                code, out = self._run(
                    "--data", str(d), "list", "--source", "zen", "--tables", "all"
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        self.assertIn("STEP-5-PREVIEW [free]", out)

    def test_list_hides_a_model_the_plan_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(
                tmp,
                offered=["kept", "third", "fourth"],
                shipped=["kept", "third", "fourth", "gone"],
            )
            code, out = self._run("--data", tmp, "list", "--source", "zen")
        self.assertEqual(code, 0)
        self.assertIn("KEPT", out)
        self.assertNotIn("GONE", out)

    def test_list_shows_it_again_with_all_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(
                tmp,
                offered=["kept", "third", "fourth"],
                shipped=["kept", "third", "fourth", "gone"],
            )
            code, out = self._run(
                "--data", tmp, "list", "--source", "zen", "--all-models"
            )
        self.assertEqual(code, 0)
        self.assertIn("KEPT", out)
        self.assertIn("GONE", out)
        self.assertNotIn("no longer listed", out)
        self.assertNotIn("available right now", out)

    def test_all_is_an_alias_for_all_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(
                tmp,
                offered=["kept", "third", "fourth"],
                shipped=["kept", "third", "fourth", "gone"],
            )
            _code, short = self._run("--data", tmp, "list", "--source", "zen", "--all")
            _code, long = self._run(
                "--data", tmp, "list", "--source", "zen", "--all-models"
            )
        self.assertEqual(short, long)

    def test_the_hidden_model_is_the_strongest_one(self):
        # guards the guard: the row that must disappear leads the table when
        # it is shown, so this cannot pass on a cull or an ordering change
        with tempfile.TemporaryDirectory() as tmp:
            self._data(
                tmp,
                offered=["kept", "third", "fourth"],
                shipped=["kept", "third", "fourth", "gone"],
            )
            _c, every = self._run("--data", tmp, "scores", "--all-models")
        first = next(l for l in every.splitlines() if "GONE" in l)
        self.assertIn("GONE", first.split())

    def test_scores_hides_a_model_the_plan_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(
                tmp,
                offered=["kept", "third", "fourth"],
                shipped=["kept", "third", "fourth", "gone"],
            )
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertIn("KEPT", out)
        self.assertNotIn("GONE", out)

    def test_scores_shows_it_again_with_all_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(
                tmp,
                offered=["kept", "third", "fourth"],
                shipped=["kept", "third", "fourth", "gone"],
            )
            code, out = self._run("--data", tmp, "scores", "--all-models")
        self.assertEqual(code, 0)
        self.assertIn("GONE", out)

    def test_a_free_line_sorts_before_paid_lines(self):
        # free is metered at $0, so a free line outranks every paid line
        # and reads "-" for score/$ instead of dividing by a nominal
        import json

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for mid, tb, swe in [("a/m1", 80.0, 40.0), ("a/m2", 40.0, 80.0)]:
                (_models(d) / f"{mid.replace('/', '__')}.json").write_text(
                    json.dumps(
                        {
                            "model": {
                                "id": mid,
                                "name": mid.split("/")[1].upper(),
                                "lab": "a",
                                "priceUsdPer1M": {
                                    "input": 1.0,
                                    "output": None,
                                    "cachedInput": None,
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
            plans = d / "plans"
            plans.mkdir(parents=True, exist_ok=True)
            (plans / "zen.json").write_text(
                json.dumps(
                    _plan_doc(
                        [
                            {
                                "tier": "zen",
                                "model_id": "m1",
                                "name": "M1",
                                "input": 0.0,
                                "output": 0.0,
                                "cached_read": 0.0,
                                "limit": None,
                                "free": True,
                            },
                            {
                                "tier": "zen",
                                "model_id": "m2",
                                "name": "M2",
                                "input": 1.0,
                                "output": 1.0,
                                "cached_read": 1.0,
                                "limit": None,
                                "free": False,
                            },
                        ],
                        "zen",
                    )
                )
            )
            usage = d / "usage"
            usage.mkdir(parents=True, exist_ok=True)
            import opencode_models.availability as availability

            orig = availability.load_roster
            availability.load_roster = lambda _d, tier: {"m1", "m2"}
            try:
                code, out = self._run(
                    "--data", str(d), "list", "--source", "zen", "--tables", "all"
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        lines = out.splitlines()
        m1 = next(i for i, l in enumerate(lines) if l.rstrip().endswith("M1 [free]"))
        m2 = next(i for i, l in enumerate(lines) if l.rstrip().endswith("M2"))
        self.assertLess(m1, m2)
        self.assertRegex(lines[m1], r"\s0\s+-\s+")

    def test_the_headline_states_availability_without_naming_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(
                tmp,
                offered=["kept", "third", "fourth"],
                shipped=["kept", "third", "fourth", "gone"],
            )
            code, out = self._run("--data", tmp, "list", "--source", "zen")
        self.assertEqual(code, 0)
        self.assertIn("available right now", out)
        self.assertIn("--all-models", out)
        self.assertIn("on the zen plan", out)
        self.assertNotIn("gone", out)
        self.assertNotIn("no longer listed", out)

    def test_source_all_omits_a_tier_listed_unoffered_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(
                tmp,
                offered=["kept", "third", "fourth"],
                shipped=["kept", "third", "fourth", "gone"],
            )
            code, out = self._run("--data", tmp, "list", "--source", "all")
            self.assertEqual(code, 0)
            self.assertIn("KEPT", out)
            self.assertNotIn("GONE", out)
            self.assertFalse(
                any(line.rstrip().endswith("-  GONE") for line in out.splitlines())
            )
            code, out = self._run(
                "--data", tmp, "list", "--source", "all", "--all-models"
            )
            self.assertEqual(code, 0)
            self.assertIn("GONE", out)

    def test_the_scales_still_span_the_whole_catalog(self):
        # a model you cannot buy still says where the field is, so a score
        # does not change when a plan drops something
        with tempfile.TemporaryDirectory() as tmp:
            self._data(
                tmp,
                offered=["kept", "third", "fourth"],
                shipped=["kept", "third", "fourth", "gone"],
            )
            _c, shown = self._run("--data", tmp, "scores")
            _c, every = self._run("--data", tmp, "scores", "--all-models")
        kept_line = next(l for l in shown.splitlines() if "KEPT" in l)
        every_line = next(l for l in every.splitlines() if "KEPT" in l)
        self.assertEqual(kept_line.split()[1:], every_line.split()[1:])


class ScoresCommandTest(unittest.TestCase):
    """`scores` shows what a model was measured on and what it derives to."""

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def _grid(self, tmp):
        import json

        for mid, price, score in [
            ("a/m1", 1.0, 90.0),
            ("a/m2", 1.0, 50.0),
            ("a/m3", 1.0, 10.0),
        ]:
            (_models(Path(tmp)) / f"{mid.replace('/', '__')}.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": mid,
                            "name": mid.split("/")[1].upper(),
                            "lab": "a",
                            "priceUsdPer1M": {
                                "input": price,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": score,
                                    "metric": "acc",
                                    "source": "",
                                },
                                {
                                    "name": "SWE-Bench",
                                    "version": "Verified",
                                    "score": score / 2,
                                    "metric": "acc",
                                    "source": "",
                                },
                            ],
                        }
                    }
                )
            )

    def test_it_lists_each_models_scores_on_both_scales(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertIn("M1", out)
        self.assertIn("Terminal-Bench 2.1", out)
        self.assertIn("SWE-Bench Verified", out)

    def test_it_shows_the_raw_and_the_derived_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        # the best model on both benchmarks scores 90 and 45 raw, and 100
        # on each derived scale
        line = next(
            l for l in out.splitlines() if " M1 " in l or l.strip().endswith("M1")
        )
        self.assertIn("90", line)
        self.assertIn("100", line)

    def test_it_does_not_list_benchmarks_we_do_not_score_on(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            (_models(Path(tmp)) / "a__m4.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "a/m4",
                            "name": "M4",
                            "lab": "a",
                            "priceUsdPer1M": {
                                "input": 1.0,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 70.0,
                                    "metric": "a",
                                    "source": "",
                                },
                                {
                                    "name": "SEC-Bench Pro",
                                    "score": 17.7,
                                    "metric": "a",
                                    "source": "",
                                },
                            ],
                        }
                    }
                )
            )
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertNotIn("SEC-Bench", out)
        self.assertNotIn("17.7", out)  # not counted, so not listed

    def _columns(self, out):
        """The header labels of the scores table, in order.

        Benchmark columns are separated by two spaces and their labels
        contain single ones ("swe vrfd"), so the gap is what delimits
        them; model/grade/score are single-spaced and come first.
        """
        head = next(l for l in out.splitlines() if "Score" in l and "Tb" in l)
        return re.split(r"\s{2,}", head.strip())[1:]

    def _row(self, out, name):
        # the grade marker leads the line, so the name is a token in it,
        # not the first one
        return next(l for l in out.splitlines() if name in l.split())

    def test_each_scored_benchmark_is_a_column_of_its_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertEqual(self._columns(out), ["Tb 2.1", "Swe vrfd"])

    def test_a_column_shows_the_raw_and_the_derived_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            _code, out = self._run("--data", tmp, "scores")
        # M1 is the best model on both: 90 raw and 45 raw, both 100 derived
        self.assertIn("90→100", self._row(out, "M1"))
        self.assertIn("45→100", self._row(out, "M1"))

    def test_the_columns_follow_the_axis_order(self):
        # SWE-bench Verified is carried by more models here than Terminal-Bench
        # is, as in the live catalog, so ordering columns by breadth would
        # lead with SWE. The axis the console ranks on leads instead.
        import json

        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            extra = [
                ("a/m5", "SWE-Bench", "Verified", 60.0),
                ("a/m6", "SWE-Bench", "Verified", 70.0),
                ("a/m7", "SWE-Bench", "Pro", 5.0),
                ("a/m8", "SWE-Bench", "Pro", 40.0),
                # an older, easier revision: not a column, not scored
                ("a/m9", "Terminal-Bench", "2.0", 80.0),
                ("a/ma", "Terminal-Bench", "2.0", 20.0),
            ]
            for mid, bench, ver, score in extra:
                (_models(Path(tmp)) / f"{mid.replace('/', '__')}.json").write_text(
                    json.dumps(
                        {
                            "model": {
                                "id": mid,
                                "name": mid.split("/")[1].upper(),
                                "lab": "a",
                                "priceUsdPer1M": {
                                    "input": 1.0,
                                    "output": None,
                                    "cachedInput": None,
                                },
                                "benchmarks": [
                                    {
                                        "name": bench,
                                        "version": ver,
                                        "score": score,
                                        "metric": "acc",
                                        "source": "",
                                    }
                                ],
                            }
                        }
                    )
                )
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertEqual(self._columns(out), ["Tb 2.1", "Swe vrfd", "Swe pro"])

    def test_a_benchmark_nobody_carries_gets_no_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            code, out = self._run("--data", tmp, "scores")
        self.assertNotIn("SWE Lite", out)

    def test_an_unscored_benchmark_gets_no_column_and_no_number(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            for mid, score in [("a/m5", 80.0), ("a/m6", 20.0)]:
                (_models(Path(tmp)) / f"{mid.replace('/', '__')}.json").write_text(
                    json.dumps(
                        {
                            "model": {
                                "id": mid,
                                "name": mid.split("/")[1].upper(),
                                "lab": "a",
                                "priceUsdPer1M": {
                                    "input": 1.0,
                                    "output": None,
                                    "cachedInput": None,
                                },
                                "benchmarks": [
                                    {
                                        "name": "Terminal-Bench",
                                        "version": "2.0",
                                        "score": score,
                                        "metric": "acc",
                                        "source": "",
                                    }
                                ],
                            }
                        }
                    )
                )
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertNotIn("TB 2.0", out)
        # M5 measured on nothing we score on: no score, no axes
        self.assertIn("M5", self._row(out, "M5"))
        self.assertEqual(self._row(out, "M5").split()[:3], ["M5", "·", "-"])

    def test_both_spellings_of_a_benchmark_share_one_column(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            for mid, score in [("a/m7", 70.0), ("a/m9", 30.0)]:
                (_models(Path(tmp)) / f"{mid.replace('/', '__')}.json").write_text(
                    json.dumps(
                        {
                            "model": {
                                "id": mid,
                                "name": f"M{mid[-1]}",
                                "lab": "a",
                                "priceUsdPer1M": {
                                    "input": 1.0,
                                    "output": None,
                                    "cachedInput": None,
                                },
                                "benchmarks": [
                                    {
                                        "name": "Terminal Bench",
                                        "version": "2.1",
                                        "score": score,
                                        "metric": "acc",
                                        "source": "",
                                    }
                                ],
                            }
                        }
                    )
                )
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        # one column, and it is the same scale as the hyphenated spelling:
        # the five numbers run 10-90, so 70 lands at 75
        self.assertEqual(self._columns(out).count("Tb 2.1"), 1)
        self.assertIn("70→75", self._row(out, "M7"))

    def test_the_short_column_names_say_which_benchmark_they_are(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            _code, out = self._run("--data", tmp, "scores")
        self.assertIn("Tb 2.1 = Terminal-Bench 2.1", out)
        self.assertIn("Swe vrfd = SWE-Bench Verified", out)

    def test_a_column_is_blank_where_the_model_was_not_measured(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            (_models(Path(tmp)) / "a__m8.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "a/m8",
                            "name": "M8",
                            "lab": "a",
                            "priceUsdPer1M": {
                                "input": 1.0,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "SWE-Bench",
                                    "version": "Verified",
                                    "score": 40.0,
                                    "metric": "acc",
                                    "source": "",
                                }
                            ],
                        }
                    }
                )
            )
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        head = next(l for l in out.splitlines() if "Score" in l and "Tb 2.1" in l)
        row = self._row(out, "M8")
        self.assertIn("40→", row)
        # the SWE cell sits past the Terminal-Bench column, and that column
        # is blank for a model that was never measured on it
        self.assertGreater(row.index("40→"), head.index("Tb 2.1"))
        self.assertEqual(row[head.index("Tb 2.1") : row.index("40→")].strip(), "")

    def test_the_headline_counts_scores_not_benchmarks(self):
        # M9 has one benchmark and therefore no score; counting it as
        # "with scores" overstated the scored field by a third. M0 carries
        # only an unscored benchmark, which is a third group, not part of
        # the one-benchmark count it used to be folded into.
        import json

        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            (_models(Path(tmp)) / "a__m9.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "a/m9",
                            "name": "M9",
                            "lab": "a",
                            "priceUsdPer1M": {
                                "input": 1.0,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 42.0,
                                    "metric": "acc",
                                    "source": "",
                                }
                            ],
                        }
                    }
                )
            )
            (_models(Path(tmp)) / "a__m0.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "a/m0",
                            "name": "M0",
                            "lab": "a",
                            "priceUsdPer1M": {
                                "input": 1.0,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "SEC-Bench",
                                    "version": "Pro",
                                    "score": 17.7,
                                    "metric": "acc",
                                    "source": "",
                                }
                            ],
                        }
                    }
                )
            )
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        head = out.splitlines()[0]
        self.assertIn("3 with scores", head)  # M1-M3 only
        self.assertIn("1 on one benchmark", head)  # M9: measured, unranked
        self.assertIn("1 with none scored", head)  # M0: benchmarks, no axes
        self.assertNotIn("4 with scores", head)

    def test_the_grade_sits_between_the_name_and_the_score(self):
        # MODEL GRADE SCORE: the same markers as `list`, as a column
        # rather than a prefix
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        head = next(l for l in out.splitlines() if "Score" in l and "Tb 2.1" in l)
        self.assertLess(head.index("Model"), head.index("Grade"))
        self.assertLess(head.index("Grade"), head.index("Score"))
        grades = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) > 3 and parts[1] in ("++", "+", "o", "--", "·"):
                grades[parts[0]] = parts[1]
        self.assertEqual(grades.get("M1"), "++")  # best of the grid
        self.assertEqual(grades.get("M3"), "--")  # worst of the grid

    def test_a_model_with_one_benchmark_is_marked_unranked(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            (_models(Path(tmp)) / "a__m9.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "a/m9",
                            "name": "M9",
                            "lab": "a",
                            "priceUsdPer1M": {
                                "input": 1.0,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 42.0,
                                    "metric": "acc",
                                    "source": "",
                                }
                            ],
                        }
                    }
                )
            )
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        row = next(l for l in out.splitlines() if "M9" in l.split())
        self.assertEqual(row.split()[1], "·")

    def test_no_line_pads_itself_out_with_trailing_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        for line in out.splitlines():
            self.assertEqual(line, line.rstrip())

    def test_the_console_does_not_explain_the_scoring(self):
        # the arithmetic belongs in the README and on the functions that do
        # it; the console states what it measured and points at the rest
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        lowered = out.lower()
        for prose in ("median", "0-100", "best model", "lowest 0", "scale"):
            with self.subTest(word=prose):
                self.assertNotIn(prose, lowered)

    def test_it_points_at_the_documentation_for_the_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertIn("README", out)

    def test_the_headline_is_counts_and_the_help_is_at_the_bottom(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        head = lines[0]
        self.assertIn("models", head)
        self.assertIn("benchmarks", head)
        # the column legend is what a reader needs to decode the table, and
        # it belongs under it rather than above
        legend = next(
            i for i, l in enumerate(lines) if "Tb 2.1 = Terminal-Bench 2.1" in l
        )
        table = next(i for i, l in enumerate(lines) if "Score" in l and "Tb 2.1" in l)
        self.assertGreater(legend, table)
        self.assertLess(
            table, next(i for i, l in enumerate(lines) if "M1" in l.split())
        )

    def test_a_model_with_no_benchmarks_is_listed_as_unscored(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            (_models(Path(tmp)) / "a__m1.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "a/m1",
                            "name": "M1",
                            "lab": "a",
                            "priceUsdPer1M": {
                                "input": 1.0,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": [],
                        }
                    }
                )
            )
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertIn("M1", out)
        self.assertRegex(out, r"(?i)(no benchmarks|none)")


class ModelsCommandTest(unittest.TestCase):
    def _snapshot(self, data_dir, tier, model_ids):
        import json

        out = Path(data_dir) / "plans"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{tier}.json").write_text(
            json.dumps(
                {
                    "meta": {
                        "source": "test",
                        "fetched_at": "2026-01-01T00:00:00+00:00",
                        "schema_rev": 1,
                        "plans": [tier],
                    },
                    "models": {
                        mid: {
                            "name": mid,
                            "free": False,
                            "tiers": [
                                {
                                    "label": None,
                                    "in": 1.0,
                                    "out": 1.0,
                                    "cached": None,
                                    "cached_write": None,
                                }
                            ],
                            "limits": None,
                            "requests_per_month": None,
                            "assumptions": None,
                        }
                        for mid in model_ids
                    },
                }
            )
        )

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def test_models_lists_each_tiers_slugs_from_snapshots(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            import json as _json

            self._snapshot(tmp, "zen", ["m-zen"])
            self._snapshot(tmp, "go", ["m-go"])
            for mid in ("m-zen", "m-go"):
                (_models(Path(tmp)) / f"l__{mid}.json").write_text(
                    _json.dumps({"model": {"id": f"l/{mid}", "name": mid.upper()}})
                )
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENCODE_API_KEY", None)
                code, out = self._run("--data", tmp, "models")
            self.assertEqual(code, 0)
            self.assertIn("m-zen", out)
            self.assertIn("m-go", out)

    def test_models_prefers_the_live_api_when_keyed(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            import json as _json

            self._snapshot(tmp, "zen", ["m-stale"])
            # both catalog-known: m-stale's absence proves the served
            # filter, not the catalog intersect, does the hiding
            for mid in ("m-live", "m-stale"):
                (_models(Path(tmp)) / f"l__{mid}.json").write_text(
                    _json.dumps({"model": {"id": f"l/{mid}", "name": mid.upper()}})
                )
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with (
                    mock.patch.object(
                        availability,
                        "fetch_served",
                        return_value={"zen": {"m-live"}, "go": set()},
                    ),
                    mock.patch.object(
                        availability,
                        "fetch_live",
                        return_value={"zen": {"m-live": False}, "go": {}},
                    ),
                ):
                    code, out = self._run("--data", tmp, "models")
            self.assertEqual(code, 0)
            self.assertIn("m-live", out)
            self.assertNotIn("m-stale", out)

    def test_models_records_the_live_set_for_next_and_list(self):
        import json
        import os

        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot(tmp, "zen", ["m-stale"])
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with (
                    mock.patch.object(
                        availability,
                        "fetch_served",
                        return_value={"zen": {"m-live"}, "go": set()},
                    ),
                    mock.patch.object(
                        availability,
                        "fetch_live",
                        return_value={"zen": {"m-live": False}, "go": {}},
                    ),
                ):
                    code, _ = self._run("--data", tmp, "models")
            self.assertEqual(code, 0)
            saved = json.loads((Path(tmp) / "available" / "zen.json").read_text())[
                "models"
            ]
            self.assertEqual(saved, {"m-live": True})


class CurrentOfferedTest(unittest.TestCase):
    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def _snapshot(self, data_dir, tier, model_ids):
        import json

        out = Path(data_dir) / "plans"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{tier}.json").write_text(
            json.dumps(
                {
                    "meta": {
                        "source": "test",
                        "fetched_at": "2026-01-01T00:00:00+00:00",
                        "schema_rev": 1,
                        "plans": [tier],
                    },
                    "models": {
                        mid: {
                            "name": mid,
                            "free": False,
                            "tiers": [
                                {
                                    "label": None,
                                    "in": 1.0,
                                    "out": 1.0,
                                    "cached": None,
                                    "cached_write": None,
                                }
                            ],
                            "limits": None,
                            "requests_per_month": None,
                            "assumptions": None,
                        }
                        for mid in model_ids
                    },
                }
            )
        )

    def _offered(self, tmp, **kw):
        import io

        import opencode_models.cli as cli_mod

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return cli_mod._current_offered(Path(tmp), **kw)

    def test_bypass_means_no_restriction_even_when_keyed(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot(tmp, "zen", ["m1"])
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with mock.patch.object(
                    availability, "fetch_served", side_effect=AssertionError("no net")
                ):
                    self.assertIsNone(self._offered(tmp, bypass=True))

    def test_without_a_key_the_snapshots_decide(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot(tmp, "zen", ["m1"])
            os.environ.pop("OPENCODE_API_KEY", None)
            self.assertEqual(self._offered(tmp), {"m1"})

    def test_with_a_key_the_served_set_decides_and_is_recorded(self):
        import json
        import os

        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot(tmp, "zen", ["m-stale"])
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with (
                    mock.patch.object(
                        availability,
                        "fetch_served",
                        return_value={"zen": {"m-live"}, "go": {"g-live"}},
                    ),
                    mock.patch.object(
                        availability,
                        "fetch_live",
                        return_value={
                            "zen": {"m-live": True, "legacy": True},
                            "go": {},
                        },
                    ),
                ):
                    # served only: the config-known-but-unserved "legacy"
                    # model is recorded but never offered
                    self.assertEqual(self._offered(tmp), {"m-live", "g-live"})
            saved = json.loads((Path(tmp) / "available" / "zen.json").read_text())[
                "models"
            ]
            self.assertEqual(saved, {"m-live": True, "legacy": False})

    def test_zero_go_models_raises_a_personal_token_warning(self):
        import os

        import opencode_models.cli as cli_mod

        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot(tmp, "zen", ["m-doc"])
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with (
                    mock.patch.object(
                        availability,
                        "fetch_served",
                        return_value={"zen": {"m-live"}, "go": set()},
                    ),
                    mock.patch.object(
                        availability,
                        "fetch_live",
                        return_value={"zen": {}, "go": {}},
                    ),
                    mock.patch.object(availability, "save_live", lambda *a: None),
                ):
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        served = cli_mod._current_offered(Path(tmp))
            out = buf.getvalue()
        self.assertEqual(served, {"m-live"})
        self.assertIn("returned no models", out)
        self.assertIn("personal", out)
        self.assertIn("enabled", out)

    def test_an_unreachable_api_falls_back_to_snapshots(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot(tmp, "zen", ["m-doc"])
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with mock.patch.object(
                    availability, "fetch_served", side_effect=OSError("down")
                ):
                    self.assertEqual(self._offered(tmp), {"m-doc"})

    def test_next_accepts_all_to_bypass_the_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run("--data", tmp, "next", "--all")
        self.assertEqual(code, 1)
        self.assertIn("run `fetch` first", out)

    def test_next_with_a_key_unlists_what_the_api_dropped(self):
        import os

        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {
                f"m{i:02d}" for i in range(1, 11)
            }
            try:
                with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                    served = {"zen": {f"m{i:02d}" for i in range(1, 10)}, "go": set()}
                    with (
                        mock.patch.object(
                            availability, "fetch_served", return_value=served
                        ),
                        mock.patch.object(
                            availability,
                            "fetch_live",
                            return_value={"zen": {}, "go": {}},
                        ),
                    ):
                        code, out = self._run("--data", tmp, "next", "--source", "zen")
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        self.assertNotIn("M10", out)
        self.assertNotIn("m10", out)
        self.assertIn("m09", out)


class AuthCommandTest(unittest.TestCase):
    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def _home(self, tmp):
        return {"XDG_CONFIG_HOME": str(Path(tmp) / "config"), "HOME": tmp}

    def test_auth_saves_the_prompted_key_and_verifies_it(self):
        import getpass

        import opencode_models.cli as cli_mod
        from opencode_models.commands import auth as auth_cmd

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                with (
                    mock.patch.object(getpass, "getpass", return_value="oc_sk_new"),
                    mock.patch.object(cli_mod.sys.stdin, "isatty", return_value=True),
                    mock.patch.object(
                        auth_cmd,
                        "fetch_served",
                        return_value={"zen": {"m"}, "go": set()},
                    ) as served,
                    mock.patch.object(
                        auth_cmd,
                        "fetch_live",
                        return_value={"zen": {"m": False}, "go": {}},
                    ) as live,
                ):
                    code, out = self._run("--data", tmp, "auth")
                    self.assertEqual(code, 0)
                    self.assertTrue(served.called)
                    self.assertTrue(live.called)
                    self.assertNotIn("oc_sk_new", out)
                    from opencode_models.auth import key_file

                    self.assertEqual(key_file().read_text(), "oc_sk_new")

    def test_auth_warns_when_either_live_endpoint_rejects(self):
        import getpass
        import urllib.error

        import opencode_models.cli as cli_mod
        from opencode_models.commands import auth as auth_cmd

        def _403(url):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                with (
                    mock.patch.object(getpass, "getpass", return_value="oc_sk_new"),
                    mock.patch.object(cli_mod.sys.stdin, "isatty", return_value=True),
                    mock.patch.object(
                        auth_cmd,
                        "fetch_served",
                        return_value={"zen": {"m"}, "go": set()},
                    ),
                    mock.patch.object(auth_cmd, "fetch_live", side_effect=_403),
                ):
                    code, out = self._run("--data", tmp, "auth")
                    self.assertEqual(code, 0)
                    self.assertIn("saved but not verified", out)
                    self.assertNotIn("verified live", out)

    def test_auth_refuses_an_empty_key(self):
        import getpass

        import opencode_models.cli as cli_mod

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                with (
                    mock.patch.object(getpass, "getpass", return_value="  "),
                    mock.patch.object(cli_mod.sys.stdin, "isatty", return_value=True),
                ):
                    code, out = self._run("--data", tmp, "auth")
        self.assertEqual(code, 1)
        from opencode_models.auth import key_file

        with mock.patch.dict(os.environ, self._home(tmp), clear=True):
            self.assertFalse(key_file().exists())

    def test_auth_needs_an_interactive_terminal(self):
        import opencode_models.cli as cli_mod

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                with mock.patch.object(cli_mod.sys.stdin, "isatty", return_value=False):
                    code, out = self._run("--data", tmp, "auth")
        self.assertEqual(code, 1)
        self.assertIn("interactive", out)

    def test_a_saved_key_is_used_when_the_environment_is_quiet(self):
        from opencode_models.auth import save_key

        with tempfile.TemporaryDirectory() as tmp:
            import json as _json

            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                save_key("oc_sk_file")
                (_models(Path(tmp)) / "l__m-live.json").write_text(
                    _json.dumps({"model": {"id": "l/m-live", "name": "M-LIVE"}})
                )
                with (
                    mock.patch.object(
                        availability,
                        "fetch_served",
                        return_value={"zen": {"m-live"}, "go": set()},
                    ),
                    mock.patch.object(
                        availability,
                        "fetch_live",
                        return_value={"zen": {"m-live": False}, "go": {}},
                    ) as live,
                ):
                    code, out = self._run("--data", tmp, "models")
        self.assertEqual(code, 0)
        self.assertTrue(live.called)
        self.assertIn("m-live", out)


class NoKeyNoticeTest(unittest.TestCase):
    def setUp(self):
        self._rp = __import__("unittest.mock", fromlist=["patch"]).patch(
            "opencode_models.cli.sync_mod.refresh_plan",
            return_value=("fresh", (), None),
        )
        self._rp.start()
        self.addCleanup(self._rp.stop)

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def test_list_without_a_key_warns_first(self):
        import os

        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            os.environ.pop("OPENCODE_API_KEY", None)
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {"m1", "m2", "m3"}
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--source", "zen", "--benchmark", "TB"
                )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        self.assertTrue(
            out.splitlines()[0].startswith("no API token found"), out.splitlines()[0]
        )

    def test_next_with_a_key_has_no_warning(self):
        import os

        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: {
                f"m{i:02d}" for i in range(1, 11)
            }
            try:
                with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                    served = {
                        t: {f"m{i:02d}" for i in range(1, 11)} for t in ("zen", "go")
                    }
                    with (
                        mock.patch.object(
                            availability, "fetch_served", return_value=served
                        ),
                        mock.patch.object(
                            availability,
                            "fetch_live",
                            return_value={"zen": {}, "go": {}},
                        ),
                    ):
                        code, out = self._run("--data", tmp, "next", "--source", "zen")
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        self.assertTrue(out.splitlines()[0].startswith("Next "), out.splitlines()[0])


class ModelsWidthTest(unittest.TestCase):
    def test_lines_fit_a_narrow_terminal(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plans"
            out.mkdir(parents=True, exist_ok=True)
            slugs = ["m1", "s" * 60]
            (out / "zen.json").write_text(
                json.dumps(
                    _plan_doc(
                        [
                            {
                                "model_id": s,
                                "name": s,
                                "input": 1.0,
                                "output": 1.0,
                                "cached_read": None,
                                "limit": None,
                                "free": False,
                            }
                            for s in slugs
                        ],
                        "zen",
                    )
                )
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main(["--data", tmp, "models"])
        self.assertEqual(code, 0)
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        self.assertLessEqual(max(map(len, lines)), 100)


class ModelsTableTest(unittest.TestCase):
    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def _snapshot(self, data_dir, tier, model_ids):
        import json

        out = Path(data_dir) / "plans"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{tier}.json").write_text(
            json.dumps(
                {
                    "meta": {
                        "source": "test",
                        "fetched_at": "2026-01-01T00:00:00+00:00",
                        "schema_rev": 1,
                        "plans": [tier],
                    },
                    "models": {
                        mid: {
                            "name": mid,
                            "free": False,
                            "tiers": [
                                {
                                    "label": None,
                                    "in": 1.0,
                                    "out": 1.0,
                                    "cached": None,
                                    "cached_write": None,
                                }
                            ],
                            "limits": None,
                            "requests_per_month": None,
                            "assumptions": None,
                        }
                        for mid in model_ids
                    },
                }
            )
        )

    def _catalog(self, tmp, names):
        import json

        out = Path(tmp) / "opencode"
        out.mkdir(parents=True, exist_ok=True)
        for mid, name in names.items():
            (out / f"{mid.replace('/', '__')}.json").write_text(
                json.dumps({"model": {"id": mid, "name": name}})
            )

    def test_models_is_one_sorted_table_of_endpoints_and_names(self):
        import re

        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot(tmp, "zen", ["m-bravo", "m-alpha"])
            self._snapshot(tmp, "go", ["m-bravo"])
            self._catalog(tmp, {"l/m-alpha": "Alpha", "l/m-bravo": "Bravo"})
            code, out = self._run("--data", tmp, "models")
        self.assertEqual(code, 0)
        self.assertIn("Endpoint", out)
        self.assertIn("Model", out)
        # no per-tier headlines: one sorted list instead
        self.assertIsNone(re.search(r"(?m)^(zen|go) · ", out))
        self.assertLess(out.index("Alpha"), out.index("Bravo"))
        # the endpoint reads as next shows it, preferring go where shared
        self.assertIn("opencode/m-alpha", out)
        self.assertIn("opencode-go/m-bravo", out)
        self.assertNotIn("opencode/m-bravo", out)

    def test_models_offline_marks_unknown_status(self):
        # no live record ever (docs snapshot only): --all marks the row
        # [?], never an invented [x]/[ ]; the default view carries no
        # status column at all
        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot(tmp, "zen", ["m-known"])
            self._catalog(tmp, {"l/m-known": "Known"})
            code, out = self._run("--data", tmp, "models")
            code_all, out_all = self._run("--data", tmp, "models", "--all")
        self.assertEqual(code, 0)
        head = next(l for l in out.splitlines() if "Endpoint" in l)
        self.assertNotIn("Avail", head)
        row = next(l for l in out.splitlines() if "m-known" in l)
        self.assertNotRegex(row, r"\[[ x?]\]")
        self.assertEqual(code_all, 0)
        row_all = next(l for l in out_all.splitlines() if "m-known" in l)
        self.assertTrue(row_all.lstrip().startswith("[?]"), row_all)

    def test_models_shows_served_status_markers(self):
        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot(tmp, "zen", ["m-live", "m-dead"])
            self._catalog(tmp, {"l/m-live": "Live", "l/m-dead": "Dead"})
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with (
                    mock.patch.object(
                        availability,
                        "fetch_served",
                        return_value={"zen": {"m-live"}, "go": set()},
                    ),
                    mock.patch.object(
                        availability,
                        "fetch_live",
                        return_value={
                            "zen": {"m-live": True, "m-dead": True},
                            "go": {},
                        },
                    ),
                ):
                    code, out = self._run("--data", tmp, "models")
                    code_all, out_all = self._run("--data", tmp, "models", "--all")
        self.assertEqual(code, 0)
        head = next(l for l in out.splitlines() if "Endpoint" in l)
        self.assertRegex(head, r"Endpoint\s+Model\s*$")
        self.assertNotIn("Avail", head)
        # default: served rows only, endpoint first, no status marker
        self.assertIn("m-live", out)
        self.assertNotIn("m-dead", out)
        row = next(l for l in out.splitlines() if "m-live" in l)
        self.assertTrue(row.lstrip().startswith("opencode/"), row)
        self.assertNotRegex(row, r"\[[ x?]\]")
        # --all: marker-first layout plus the known-unserved rows
        self.assertEqual(code_all, 0)
        head_all = next(l for l in out_all.splitlines() if "Endpoint" in l)
        self.assertRegex(head_all, r"Avail\s+Endpoint\s+Model\s*$")
        self.assertTrue(
            next(l for l in out_all.splitlines() if "m-live" in l)
            .lstrip()
            .startswith("[x]")
        )
        self.assertIn("m-dead", out_all)
        self.assertTrue(
            next(l for l in out_all.splitlines() if "m-dead" in l)
            .lstrip()
            .startswith("[ ]")
        )

    def test_models_hides_live_only_rows_the_docs_do_not_name(self):
        # m-ghost is served but documented nowhere (like the live "test"
        # id): with no catalog record and no docs name it stays hidden.
        import opencode_models.availability as availability

        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot(tmp, "zen", ["m-known"])
            self._catalog(tmp, {"l/m-known": "Known"})
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with (
                    mock.patch.object(
                        availability,
                        "fetch_served",
                        return_value={"zen": {"m-ghost", "m-known"}, "go": set()},
                    ),
                    mock.patch.object(
                        availability, "fetch_live", return_value={"zen": {}, "go": {}}
                    ),
                ):
                    code, out = self._run("--data", tmp, "models")
                    code_all, out_all = self._run("--data", tmp, "models", "--all")
        self.assertEqual(code, 0)
        self.assertIn("m-known", out)
        self.assertNotIn("m-ghost", out)  # junk like the served "test" id
        self.assertNotIn("m-ghost", out_all)

    def test_models_head_lines_name_the_origin_and_the_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot(tmp, "zen", ["m-alpha", "m-bravo"])
            self._catalog(tmp, {"l/m-alpha": "Alpha", "l/m-bravo": "Bravo"})
            code, out = self._run("--data", tmp, "models")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], f"snapshots in {tmp} (run `fetch` to refresh)")
        self.assertEqual(lines[1], "2 models · 2 zen · go unknown")
        self.assertEqual(lines[2], "")

    def test_models_fails_when_nothing_is_known(self):
        # empty data dir, no key: nothing to rank, so exit 1 with the
        # fetch hint — and the no-key stale notice stays down, so the
        # failure message is the whole of stdout
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run("--data", tmp, "models")
        self.assertEqual(code, 1)
        self.assertEqual(
            out.splitlines(),
            [
                f"no availability known in {Path(tmp)} — run `fetch` first "
                "(with OPENCODE_API_KEY for the live set)"
            ],
        )

    def test_models_prints_live_warnings_and_keeps_going(self):
        # an unreachable live list warns, then the view falls back to
        # the snapshots instead of failing
        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot(tmp, "zen", ["m-known"])
            self._catalog(tmp, {"l/m-known": "Known"})
            with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}):
                with mock.patch.object(
                    availability, "fetch_served", side_effect=OSError("down")
                ):
                    code, out = self._run("--data", tmp, "models")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(
            lines[0],
            "warning: live model list unreachable (down) — using the last snapshots",
        )
        self.assertEqual(lines[1], f"snapshots in {tmp} (run `fetch` to refresh)")

    def test_deepswe_version_survives_in_the_legend(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            for mid, tb, ds in (("a/m1", 90.0, 80.0), ("a/m2", 10.0, 20.0)):
                (_models(Path(tmp)) / f"{mid.replace('/', '__')}.json").write_text(
                    json.dumps(
                        {
                            "model": {
                                "id": mid,
                                "name": mid.split("/")[1].upper(),
                                "lab": "a",
                                "priceUsdPer1M": {
                                    "input": 1.0,
                                    "output": None,
                                    "cachedInput": None,
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
                                        "name": "DeepSWE",
                                        "version": "1.1",
                                        "score": ds,
                                        "metric": "acc",
                                        "source": "",
                                    },
                                ],
                            }
                        }
                    )
                )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--data", tmp, "scores"])
            text = out.getvalue()
        self.assertEqual(code, 0)
        head = next(l for l in text.splitlines() if "Model" in l and "Grade" in l)
        self.assertIn("Deepswe", head.split())
        self.assertNotIn("DeepSWE 1.1", head)
        self.assertIn("Deepswe = DeepSWE 1.1", text)


class TransportHygieneTest(unittest.TestCase):
    def test_api_get_closes_failed_responses(self):
        import gc
        import urllib.error
        import urllib.request
        import warnings

        from opencode_models.cli import _api_get

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", {}, io.BytesIO()
            )

        err = None
        get = _api_get("oc_sk_test")
        with mock.patch.object(urllib.request, "urlopen", boom):
            try:
                get("https://example.invalid/console/api/v2/config")
            except urllib.error.HTTPError as caught:
                err = caught
            else:
                self.fail("expected HTTPError")
        self.assertIsNotNone(err)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            del err
            gc.collect()
        leaked = [w for w in caught if issubclass(w.category, ResourceWarning)]
        self.assertEqual(leaked, [])


class VersionTest(unittest.TestCase):
    def test_version_flag_reports_the_installed_version(self):
        from importlib.metadata import version as pkg_version

        from opencode_models.cli import main

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(pkg_version("opencode-models"), buf.getvalue())

    def test_api_get_sends_a_versioned_user_agent(self):
        import urllib.request
        from importlib.metadata import version as pkg_version

        from opencode_models.cli import _api_get

        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["req"] = req
            raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, io.BytesIO())

        get = _api_get("oc_sk_test")
        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(urllib.error.HTTPError):
                get("https://example.invalid/console/api/v2/config")
        ua = next(v for k, v in seen["req"].header_items() if k.lower() == "user-agent")
        self.assertEqual(ua, f"opencode-models/{pkg_version('opencode-models')}")


class PriceModeTest(unittest.TestCase):
    """`--cost fix` (the fixed 20/60/20 blend) and `--cost est`.

    Fixture: one scored model, a Go tier at 1.0/5.0 in/out (no
    cache discount, so fix = .2*1 + .6*1 + .2*5 = 1.8 $/1M -> 180c) and
    assumptions giving (1000*1 + 9000*1 + 500*5)/1e6 = 0.0125 $/req -> 1c.
    """

    def _data(self, d):
        import json

        (d / "opencode").mkdir(parents=True, exist_ok=True)
        (d / "opencode" / "a__m1.json").write_text(
            json.dumps(
                {
                    "model": {
                        "id": "a/m1",
                        "name": "M1",
                        "lab": "a",
                        "priceUsdPer1M": {
                            "input": 1.0,
                            "output": 5.0,
                            "cachedInput": None,
                        },
                        "benchmarks": [
                            {
                                "name": "TB",
                                "score": 50.0,
                                "metric": "acc",
                                "source": "",
                            },
                            {
                                "name": "SWE-Bench Verified",
                                "score": 60.0,
                                "metric": "acc",
                                "source": "",
                            },
                        ],
                    }
                }
            )
        )
        (d / "plans").mkdir(parents=True, exist_ok=True)
        (d / "plans" / "go.json").write_text(
            json.dumps(
                {
                    "meta": {
                        "source": "test",
                        "fetched_at": "2026-10-22T00:00:00+00:00",
                    },
                    "models": {
                        "m1": {
                            "name": "M1",
                            "free": False,
                            "tiers": [
                                {
                                    "label": None,
                                    "in": 1.0,
                                    "out": 5.0,
                                    "cached": None,
                                    "cached_write": None,
                                }
                            ],
                            "limits": {"go": 60.0, "go_plus": 120.0},
                            "assumptions": {
                                "in": 1000,
                                "cached": 9000,
                                "out": 500,
                            },
                        }
                    },
                }
            )
        )

    def _run_list(self, tmp, *extra):
        import opencode_models.availability as availability

        buf = io.StringIO()
        orig = availability.load_roster
        availability.load_roster = lambda _d, name: {"m1"}
        try:
            with contextlib.redirect_stdout(buf):
                code = main(["--data", tmp, "list", *extra])
        finally:
            availability.load_roster = orig
        return code, buf.getvalue()

    def test_fix_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(Path(tmp))
            code, out = self._run_list(tmp, "--source", "go", "--cost", "fix")
        self.assertEqual(code, 0)
        self.assertIn("180", out)  # fix 1.8 $/1M

    def test_pricing_note_has_its_own_line(self):
        from opencode_models.cli import EST_NOTE, FIX_NOTE

        with tempfile.TemporaryDirectory() as tmp:
            self._data(Path(tmp))
            for args, note in (
                (["--source", "go", "--cost", "fix"], FIX_NOTE),
                (["--source", "go", "--cost", "est"], EST_NOTE),
            ):
                code, out = self._run_list(tmp, *args)
                self.assertEqual(code, 0)
                lines = out.splitlines()
                head = next(l for l in lines if "lines ·" in l)
                self.assertNotIn(note, head)
                self.assertIn(note, [ln.strip() for ln in lines])

    def test_default_price_is_est(self):
        from opencode_models.cli import EST_NOTE

        with tempfile.TemporaryDirectory() as tmp:
            self._data(Path(tmp))
            code, out = self._run_list(tmp, "--source", "go")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        head = next(l for l in lines if "lines ·" in l)
        self.assertNotIn(EST_NOTE, head)
        self.assertIn(EST_NOTE, [ln.strip() for ln in lines])

    def test_cost_line_names_the_ranking_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(Path(tmp))
            cases = (
                (
                    ["--source", "go", "--cost", "fix"],
                    "fixed 20/60/20 tokens per request",
                ),
                (["--source", "go", "--cost", "est"], "page-estimated"),
            )
            for args, basis in cases:
                code, out = self._run_list(tmp, *args)
                self.assertEqual(code, 0)
                line = next(l for l in out.splitlines() if "score/$ rank based on" in l)
                self.assertIn(basis, line)
                header = next(
                    l for l in out.splitlines() if "Score" in l and "Prov" in l
                )
                self.assertIn("¢/req", header)

    def test_est_uses_snapshot_cpr_on_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._data(Path(tmp))
            code, out = self._run_list(tmp, "--source", "go", "--cost", "est")
        self.assertEqual(code, 0)
        self.assertIn("¢/req", out)
        line = next(l for l in out.splitlines() if l.rstrip().endswith("M1"))
        self.assertRegex(line, r"\s1\s+.*M1$")  # 1.15c -> 1

    def test_est_falls_back_to_mix_on_zen(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._data(d)
            (d / "plans" / "zen.json").write_text(
                json.dumps(
                    _plan_doc(
                        [
                            {
                                "model_id": "m1",
                                "name": "M1",
                                "input": 1.0,
                                "output": 5.0,
                                "cached_read": None,
                                "limit": None,
                                "free": False,
                            }
                        ],
                        "zen",
                    )
                )
            )
            code, out = self._run_list(tmp, "--source", "zen", "--cost", "est")
        self.assertEqual(code, 0)
        line = next(l for l in out.splitlines() if l.rstrip().endswith("M1"))
        self.assertRegex(line, r"\s180\s+.*M1$")  # fix fallback


class ModelsListCoverageTest(unittest.TestCase):
    """Every served, catalog-nameable slug renders in `list --tables all`.

    The tier snapshots (built from the docs Endpoints tables) are what the
    plans offer; `models` shows them, so the full `list` view must render
    each one the catalog can name (free variants reach their base). A slug
    the roster drops instead vanishes from the ranking while still being
    offered - the defect class this pins down.
    """

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def _snapshot(self, data_dir, tier, model_ids):
        import json

        out = Path(data_dir) / "plans"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{tier}.json").write_text(
            json.dumps(
                _plan_doc(
                    [
                        {
                            "model_id": mid,
                            "name": mid,
                            "input": 1.0 if not mid.endswith("-free") else 0.0,
                            "output": 1.0 if not mid.endswith("-free") else 0.0,
                            "cached_read": None,
                            "limit": None,
                            "free": mid.endswith("-free"),
                        }
                        for mid in model_ids
                    ],
                    tier,
                )
            )
        )

    def test_served_free_variant_renders_in_full_list(self):
        import json
        import os

        def catalog(data_dir, mid, price, score):
            # two scored benchmarks: one axis alone is never ranked
            # (scoring.MIN_AXES), so a single-benchmark model cannot
            # stand in for a listable row here.
            doc = {
                "model": {
                    "id": mid,
                    "name": mid.split("/")[1].upper(),
                    "lab": mid.split("/")[0],
                    "priceUsdPer1M": {
                        "input": price,
                        "output": None,
                        "cachedInput": None,
                    },
                    "benchmarks": [
                        {
                            "name": name,
                            "version": version,
                            "score": score,
                            "metric": "acc",
                            "source": "",
                        }
                        for name, version in (
                            ("Terminal-Bench", "2.1"),
                            ("SWE-Bench", "Verified"),
                        )
                    ],
                }
            }
            (_models(Path(data_dir)) / f"{mid.replace('/', '__')}.json").write_text(
                json.dumps(doc)
            )

        with tempfile.TemporaryDirectory() as tmp:
            catalog(tmp, "l/m1", 1.0, 50.0)
            catalog(tmp, "l/m2", 3.0, 90.0)
            # m2-free is in the plans snapshot (the roster now) and
            # catalog-nameable via l/m2, so it must render in the full list.
            self._snapshot(tmp, "zen", ["m1", "m2-free"])
            self._snapshot(tmp, "go", [])
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENCODE_API_KEY", None)
                code, models_out = self._run("--data", tmp, "models")
            self.assertEqual(code, 0)
            self.assertIn("m2-free", models_out)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENCODE_API_KEY", None)
                code, list_out = self._run(
                    "--data", tmp, "list", "--source", "zen", "--tables", "all"
                )
        self.assertEqual(code, 0)
        self.assertIn("M2 [free]", list_out)
