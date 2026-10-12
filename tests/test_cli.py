"""CLI: fetch data, list benchmarks, render the frontier HTML."""

import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
import tempfile
from unittest import mock

from opencode_rank.cli import main


def _write_sample(data_dir: Path):
    import json

    def model_doc(mid, price, score):
        return json.dumps({
            "model": {
                "id": mid,
                "name": mid.split("/")[1].upper(),
                "lab": mid.split("/")[0],
                "priceUsdPer1M": {"input": price, "output": None,
                                  "cachedInput": None},
                "benchmarks": ([] if score is None else
                               [{"name": "TB", "score": score,
                                 "metric": "acc", "source": ""}]),
            }
        })

    (_models(data_dir) / "a__m1.json").write_text(model_doc("a/m1", 1.0, 50.0))
    (_models(data_dir) / "b__m2.json").write_text(model_doc("b/m2", 3.0, 90.0))
    (_models(data_dir) / "c__m3.json").write_text(model_doc("c/m3", 9.0, 40.0))


def _models(d: Path) -> Path:
    """Model fixtures live under <data>/opencode/, like production data."""
    models = d / "opencode"
    models.mkdir(parents=True, exist_ok=True)
    return models


def _write_grid(data_dir: Path):
    """Ten models: price i, Terminal-Bench score i² — value climbs with i."""
    import json

    for i in range(1, 11):
        doc = json.dumps({"model": {
            "id": f"l/m{i:02d}", "name": f"M{i:02d}", "lab": "l",
            "priceUsdPer1M": {"input": float(i), "output": None,
                              "cachedInput": None},
            "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                            "score": float(i * i), "metric": "acc",
                            "source": ""}],
        }})
        (_models(data_dir) / f"l__m{i:02d}.json").write_text(doc)


