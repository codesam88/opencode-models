"""CLI: fetch data, list benchmarks, render the frontier HTML."""

import contextlib
import io
import unittest
from pathlib import Path
import tempfile

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

    def test_missing_data_dir_is_a_clear_error(self):
        code, out = self._run("--data", "/nonexistent/xyz", "benchmarks")
        self.assertEqual(code, 1)
        self.assertIn("no data", out.lower())

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
        # m1: 50/1=50, m2: 90/3=30, m3: 40/9≈4.4 → that order
        self.assertLess(out.index("M1"), out.index("M2"))
        self.assertLess(out.index("M2"), out.index("M3"))

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


if __name__ == "__main__":
    unittest.main()
