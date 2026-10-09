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

        import modelpareto.cli as cli

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
            from modelpareto.arena import ArenaCache
            cached = ArenaCache(Path(tmp) / "aiarena").load()
        self.assertEqual(code, 0)
        self.assertEqual(cached, {"m1": 1500.0, "m2": 1400.0})
        self.assertIn("2", out)

    def test_list_merged_benchmark_list_includes_arena_axis(self):
        import json
        from modelpareto.arena import ArenaCache

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            ArenaCache(Path(tmp) / "aiarena").save({"m1": 1500.0})
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
        line = next(l for l in out.splitlines() if l.rstrip().endswith("M1"))
        # everything integer: score 50, $1.00 shown as 100 cents, ratio 50
        self.assertRegex(line, r"\s50\s+TB\s+100\s+50\s+")

    def test_numbers_are_shown_as_rounded_integers(self):
        import json

        import modelpareto.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "l__cheap.json").write_text(json.dumps({"model": {
                "id": "l/cheap", "name": "CHEAP", "lab": "l",
                "priceUsdPer1M": {"input": 0.0612, "output": None,
                                  "cachedInput": None},
                "benchmarks": [{"name": "Terminal-Bench", "version": "2.1",
                                "score": 88.8, "metric": "acc", "source": ""}],
            }}))
            orig = sources.load_source
            sources.load_source = lambda name: {"cheap"}
            try:
                code, out = self._run("--data", tmp, "list")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertIn("¢/1M", out)   # the column says what it counts
        line = next(l for l in out.splitlines() if l.rstrip().endswith("CHEAP"))
        # 88.8 rounds to 89; $0.0612 shows as 6 cents; the ratio rounds too
        self.assertRegex(line, r"\s89\s+TB\s+6\s+1451\s+\S+\s+CHEAP$")
        self.assertNotIn("88.8", line)
        self.assertNotIn("0.0612", line)

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
        # one column header per table, all five columns named, in order
        header = re.compile(r"(?m)^\s*SCORE\s+BENCH\s+¢/1M\s+SCORE/\$\s+PROV"
                            r"\s+MODEL\s*$")
        self.assertEqual(len(header.findall(out)), 3)

    def test_headers_are_bold_when_the_output_is_a_terminal(self):
        import modelpareto.cli as cli

        with mock.patch.object(sys.stdout, "isatty", return_value=True):
            self.assertEqual(cli._style("hi"), "\033[1mhi\033[0m")
        self.assertEqual(cli._style("hi"), "hi")   # plain when redirected/piped

    def test_list_defaults_to_both_providers_merged(self):
        import json

        import modelpareto.sources as sources

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
        self.assertEqual(len(re.findall(r"(?m)^\s+\S+\s+\S+\s+\S+\s+\S+\s+"
                                        r"\S+  DS$", out)), 2)
        self.assertTrue(any(line.endswith("go  DS") for line in lines))   # provider
        self.assertTrue(any(line.endswith("zen  DS") for line in lines))  # before name
        self.assertIn("PROV  MODEL", out)            # column header
        self.assertIn("included usage", out)         # go grants a monthly cap
        self.assertIn("pay-as-you-go", out)          # zen does not

    def test_list_all_adds_a_dash_provider_for_catalog_only_models(self):
        import modelpareto.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _write_sample(Path(tmp))
            orig = sources.load_source
            sources.load_source = lambda name: {"m1", "m2"}
            try:
                code, out = self._run("--data", tmp, "list", "--benchmark", "TB")
                self.assertEqual(code, 0)
                self.assertNotIn("M3", out)          # default = tiers only
                code, out = self._run("--data", tmp, "list", "--source", "all",
                                      "--benchmark", "TB")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertIn("M1", out)
        self.assertTrue(any(line.endswith("-  M3") for line in out.splitlines()),
                        "M3 is in neither tier, so it is listed under '-'")

    def test_list_collapses_a_pair_priced_the_same_into_one_line(self):
        import json

        import modelpareto.sources as sources

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

        import modelpareto.sources as sources

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
                code, out = self._run("--data", tmp, "list")
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

        import modelpareto.sources as sources

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
                code, out = self._run("--data", tmp, "list")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        lines = out.splitlines()
        header = next(l for l in lines if l.strip().startswith("SCORE"))
        model_col = header.index("MODEL")
        # one row from each of the three tables, widest cells checked last
        for name in ("MID", "GIFT [free]", "TOP", "WIDE"):
            row = next(l for l in lines if l.rstrip().endswith(name))
            self.assertEqual(row.index(name), model_col, f"{name}: {row!r}")
        wide = next(l for l in lines if l.rstrip().endswith("WIDE"))
        self.assertIn("go,zen", wide)   # the whole token, not a truncation

    def test_tb4_only_models_get_their_own_tag_and_axis(self):
        import json

        import modelpareto.sources as sources

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
                code, out = self._run("--data", tmp, "list")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        lines = out.splitlines()
        four = next(l for l in lines if l.rstrip().endswith("FOUR"))
        two = next(l for l in lines if l.rstrip().endswith("TWO"))
        self.assertIn("TB4", four)      # 4.0 is labelled apart from 2.1
        self.assertIn("  TB", two)
        self.assertNotIn("TB4", two)
        self.assertIn("TB 4.0 fill", out)   # the header names the axis

    def test_conflicts_reports_disagreements_with_tiers_and_verdicts(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "a__m.json").write_text(json.dumps({"model": {
                "id": "a/m", "name": "M",
                "benchmarks": [{"name": "SWE-Bench", "version": "Verified",
                                "score": 74.0, "metric": "pass@1",
                                "source":
                                "https://artificialanalysis.ai/models/m"}]}}))
            over = d / "overrides"
            over.mkdir()
            (over / "a__m.json").write_text(json.dumps({"model": {
                "id": "a/m", "benchmarks": [
                    {"name": "SWE-Bench", "version": "Verified", "score": 99.0,
                     "source": "https://random-blog.example/post",
                     "note": "a blog says the vendor claimed more"}]}}))
            code, out = self._run("--data", tmp, "conflicts")
        self.assertEqual(code, 0)
        self.assertIn("a/m", out)
        self.assertIn("74", out)          # upstream value
        self.assertIn("99", out)          # the override's claim
        self.assertIn("upstream kept", out)   # tier-3 loses to independent
        self.assertIn("1 curated models", out)

    def test_curated_rows_carry_an_asterisk(self):
        import json
        import modelpareto.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "a__m1.json").write_text(json.dumps({"model": {
                "id": "a/m1", "name": "M1",
                "priceUsdPer1M": {"input": 1.0, "output": None,
                                  "cachedInput": None},
                "benchmarks": [{"name": "TB", "score": 50.0,
                                "metric": "acc", "source": ""}]}}))
            over = d / "overrides"
            over.mkdir()
            (over / "a__m1.json").write_text(json.dumps({"model": {
                "id": "a/m1", "benchmarks": [
                    {"name": "Humanity's Last Exam", "score": 46.5,
                     "source": "https://artificialanalysis.ai/models/m1"}]}}))
            orig = sources.load_source
            sources.load_source = lambda name: {"m1"}
            try:
                code, out = self._run(
                    "--data", tmp, "list", "--source", "zen", "--benchmark", "TB",
                )
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        line = next(l for l in out.splitlines() if "M1" in l and "TB" in l)
        self.assertTrue(line.rstrip().endswith("M1 *"), line)   # curated mark

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
                code, out = self._run("--data", tmp, "list", "--source", "go")
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

        import modelpareto.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (_models(d) / "a__m1.json").write_text(json.dumps({"model": {
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
        # 0.2·1.0 + 0.6·0.1 + 0.2·2.0 = 0.66 → 66 cents; ratio 50/0.66 → 76
        line = next(l for l in out.splitlines() if l.rstrip().endswith("M1"))
        self.assertRegex(line, r"\s50\s+TB\s+66\s+76\s+")

    def test_list_gives_each_priced_variant_its_own_line(self):
        import json

        import modelpareto.sources as sources

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
                code, out = self._run("--data", tmp, "list", "--source", "go")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        self.assertIn("(Off-Peak)", out)
        self.assertIn("(Peak)", out)


if __name__ == "__main__":
    unittest.main()