class CliTest(unittest.TestCase):
    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def test_benchmarks_lists_names_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            code, out = self._run("--data", tmp, "benchmarks")
        self.assertEqual(code, 0)
        self.assertIn("TB", out)
        self.assertIn("3", out)

    def test_render_writes_html_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            out_path = Path(tmp) / "report.html"
            code, _ = self._run(
                "--data", tmp, "render", "--benchmark", "TB",
                "--out", str(out_path),
            )
            html = out_path.read_text()
        self.assertEqual(code, 0)
        self.assertIn("Pareto", html)
        self.assertIn("M2", html)  # frontier winner renders its name

    def test_render_rejects_unknown_benchmark_and_lists_alternatives(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            code, out = self._run(
                "--data", tmp, "render", "--benchmark", "Nope",
                "--out", str(Path(tmp) / "x.html"),
            )
        self.assertEqual(code, 1)
        self.assertIn("TB", out)  # suggests what is available

    def test_render_defaults_to_best_covered_benchmark(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            out_path = Path(tmp) / "report.html"
            code, _ = self._run("--data", tmp, "render", "--out", str(out_path))
            html = out_path.read_text()
        self.assertEqual(code, 0)
        self.assertIn("TB", html)

    def test_fetch_uses_injected_fetcher_for_tests(self):
        calls = []

        def fake_get(url):
            calls.append(url)
            if url.endswith("/sitemap.xml"):
                return ('<urlset xmlns="http://www.sitemaps.org/schemas/'
                        'sitemap/0.9"><url><loc>https://opencode.ai/data/'
                        'x/y</loc></url></urlset>')
            return "{}"

        import opencode_rank.cli as cli

        with tempfile.TemporaryDirectory() as tmp:
            orig = cli.http_get
            cli.http_get = fake_get
            try:
                code, _ = self._run("--data", tmp, "fetch")
            finally:
                cli.http_get = orig
            saved = (_models(Path(tmp)) / "x__y.json").exists()
        self.assertEqual(code, 0)
        self.assertTrue(saved)

    def test_fetch_snapshots_tier_pricing(self):
        import json

        sitemap = ('<urlset xmlns="http://www.sitemaps.org/schemas/'
                   'sitemap/0.9"><url><loc>https://opencode.ai/data/'
                   'x/y</loc></url></urlset>')
        endpoints = ('<table><tr><th>Model</th><th>Model ID</th>'
                     '<th>Endpoint</th></tr>'
                     '<tr><td>M1</td><td>m1</td><td>x</td></tr></table>')
        pricing = ('<table><tr><th>Model</th><th>Input</th><th>Output</th>'
                   '<th>Cached Read</th></tr>'
                   '<tr><td>M1</td><td>$1.50</td><td>$7.50</td>'
                   '<td>$0.15</td></tr></table>')

        def fake_get(url):
            if url.endswith("/sitemap.xml"):
                return sitemap
            if "/docs/" in url:
                return endpoints + pricing
            return "{}"

        import opencode_rank.cli as cli

        with tempfile.TemporaryDirectory() as tmp:
            orig = cli.http_get
            cli.http_get = fake_get
            try:
                code, _ = self._run("--data", tmp, "fetch")
            finally:
                cli.http_get = orig
            snapshot = Path(tmp) / "tier_prices" / "go_prices.json"
            entries = json.loads(snapshot.read_text()) if snapshot.exists() else []
        self.assertEqual(code, 0)
        self.assertEqual([e["model_id"] for e in entries], ["m1"])

    def test_default_data_dir_honours_the_environment(self):
        import os

        import opencode_rank.cli as cli

        with mock.patch.dict(os.environ, {"OPENCODE_RANK_DATA": "/tmp/x"}):
            self.assertEqual(cli.default_data_dir(), Path("/tmp/x"))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cli.default_data_dir(),
                             Path(os.environ.get("XDG_DATA_HOME",
                                                 str(Path.home() / ".local" / "share")))
                             / "opencode-rank")

    def test_missing_data_dir_is_a_clear_error(self):
        code, out = self._run("--data", "/nonexistent/xyz", "benchmarks")
        self.assertEqual(code, 1)
        self.assertIn("no data", out.lower())

    def test_scores_reports_nothing_cached_and_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertIn("0", out)  # no cached arena scores yet
        self.assertNotIn("failed", out.lower())

    def test_scores_refresh_uses_injected_lookup_and_saves_cache(self):
        import opencode_rank.arena as arena

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = arena.lookup_arena_elo
            arena.lookup_arena_elo = lambda get: {"m1": 1500.0, "m2": 1400.0}
            try:
                code, out = self._run("--data", tmp, "scores", "--refresh")
            finally:
                arena.lookup_arena_elo = orig
            from opencode_rank.arena import ArenaCache
            cached = ArenaCache(Path(tmp) / "aiarena").load()
        self.assertEqual(code, 0)
        self.assertEqual(cached, {"m1": 1500.0, "m2": 1400.0})
        self.assertIn("2", out)

    def test_list_merged_benchmark_list_includes_arena_axis(self):
        import json
        from opencode_rank.arena import ArenaCache

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            ArenaCache(Path(tmp) / "aiarena").save({"m1": 1500.0})
            code, out = self._run("--data", tmp, "benchmarks")
        self.assertEqual(code, 0)
        self.assertIn("LMArena Elo", out)

    def test_list_source_sorted_by_score_per_price(self):
        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = sources.load_source
            sources.load_source = lambda name: {"m1", "m2", "m3"}
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--source", "zen", "--benchmark", "TB",
                    "--tables", "all",
                )
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        # m1: 50/1=50, m2: 90/3=30, m3: 40/9≈4.4 — but the list now culls:
        # m2 stays in best value, m1 is the worst score, m3 the highest cost
        self.assertLess(out.index("M2"), out.index("M1"))
        self.assertLess(out.index("M1"), out.index("M3"))
        self.assertIn("best value", out)
        self.assertIn("worst score", out)
        self.assertIn("highest cost", out)

    def test_list_filters_to_source_members_only(self):
        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = sources.load_source
            sources.load_source = lambda name: {"a/m1"}
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--source", "zen",
                    "--benchmark", "TB",
                )
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertIn("M1", out)
        self.assertNotIn("M2", out)

    def test_list_shows_score_price_and_ratio_columns(self):
        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = sources.load_source
            sources.load_source = lambda name: {"m1"}
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--source", "zen", "--benchmark", "TB",
                    "--tables", "all",
                )
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        line = next(l for l in out.splitlines() if l.rstrip().endswith("M1"))
        # everything integer: score 50, $1.00 shown as 100 cents, ratio 50
        self.assertRegex(line, r"\s50\s+TB\s+100\s+50\s+")

    def test_numbers_are_shown_as_rounded_integers(self):
        import json

        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "l__cheap.json").write_text(json.dumps({"model": {
                "id": "l/cheap", "name": "CHEAP", "lab": "l",
                "priceUsdPer1M": {"input": 0.0612, "output": None,
                                  "cachedInput": None},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": 88.8, "metric": "acc", "source": ""}],
            }}))
            # a peer, so Terminal-Bench has a range to read CHEAP's 88.8 on
            (_models(d) / "l__dear.json").write_text(json.dumps({"model": {
                "id": "l/dear", "name": "DEAR", "lab": "l",
                "priceUsdPer1M": {"input": 9.0, "output": None,
                                  "cachedInput": None},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": 40.0, "metric": "acc", "source": ""}],
            }}))
            # a third line, so the 30% cull leaves best value non-empty
            (_models(d) / "l__mid.json").write_text(json.dumps({"model": {
                "id": "l/mid", "name": "MID", "lab": "l",
                "priceUsdPer1M": {"input": 1.0, "output": None,
                                  "cachedInput": None},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": 60.0, "metric": "acc", "source": ""}],
            }}))
            orig = sources.load_source
            sources.load_source = lambda name: {"cheap", "dear", "mid"}
            try:
                code, out = self._run("--data", tmp, "list")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertIn("¢/1M", out)   # the column says what it counts
        line = next(l for l in out.splitlines() if l.rstrip().endswith("CHEAP"))
        # the TB2 cell shows the raw 88.8; the other two axes are blank;
        # $0.0612 shows as 6 cents, and the ratio divides the derived score
        # (100, the best of the 40-88.8 range) by the cost: 100/0.0612 → 1634
        self.assertRegex(line, r"\s88\.8\s+-\s+-\s+6\s+1634\s+\S+\s+CHEAP$")
        self.assertNotIn("0.0612", line)   # cents, never dollars

    def test_list_output_price_uses_output_axis(self):
        import json
        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            doc = json.dumps({
                "model": {
                    "id": "a/m1", "name": "M1", "lab": "a",
                    "priceUsdPer1M": {"input": 1.0, "output": 5.0,
                                      "cachedInput": None},
                    "benchmarks": [{"name": "TB", "score": 50.0,
                                    "metric": "acc", "source": ""}],
                }
            })
            (_models(d) / "a__m1.json").write_text(doc)
            orig = sources.load_source
            sources.load_source = lambda name: {"m1"}
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--source", "zen",
                    "--benchmark", "TB", "--price", "output",
                )
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        # output price $5.00 shown as 500 cents; ratio 50/5 → 10
        line = next(l for l in out.splitlines() if l.rstrip().endswith("M1"))
        self.assertRegex(line, r"\s50\s+TB\s+500\s+10\s+")

    def test_list_unknown_source_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            code, out = self._run("--data", tmp, "list", "--source", "nope")
        self.assertEqual(code, 1)
        self.assertIn("zen", out)

    def test_next_prints_an_ordered_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "next", "--count", "3",
                                  "--source", "all")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^next \d+ · ")
        self.assertIn("MODEL", out)
        self.assertIn("BAND", out)

    def test_next_keeps_the_billing_prose_out_of_the_output(self):
        # LIMITS.md is the place for what an allowance costs; the view must
        # not restate it on every run
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "next", "--source", "all")
        self.assertEqual(code, 0)
        for boilerplate in ("allowance runs out", "rate-limited",
                            "costs nothing", "Zen balance"):
            self.assertNotIn(boilerplate, out)

    def test_next_names_the_band_rather_than_a_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "next", "--source", "all")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"\b(payg|included|free)\b")

    def test_next_points_at_the_limits_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "next", "--source", "all")
        self.assertEqual(code, 0)
        self.assertIn("LIMITS.md", out)

    def test_next_numbers_the_picks_in_order(self):
        import re
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "next", "--count", "2",
                                  "--source", "all")
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
        self.assertIn("M10", out)          # score 100 at $10 is the best ratio
        self.assertNotIn("M01", out)

    def test_next_gates_on_strength_not_on_the_rank_marker(self):
        # the grid scores i², so the two strongest are M09 and M10 — and
        # neither is necessarily ++, because the marker divides by price
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "next", "--source", "all")
        self.assertEqual(code, 0)
        self.assertIn("M10", out)
        self.assertNotIn("M01", out)      # weakest, however cheap

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
                "--data", tmp, "list", "--tables", "all", "--source", "all", "--benchmark", "TB",
            )
        self.assertEqual(code, 0)
        self.assertIn("M1", out)
        self.assertIn("M2", out)
        self.assertIn("M3", out)


    def test_the_three_tables_are_visually_separated(self):
        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            slugs = {f"m{i:02d}" for i in range(1, 11)}
            orig = sources.load_source
            sources.load_source = lambda name: slugs
            try:
                code, out = self._run("--data", tmp, "list", "--tables", "all", "--source", "zen")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\n\n──"), 3)   # blank line before each table
        for title in ("best value", "worst score", "highest cost"):
            self.assertRegex(out, rf"(?m)^── [^\n]*{title}")
        # one column header per table, all five columns named, in order
        header = re.compile(r"(?m)^\s*TB2\s+TB4\s+SWE\s+¢/1M\s+SCORE/\$\s+"
                            r"PROV\s+MODEL\s*$")
        self.assertEqual(len(header.findall(out)), 3)

    def test_headers_are_bold_when_the_output_is_a_terminal(self):
        import opencode_rank.cli as cli

        with mock.patch.object(sys.stdout, "isatty", return_value=True):
            self.assertEqual(cli._style("hi"), "\033[1mhi\033[0m")
        self.assertEqual(cli._style("hi"), "hi")   # plain when redirected/piped

    def test_list_defaults_to_both_providers_merged(self):
        import json

        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)

            def model_doc(mid, price, score):
                return json.dumps({"model": {
                    "id": mid, "name": mid.split("/")[1].upper(), "lab": "l",
                    "priceUsdPer1M": {"input": price, "output": None,
                                      "cachedInput": None},
                    "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                    "score": score, "metric": "acc",
                                    "source": ""}],
                }})

            # ds: documented by both providers at different rates; the two
            # fillers are pricey enough to be culled and weak enough that
            # best value keeps both ds lines
            (_models(d) / "l__ds.json").write_text(model_doc("l/ds", 9.9, 50.0))
            (_models(d) / "l__e1.json").write_text(model_doc("l/e1", 5.0, 10.0))
            (_models(d) / "l__e2.json").write_text(model_doc("l/e2", 6.0, 20.0))
            prices_dir = d / "tier_prices"
            prices_dir.mkdir()
            (prices_dir / "zen_prices.json").write_text(json.dumps([
                {"tier": "zen", "model_id": "ds", "name": "DS", "input": 2.0,
                 "output": 2.0, "cached_read": 2.0, "limit": None,
                 "free": False},
            ]))
            (prices_dir / "go_prices.json").write_text(json.dumps([
                {"tier": "go", "model_id": "ds", "name": "DS", "input": 1.0,
                 "output": 1.0, "cached_read": 1.0, "limit": 60.0,
                 "free": False},
            ]))
            orig = sources.load_source
            sources.load_source = lambda name: {"ds", "e1", "e2"}
            try:
                code, out = self._run("--data", tmp, "list")   # no --source
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(re.findall(r"(?m)^\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+"
                                        r"\S+\s+DS$", out)), 2)
        self.assertTrue(any(line.endswith("go  DS") for line in lines))   # provider
        self.assertTrue(any(line.endswith("zen  DS") for line in lines))  # before name
        self.assertIn("PROV  MODEL", out)            # column header
        self.assertIn("included usage", out)         # go grants a monthly cap
        self.assertIn("pay-as-you-go", out)          # zen does not

    def test_list_all_adds_a_dash_provider_for_catalog_only_models(self):
        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = sources.load_source
            sources.load_source = lambda name: {"m1", "m2"}
            try:
                code, out = self._run("--data", tmp, "list", "--tables", "all", "--benchmark", "TB")
                self.assertEqual(code, 0)
                self.assertNotIn("M3", out)          # default = tiers only
                code, out = self._run("--data", tmp, "list", "--source", "all", "--tables", "all",
                                      "--benchmark", "TB")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertIn("M1", out)
        self.assertTrue(any(line.endswith("-  M3") for line in out.splitlines()),
                        "M3 is in neither tier, so it is listed under '-'")

    def test_list_collapses_a_pair_priced_the_same_into_one_line(self):
        import json

        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "l__ds.json").write_text(json.dumps({"model": {
                "id": "l/ds", "name": "DS", "lab": "l",
                "priceUsdPer1M": {"input": 9.9, "output": None,
                                  "cachedInput": None},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": 50.0, "metric": "acc", "source": ""}],
            }}))
            prices_dir = d / "tier_prices"
            prices_dir.mkdir()
            same = {"model_id": "ds", "name": "DS", "input": 1.0,
                    "output": 1.0, "cached_read": 1.0, "free": False}
            (prices_dir / "go_prices.json").write_text(
                json.dumps([dict(same, tier="go", limit=60.0)]))
            (prices_dir / "zen_prices.json").write_text(
                json.dumps([dict(same, tier="zen", limit=None)]))
            orig = sources.load_source
            sources.load_source = lambda name: {"ds"}
            try:
                code, out = self._run("--data", tmp, "list")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        lines = [l for l in out.splitlines() if l.rstrip().endswith("DS")]
        self.assertEqual(len(lines), 1, out)   # one line, not one per provider
        self.assertIn("go,zen", lines[0])

    def test_free_lines_show_a_zero_price_tag(self):
        import json

        import opencode_rank.sources as sources

        def doc(mid, price, score):
            return json.dumps({"model": {
                "id": mid, "name": mid.split("/")[1].upper(), "lab": "l",
                "priceUsdPer1M": {"input": price, "output": None,
                                  "cachedInput": None},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": score, "metric": "acc", "source": ""}],
            }})

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "l__gift.json").write_text(doc("l/gift", 5.0, 40.0))
            (_models(d) / "l__paid.json").write_text(doc("l/paid", 1.0, 50.0))
            prices_dir = d / "tier_prices"
            prices_dir.mkdir()
            (prices_dir / "go_prices.json").write_text(json.dumps([
                {"tier": "go", "model_id": "gift", "name": "Gift",
                 "input": 0.0, "output": 0.0, "cached_read": 0.0,
                 "limit": None, "free": True},
            ]))
            orig = sources.load_source
            sources.load_source = lambda name: {"gift", "paid"}
            try:
                code, out = self._run("--data", tmp, "list", "--tables", "all")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        gift = [l for l in out.splitlines() if "GIFT [free]" in l]
        self.assertEqual(len(gift), 1, out)
        self.assertNotIn("0.1", gift[0])       # the nominal price stays hidden
        self.assertRegex(gift[0], r"\s0\s+free\s+\S+\s+GIFT \[free\]$")

    def test_columns_stay_aligned_whatever_the_cell_widths(self):
        """Column widths are measured once, across the whole view.

        Fixed widths break the moment a cell outgrows its column — and the
        widest cells here (price '0.263175', provider 'go,zen') land in the
        *highest cost* table, so only a view-wide measurement keeps every
        table lined up with its own header.
        """
        import json

        import opencode_rank.sources as sources

        def doc(mid, price, score):
            return json.dumps({"model": {
                "id": mid, "name": mid.split("/")[1].upper(), "lab": "l",
                "priceUsdPer1M": {"input": price, "output": None,
                                  "cachedInput": None},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": score, "metric": "acc", "source": ""}],
            }})

        prices = {                    # price per $1M, cheapest last
            "gift": (5.0, 40.0),      # zero-priced in zen → shown at 0
            "narrow": (0.0612, 50.0),
            "mid": (0.1518, 55.0),
            "mid2": (0.165, 60.0),
            "wide": (0.263175, 65.0),  # widest price AND widest provider
            "top": (5.0, 70.0),
        }
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for mid, (price, score) in prices.items():
                (_models(d) / f"l__{mid}.json").write_text(doc(f"l/{mid}", price, score))
            prices_dir = d / "tier_prices"
            prices_dir.mkdir()
            (prices_dir / "go_prices.json").write_text(json.dumps([
                {"tier": "go", "model_id": "wide", "name": "Wide",
                 "input": 0.263175, "output": 0.263175, "cached_read": 0.263175,
                 "limit": 60.0, "free": False},
            ]))
            (prices_dir / "zen_prices.json").write_text(json.dumps([
                {"tier": "zen", "model_id": mid, "name": mid.title(),
                 "input": price, "output": price, "cached_read": price,
                 "limit": None, "free": mid == "gift"}
                for mid, (price, _) in prices.items()
            ]))
            orig = sources.load_source
            sources.load_source = lambda name: (
                {"wide"} if name == "go" else set(prices))
            try:
                code, out = self._run("--data", tmp, "list", "--tables", "all")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        lines = out.splitlines()
        header = next(l for l in lines if l.strip().startswith("TB2"))
        model_col = header.index("MODEL")
        # one row from each of the three tables, widest cells checked last
        for name in ("MID", "GIFT [free]", "TOP", "WIDE"):
            row = next(l for l in lines if l.rstrip().endswith(name))
            self.assertEqual(row.index(name), model_col, f"{name}: {row!r}")
        wide = next(l for l in lines if l.rstrip().endswith("WIDE"))
        self.assertIn("go,zen", wide)   # the whole token, not a truncation

    def test_tb4_only_scores_land_in_the_tb4_column(self):
        import json

        import opencode_rank.sources as sources

        def doc(mid, version, score):
            return json.dumps({"model": {
                "id": mid, "name": mid.split("/")[1].upper(), "lab": "l",
                "priceUsdPer1M": {"input": 1.0, "output": None,
                                  "cachedInput": None},
                "benchmarks": [{"name": "Terminal-Bench", "version": version,
                                "score": score, "metric": "acc", "source": ""}],
            }})

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "l__two.json").write_text(doc("l/two", "2.1", 50.0))
            (_models(d) / "l__four.json").write_text(doc("l/four", "4.0", 41.0))
            orig = sources.load_source
            sources.load_source = lambda name: {"two", "four"}
            try:
                code, out = self._run("--data", tmp, "list", "--tables", "all")
            finally:
                sources.load_source = orig
            self.assertEqual(code, 0)
            two = next(l for l in out.splitlines() if l.rstrip().endswith("TWO"))
            four = next(l for l in out.splitlines() if l.rstrip().endswith("FOUR"))
            # the two models score different axes: each score sits in its
            # own column, blank elsewhere, and no tag says which — the
            # column position does
            self.assertEqual(two.split()[1], "50")
            self.assertEqual(two.split()[2], "-")
            self.assertEqual(four.split()[1], "-")
            self.assertEqual(four.split()[2], "41")
            self.assertTrue(two.startswith(" "))      # marker prefix
            # --benchmark mode still shows one score plus the axis tag
            sources.load_source = lambda name: {"four"}
            try:
                code, single = self._run("--data", tmp, "list", "--tables", "all",
                                         "--benchmark", "Terminal-Bench 4.0")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertIn("TB4", next(l for l in single.splitlines()
                                  if l.rstrip().endswith("FOUR")))

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
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "list", "--source", "all")
        self.assertEqual(code, 0)
        self.assertNotIn("worst score", out)
        self.assertNotIn("highest cost", out)
        self.assertNotIn("M01", out)      # weakest
        self.assertNotIn("M10", out)      # priciest
        self.assertIn("M05", out)

    def test_the_default_view_culls_exactly_as_the_three_table_view(self):
        # same rows in best value either way — only the reporting differs
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            _c1, one = self._run("--data", tmp, "list", "--source", "all")
            _c2, three = self._run("--data", tmp, "list", "--source", "all",
                                   "--tables", "all")

        def best_value(text):
            body = text.split("── best value")[1].split("──")[0]
            return sorted(set(re.findall(r"\bM\d\d\b", body)))
        self.assertEqual(best_value(one), best_value(three))

    def test_tables_all_restores_the_three_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            code, out = self._run("--data", tmp, "list", "--source", "all",
                                  "--tables", "all")
        self.assertEqual(code, 0)
        for title in ("best value", "worst score", "highest cost"):
            self.assertIn(title, out)

    def test_tables_rejects_an_unknown_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_:
                    self._run("--data", tmp, "list", "--source", "all",
                              "--tables", "side")
        self.assertEqual(exit_.exception.code, 2)

    def test_list_prints_three_tables_in_order(self):
        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            slugs = {f"m{i:02d}" for i in range(1, 11)}
            orig = sources.load_source
            sources.load_source = lambda name: slugs
            try:
                code, out = self._run("--data", tmp, "list", "--tables", "all", "--source", "zen")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertEqual(out.count("SCORE/$"), 3)   # one column header per table
        self.assertLess(out.index("best value"), out.index("worst score"))
        self.assertLess(out.index("worst score"), out.index("highest cost"))

    def test_list_culls_are_disjoint_and_cover_every_line(self):
        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            slugs = {f"m{i:02d}" for i in range(1, 11)}
            orig = sources.load_source
            sources.load_source = lambda name: slugs
            try:
                code, out = self._run("--data", tmp, "list", "--tables", "all", "--source", "zen")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        for i in range(1, 11):          # each line printed exactly once
            self.assertEqual(out.count(f"M{i:02d}"), 1, f"M{i:02d}")

    def test_list_splits_best_value_into_free_included_and_payg(self):
        import json

        import opencode_rank.sources as sources
        from opencode_rank.pricing import PriceEntry

        def doc(mid, price, score):
            return json.dumps({"model": {
                "id": mid, "name": mid.split("/")[1].upper(), "lab": mid.split("/")[0],
                "priceUsdPer1M": {"input": price, "output": None, "cachedInput": None},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": score, "metric": "acc", "source": ""}],
            }})

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for mid, price, score in [("l/p1", 2.0, 20.0), ("l/p2", 3.0, 30.0),
                                      ("l/p3", 4.0, 40.0), ("l/p4", 5.0, 50.0),
                                      ("l/p5", 6.0, 60.0), ("l/inc", 1.0, 70.0),
                                      ("l/gift", 5.0, 55.0)]:
                (_models(d) / f"{mid.replace('/', '__')}.json").write_text(doc(mid, price, score))
            prices_dir = d / "tier_prices"
            prices_dir.mkdir()
            (prices_dir / "go_prices.json").write_text(json.dumps([
                {"tier": "go", "model_id": "inc", "name": "Inc", "input": 1.0,
                 "output": 1.0, "cached_read": 1.0, "limit": 60.0, "free": False},
                {"tier": "go", "model_id": "gift", "name": "Gift", "input": 0.0,
                 "output": 0.0, "cached_read": 0.0, "limit": None, "free": True},
            ]))
            orig = sources.load_source
            sources.load_source = lambda name: {"p1", "p2", "p3", "p4", "p5",
                                                "inc", "gift"}
            try:
                code, out = self._run("--data", tmp, "list", "--tables", "all", "--source", "go")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertLess(out.index("free — use first"),
                        out.index("included usage"))
        self.assertLess(out.index("included usage"),
                        out.index("pay-as-you-go"))
        self.assertLess(out.index("pay-as-you-go"), out.index("worst score"))
        self.assertIn("GIFT [free]", out)      # tagged (zero price tag is
        # covered by test_free_lines_show_a_zero_price_tag)

    def test_list_shows_the_representative_cost_not_the_bare_input(self):
        import json

        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "a__m1.json").write_text(json.dumps({"model": {
                "id": "a/m1", "name": "M1", "lab": "a",
                "priceUsdPer1M": {"input": 1.0, "output": 2.0, "cachedInput": 0.1},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": 50.0, "metric": "acc", "source": ""}],
            }}))
            # a peer, so the benchmark has a range and the score is a
            # position on it rather than undefined
            (_models(d) / "a__m2.json").write_text(json.dumps({"model": {
                "id": "a/m2", "name": "M2", "lab": "a",
                "priceUsdPer1M": {"input": 1.0, "output": 1.0, "cachedInput": 1.0},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": 25.0, "metric": "acc", "source": ""}],
            }}))
            # a third line, so the 30% cull leaves best value non-empty
            (_models(d) / "a__m3.json").write_text(json.dumps({"model": {
                "id": "a/m3", "name": "M3", "lab": "a",
                "priceUsdPer1M": {"input": 0.5, "output": 0.5, "cachedInput": 0.5},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": 37.5, "metric": "acc", "source": ""}],
            }}))
            orig = sources.load_source
            sources.load_source = lambda name: {"m1", "m2", "m3"}
            try:
                code, out = self._run("--data", tmp, "list", "--source", "zen")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        # 0.2·1.0 + 0.6·0.1 + 0.2·2.0 = 0.66 → 66 cents. The axis cell is
        # the raw 50; the ratio divides the derived score (100, the best of
        # the 25–50 range) by the cost: 100/0.66 → 152
        line = next(l for l in out.splitlines() if l.rstrip().endswith("M1"))
        self.assertRegex(line, r"\s50\s+-\s+-\s+66\s+152\s+")

    def test_list_gives_each_priced_variant_its_own_line(self):
        import json

        import opencode_rank.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "l__ds.json").write_text(json.dumps({"model": {
                "id": "l/ds", "name": "DS", "lab": "l",
                "priceUsdPer1M": {"input": 1.74, "output": None, "cachedInput": None},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": 50.0, "metric": "acc", "source": ""}],
            }}))
            prices_dir = d / "tier_prices"
            prices_dir.mkdir()
            (prices_dir / "go_prices.json").write_text(json.dumps([
                {"tier": "go", "model_id": "ds", "name": "DS (Off-Peak)",
                 "input": 0.66, "output": 1.98, "cached_read": 0.022,
                 "limit": 15.0, "free": False},
                {"tier": "go", "model_id": "ds", "name": "DS (Peak)",
                 "input": 1.32, "output": 3.96, "cached_read": 0.044,
                 "limit": 15.0, "free": False},
            ]))
            orig = sources.load_source
            sources.load_source = lambda name: {"ds"}
            try:
                code, out = self._run("--data", tmp, "list", "--tables", "all", "--source", "go")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertIn("(Off-Peak)", out)
        self.assertIn("(Peak)", out)


