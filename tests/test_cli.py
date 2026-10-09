"""CLI: fetch data, list benchmarks, render the frontier HTML."""

import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
import tempfile
from unittest import mock

from modelpareto.cli import main


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

    (data_dir / "a__m1.json").write_text(model_doc("a/m1", 1.0, 50.0))
    (data_dir / "b__m2.json").write_text(model_doc("b/m2", 3.0, 90.0))
    (data_dir / "c__m3.json").write_text(model_doc("c/m3", 9.0, 40.0))


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
        (data_dir / f"l__m{i:02d}.json").write_text(doc)


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

        import modelpareto.cli as cli

        with tempfile.TemporaryDirectory() as tmp:
            orig = cli.http_get
            cli.http_get = fake_get
            try:
                code, _ = self._run("--data", tmp, "fetch")
            finally:
                cli.http_get = orig
            saved = (Path(tmp) / "x__y.json").exists()
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

        import modelpareto.cli as cli

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
        import modelpareto.arena as arena

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = arena.lookup_arena_elo
            arena.lookup_arena_elo = lambda get: {"m1": 1500.0, "m2": 1400.0}
            try:
                code, out = self._run("--data", tmp, "scores", "--refresh")
            finally:
                arena.lookup_arena_elo = orig
            cached = arena.ArenaCache(Path(tmp)).load() if hasattr(arena, "ArenaCache") else {}
            from modelpareto.arena import ArenaCache
            cached = ArenaCache(Path(tmp)).load()
        self.assertEqual(code, 0)
        self.assertEqual(cached, {"m1": 1500.0, "m2": 1400.0})
        self.assertIn("2", out)

    def test_list_merged_benchmark_list_includes_arena_axis(self):
        import json
        from modelpareto.arena import ArenaCache

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            ArenaCache(Path(tmp)).save({"m1": 1500.0})
            code, out = self._run("--data", tmp, "benchmarks")
        self.assertEqual(code, 0)
        self.assertIn("LMArena Elo", out)

    def test_list_source_sorted_by_score_per_price(self):
        import modelpareto.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = sources.load_source
            sources.load_source = lambda name: {"m1", "m2", "m3"}
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--source", "zen", "--benchmark", "TB",
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
        import modelpareto.sources as sources

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
        import modelpareto.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = sources.load_source
            sources.load_source = lambda name: {"m1"}
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--source", "zen", "--benchmark", "TB",
                )
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertIn("50", out)   # score
        self.assertIn("1", out)    # price
        self.assertIn("50.0", out) # ratio score/price

    def test_list_output_price_uses_output_axis(self):
        import json
        import modelpareto.sources as sources

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
            (d / "a__m1.json").write_text(doc)
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
        self.assertIn("5", out)    # output price shown
        self.assertIn("10.0", out) # 50/5 ratio

    def test_list_unknown_source_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            code, out = self._run("--data", tmp, "list", "--source", "nope")
        self.assertEqual(code, 1)
        self.assertIn("zen", out)

    def test_list_source_all_includes_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            code, out = self._run(
                "--data", tmp, "list", "--source", "all", "--benchmark", "TB",
            )
        self.assertEqual(code, 0)
        self.assertIn("M1", out)
        self.assertIn("M2", out)
        self.assertIn("M3", out)


    def test_the_three_tables_are_visually_separated(self):
        import modelpareto.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            slugs = {f"m{i:02d}" for i in range(1, 11)}
            orig = sources.load_source
            sources.load_source = lambda name: slugs
            try:
                code, out = self._run("--data", tmp, "list", "--source", "zen")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\n\n──"), 3)   # blank line before each table
        for title in ("best value", "worst score", "highest cost"):
            self.assertRegex(out, rf"(?m)^── [^\n]*{title}")
        self.assertEqual(out.count("SCORE/$"), 3)  # highlighted column headers
        self.assertEqual(len(re.findall(r"(?m)^ SCORE ", out)), 3)

    def test_headers_are_bold_when_the_output_is_a_terminal(self):
        import modelpareto.cli as cli

        with mock.patch.object(sys.stdout, "isatty", return_value=True):
            self.assertEqual(cli._style("hi"), "\033[1mhi\033[0m")
        self.assertEqual(cli._style("hi"), "hi")   # plain when redirected/piped

    def test_list_prints_three_tables_in_order(self):
        import modelpareto.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            slugs = {f"m{i:02d}" for i in range(1, 11)}
            orig = sources.load_source
            sources.load_source = lambda name: slugs
            try:
                code, out = self._run("--data", tmp, "list", "--source", "zen")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertEqual(out.count("SCORE/$"), 3)   # one column header per table
        self.assertLess(out.index("best value"), out.index("worst score"))
        self.assertLess(out.index("worst score"), out.index("highest cost"))

    def test_list_culls_are_disjoint_and_cover_every_line(self):
        import modelpareto.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_grid(Path(tmp))
            slugs = {f"m{i:02d}" for i in range(1, 11)}
            orig = sources.load_source
            sources.load_source = lambda name: slugs
            try:
                code, out = self._run("--data", tmp, "list", "--source", "zen")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        for i in range(1, 11):          # each line printed exactly once
            self.assertEqual(out.count(f"M{i:02d}"), 1, f"M{i:02d}")

    def test_list_splits_best_value_into_free_included_and_payg(self):
        import json

        import modelpareto.sources as sources
        from modelpareto.pricing import PriceEntry

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
                (d / f"{mid.replace('/', '__')}.json").write_text(doc(mid, price, score))
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
                code, out = self._run("--data", tmp, "list", "--source", "go")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertLess(out.index("free — use first"),
                        out.index("included usage"))
        self.assertLess(out.index("included usage"),
                        out.index("pay-as-you-go"))
        self.assertLess(out.index("pay-as-you-go"), out.index("worst score"))
        self.assertIn("GIFT [free]", out)      # tagged, and...
        self.assertIn("0.1", out)              # ...priced at 1/10 of $1

    def test_list_shows_the_representative_cost_not_the_bare_input(self):
        import json

        import modelpareto.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a__m1.json").write_text(json.dumps({"model": {
                "id": "a/m1", "name": "M1", "lab": "a",
                "priceUsdPer1M": {"input": 1.0, "output": 2.0, "cachedInput": 0.1},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": 50.0, "metric": "acc", "source": ""}],
            }}))
            orig = sources.load_source
            sources.load_source = lambda name: {"m1"}
            try:
                code, out = self._run("--data", tmp, "list", "--source", "zen")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        # 0.2·1.0 + 0.6·0.1 + 0.2·2.0 = 0.66
        self.assertIn("0.66", out)

    def test_list_gives_each_priced_variant_its_own_line(self):
        import json

        import modelpareto.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "l__ds.json").write_text(json.dumps({"model": {
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
                code, out = self._run("--data", tmp, "list", "--source", "go")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertIn("(Off-Peak)", out)
        self.assertIn("(Peak)", out)


if __name__ == "__main__":
    unittest.main()