if __name__ == "__main__":
    unittest.main()


class ScoresCommandTest(unittest.TestCase):
    """`scores` shows what a model was measured on and what it derives to."""

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def _grid(self, tmp):
        import json
        for mid, price, score in [("a/m1", 1.0, 90.0), ("a/m2", 1.0, 50.0),
                                  ("a/m3", 1.0, 10.0)]:
            (_models(Path(tmp)) / f"{mid.replace('/', '__')}.json").write_text(
                json.dumps({"model": {
                    "id": mid, "name": mid.split("/")[1].upper(), "lab": "a",
                    "priceUsdPer1M": {"input": price, "output": None,
                                      "cachedInput": None},
                    "benchmarks": [
                        {"name": "Terminal-Bench", "version": "2.1",
                         "score": score, "metric": "acc", "source": ""},
                        {"name": "SWE-Bench", "version": "Verified",
                         "score": score / 2, "metric": "acc", "source": ""}],
                }}))

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
        line = next(l for l in out.splitlines() if " M1 " in l or l.strip().endswith("M1"))
        self.assertIn("90", line)
        self.assertIn("100", line)

    def test_it_does_not_list_benchmarks_we_do_not_score_on(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            (_models(Path(tmp)) / "a__m4.json").write_text(json.dumps({
                "model": {"id": "a/m4", "name": "M4", "lab": "a",
                          "priceUsdPer1M": {"input": 1.0, "output": None,
                                            "cachedInput": None},
                          "benchmarks": [
                              {"name": "Terminal-Bench", "version": "2.1",
                               "score": 70.0, "metric": "a", "source": ""},
                              {"name": "SEC-Bench Pro", "score": 17.7,
                               "metric": "a", "source": ""}]}}))
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertNotIn("SEC-Bench", out)
        self.assertNotIn("17.7", out)   # not counted, so not listed

    def test_it_says_where_the_scales_come_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._grid(tmp)
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertIn("scores", out.lower())
        self.assertIn("median", out.lower())

    def test_a_model_with_no_benchmarks_is_listed_as_unscored(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            (_models(Path(tmp)) / "a__m1.json").write_text(json.dumps({
                "model": {"id": "a/m1", "name": "M1", "lab": "a",
                          "priceUsdPer1M": {"input": 1.0, "output": None,
                                            "cachedInput": None},
                          "benchmarks": []}}))
            code, out = self._run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        self.assertIn("M1", out)
        self.assertRegex(out, r"(?i)(no benchmarks|none|—)")
