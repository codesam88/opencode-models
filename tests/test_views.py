"""The basic requirements of each console view, pinned in one place.

Every view gets its exact columns, its ordering rule, and its one content
guarantee, read back off the printed text. A view change must come here
first: if the columns move, this file says so on purpose rather than by
accident. (The SCORE/$ column vanished from `list` exactly this way once -
restored, and pinned here so it cannot go quietly again.)
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from opencode_models.cli import main

#: Hermetic fixtures: keep a saved user key (or env key) from sending
#: `list`/`next` to the live API and filtering the fixtures away.
os.environ.pop("OPENCODE_API_KEY", None)
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="opencode-models-test-home-")


def _doc(mid, price, benches):
    return json.dumps(
        {
            "model": {
                "id": mid,
                "name": mid.split("/")[1].upper(),
                "lab": mid.split("/")[0],
                "priceUsdPer1M": {"input": price, "output": None, "cachedInput": None},
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


def _write_models(data_dir: Path, *models):
    out = data_dir / "opencode"
    out.mkdir(parents=True, exist_ok=True)
    for mid, price, benches in models:
        (out / f"{mid.replace('/', '__')}.json").write_text(_doc(mid, price, benches))


def _run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


def _header(out):
    return next(l for l in out.splitlines() if "MODEL" in l and "SCORE" in l)


def _section_parts(out, title):
    """Tokenised data rows under a ── title ── line.

    Split on the title line, not the dashes: cutting at ── pairs ends at
    the title's own closing pair and yields nothing, which then passes
    vacuously. Starts with ── so a missing title fails loudly.
    """
    lines = out.splitlines()
    start = next(i for i, l in enumerate(lines) if f"── {title}" in l)
    rows = []
    for line in lines[start + 1 :]:
        if line.startswith("──"):
            break
        parts = line.split()
        if len(parts) >= 5 and parts[0] in ("++", "+", "o", "--", "·", "-"):
            rows.append(parts)
    return rows


def _grid(tmp):
    """Four scored models: M1 is cheapest to cull (priciest and weakest,
    cost claims first), best value keeps M3 then M4 in value order."""
    _write_models(
        Path(tmp),
        (
            "l/m1",
            9.0,
            [("Terminal-Bench", "2.1", 10.0), ("SWE-Bench", "Verified", 10.0)],
        ),
        (
            "l/m2",
            4.0,
            [("Terminal-Bench", "2.1", 50.0), ("SWE-Bench", "Verified", 50.0)],
        ),
        (
            "l/m3",
            1.0,
            [("Terminal-Bench", "2.1", 90.0), ("SWE-Bench", "Verified", 90.0)],
        ),
        (
            "l/m4",
            2.0,
            [("Terminal-Bench", "2.1", 70.0), ("SWE-Bench", "Verified", 70.0)],
        ),
    )


class ListViewContract(unittest.TestCase):
    # grade rides as an unheaded prefix, MODEL closes the row
    COLUMNS = ["SCORE", "¢/1M", "SCORE/$", "PROV", "MODEL"]

    def test_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            _grid(tmp)
            code, out = _run("--data", tmp, "list", "--source", "all")
        self.assertEqual(code, 0)
        tokens = _header(out).split()
        self.assertEqual(tokens, self.COLUMNS)

    def test_rows_fit_a_narrow_terminal(self):
        # a 40-char shown name plus a 60-char unshown one: rows fit 100
        # because the grid measures what it prints, not the catalog
        import json

        with tempfile.TemporaryDirectory() as tmp:
            _grid(tmp)
            out_dir = Path(tmp) / "opencode"

            def benches(score):
                return [
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
                ]

            (out_dir / f"{'a_' + 'm' * 40}.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "a/" + "m" * 40,
                            "name": "M" * 40,
                            "lab": "a",
                            "priceUsdPer1M": {
                                "input": 1.0,
                                "output": None,
                                "cachedInput": None,
                            },
                            "benchmarks": benches(90.0),
                        }
                    }
                )
            )
            (out_dir / "a___u64.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "a/" + "u" * 60,
                            "name": "U" * 60,
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
            code, out = _run(
                "--data", tmp, "list", "--source", "all", "--tables", "all"
            )
        self.assertEqual(code, 0)
        self.assertIn("M" * 40, out)
        self.assertNotIn("U" * 60, out)
        raw = [
            l
            for l in out.splitlines()
            if l.split() and l.split()[0] in ("++", "+", "o", "--", "·", "-")
        ]
        self.assertTrue(raw)
        self.assertLessEqual(max(map(len, raw)), 100)

    def test_best_value_runs_by_score_per_dollar(self):
        with tempfile.TemporaryDirectory() as tmp:
            _grid(tmp)
            code, out = _run("--data", tmp, "list", "--source", "all")
        self.assertEqual(code, 0)
        rows = _section_parts(out, "best value")
        assert rows, "no rows parsed - parser or view broken"
        # 100/1 > 75/2: value order; M1 is culled (priciest and
        # weakest, cost claims first) and M2 fills the worst-score table
        self.assertEqual([r[-1] for r in rows], ["M3", "M4"])
        self.assertEqual([r[-3] for r in rows], ["100", "38"])

    def test_grades_read_the_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            _grid(tmp)
            code, out = _run("--data", tmp, "list", "--source", "all")
        self.assertEqual(code, 0)
        grades = {}
        for parts in _section_parts(out, "best value"):
            grades[parts[-1]] = parts[0]
        self.assertEqual(set(grades), {"M3", "M4"})
        # M3 derives 100, M4 75; M2 (50, "-") fills the worst-score table
        self.assertEqual(grades, {"M3": "++", "M4": "+"})
        self.assertIn("SCORE/$", _header(out))


class ListBenchmarkViewContract(unittest.TestCase):
    def test_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            _grid(tmp)
            code, out = _run(
                "--data",
                tmp,
                "list",
                "--source",
                "all",
                "--benchmark",
                "Terminal-Bench 2.1",
            )
        self.assertEqual(code, 0)
        tokens = _header(out).split()
        self.assertEqual(tokens, ["SCORE", "BENCH", "¢/1M", "SCORE/$", "PROV", "MODEL"])

    def test_rows_carry_the_benchmark_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            _grid(tmp)
            code, out = _run(
                "--data",
                tmp,
                "list",
                "--source",
                "all",
                "--benchmark",
                "Terminal-Bench 2.1",
            )
        self.assertEqual(code, 0)
        self.assertIn("TB", out)


class ScoresViewContract(unittest.TestCase):
    def test_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            _grid(tmp)
            code, out = _run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        head = next(l for l in out.splitlines() if "SCORE" in l and "TB 2.1" in l)
        tokens = head.strip().split("  ")[0].split()
        self.assertEqual(tokens, ["MODEL", "GRADE", "SCORE"])
        self.assertNotIn("AXES", head)
        self.assertIn("TB 2.1", head)
        for name, grade in (("M3", "++"), ("M2", "-"), ("M1", "--")):
            row = next(l for l in out.splitlines() if l.split()[:2] == [name, grade])
            self.assertIn(name, row)

    def test_rows_run_best_score_first_with_grades(self):
        with tempfile.TemporaryDirectory() as tmp:
            _grid(tmp)
            code, out = _run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        # the GRADE legend line matches the shape but names no model
        body = [
            l
            for l in out.splitlines()
            if len(l.split()) > 3
            and l.split()[1] in ("++", "+", "o", "--", "·", "-")
            and not l.strip().startswith("GRADE:")
        ]
        names = [l.split()[0] for l in body]
        grades = [l.split()[1] for l in body]
        self.assertEqual(names, ["M3", "M4", "M2", "M1"])
        self.assertEqual(grades, ["++", "+", "-", "--"])

    def test_model_column_fits_shown_names_only(self):
        # an unscored model is counted, never rowed: its long name must
        # not widen the MODEL column for the models that are
        import json

        long_name = "VERY-LONG-UNSCORED-MODEL-NAME-XXXXXX"
        with tempfile.TemporaryDirectory() as tmp:
            _write_models(
                Path(tmp),
                (
                    "a/m1",
                    1.0,
                    [("Terminal-Bench", "2.1", 90.0), ("SWE-Bench", "Verified", 90.0)],
                ),
            )
            (Path(tmp) / "opencode" / "a__long.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "a/long",
                            "name": long_name,
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
            code, out = _run("--data", tmp, "scores")
        self.assertEqual(code, 0)
        rows = [
            l
            for l in out.splitlines()
            if len(l.split()) >= 3
            and l.split()[1] in ("++", "+", "o", "--", "·", "-")
            and not l.strip().startswith("GRADE:")
        ]
        self.assertEqual([l.split()[0] for l in rows], ["M1"])
        self.assertLess(max(map(len, rows)), len(long_name))


class NextViewContract(unittest.TestCase):
    COLUMNS = ["#", "PLAN", "SCORE", "¢/1M", "SCORE/$", "ENDPOINT", "BAND"]

    def test_columns(self):
        import opencode_models.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _grid(tmp)
            orig = sources.load_source
            sources.load_source = lambda name: {"m1", "m2", "m3"}
            try:
                code, out = _run("--data", tmp, "next", "--source", "all")
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        head = next(l for l in out.splitlines() if "ENDPOINT" in l and "SCORE" in l)
        self.assertEqual(head.split(), self.COLUMNS)

    def test_picks_stay_within_a_quarter_of_the_best(self):
        import opencode_models.sources as sources

        with tempfile.TemporaryDirectory() as tmp:
            _grid(tmp)
            orig = sources.load_source
            sources.load_source = lambda name: {"m1", "m2", "m3"}
            try:
                code, out = _run(
                    "--data", tmp, "next", "--source", "all", "--count", "3"
                )
            finally:
                sources.load_source = orig
        self.assertEqual(code, 0)
        # floor is 75% of 100: M1 at 0 never appears
        self.assertNotIn("m1", out.split("BAND")[1].lower())
        self.assertIn("opencode-go/m3", out)


class OverridesViewContract(unittest.TestCase):
    COLUMNS = ["MODEL", "BENCHMARK", "CATALOG", "REPORTED", "USED", "NOTE"]

    def test_columns_and_the_used_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_models(d, ("a/m1", 1.0, [("Terminal-Bench", "4.0", 50.0)]))
            ov = d / "overrides"
            ov.mkdir(parents=True, exist_ok=True)
            (ov / "a__m1.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "a/m1",
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "4.0",
                                    "score": 60.0,
                                    "metric": "acc",
                                    "source": "https://example.invalid/run",
                                }
                            ],
                        }
                    }
                )
            )
            code, out = _run("--data", str(d), "overrides")
        self.assertEqual(code, 0)
        head = next(l for l in out.splitlines() if "USED" in l)
        self.assertEqual(head.split(), self.COLUMNS)
        row = next(l for l in out.splitlines() if "a/m1" in l)
        # same tier counts as at least as good: the pin applies, and USED
        # names the value the console keeps
        self.assertIn("50", row)
        self.assertIn("60", row)
        self.assertRegex(row, r"\b60\s+\S.*$")


if __name__ == "__main__":
    unittest.main()


class NextTableContract(unittest.TestCase):
    COLUMNS = ["#", "PLAN", "SCORE", "¢/1M", "SCORE/$", "ENDPOINT", "BAND"]

    def _row(self, name, provider, score, price, free=False):
        from opencode_models.data import Model
        from opencode_models.ranking import Row

        model = Model(
            model_id=f"l/{name}",
            name=name,
            lab="l",
            price_input=price,
            price_output=price,
            price_cached_input=price,
            scores={},
        )
        return Row(
            model=model,
            name=name,
            price=price,
            free=free,
            band="free" if free else "included",
            score=score,
            benchmark="Terminal-Bench 2.1",
            provider=provider,
            grade="o",
            entry=None,
        )

    def _table(self, picks):
        from opencode_models.cli import _next_table

        return _next_table(picks)

    def test_rows_fit_a_narrow_terminal(self):
        # 60-char endpoints: every line fits 120 because each column is
        # measured, never truncated and never blown out by padding
        picks = [
            self._row("a" * 60, "go,zen", 95.0, 0.0013, free=True),
            self._row("b" * 60, "zen", 81.0, 15.0),
        ]
        lines = self._table(picks).splitlines()
        self.assertLessEqual(max(map(len, lines)), 120)

    def test_columns_line_up_behind_a_wide_plan(self):
        # "go,zen" is six chars in a five-wide PLAN world: later columns
        # must not shift beside it
        picks = [
            self._row("aaa", "go,zen", 95.0545, 0.0013, free=True),
            self._row("bb", "zen", 81.5, 15.0),
        ]
        lines = self._table(picks).splitlines()
        head, first, second = lines[0], lines[1], lines[2]
        self.assertEqual(head.split(), self.COLUMNS)
        self.assertEqual(first.index("go,zen"), head.index("PLAN"))
        self.assertEqual(second.index("zen"), head.index("PLAN"))
        self.assertEqual(first.index("opencode-go/aaa"), head.index("ENDPOINT"))
        self.assertEqual(second.index("opencode/bb"), head.index("ENDPOINT"))
        self.assertEqual(first.index("free"), head.index("BAND"))
        self.assertEqual(second.index("included"), head.index("BAND"))

    def test_right_aligned_values_share_a_right_edge(self):
        picks = [
            self._row("aaa", "go,zen", 95.0545, 0.0013, free=True),
            self._row("bb", "zen", 81.5, 15.0),
        ]
        lines = self._table(picks).splitlines()
        head, first, second = lines[0], lines[1], lines[2]
        for header, value, other in (
            ("SCORE/$", "73119", "5"),
            ("¢/1M", "0", "1500"),
            ("SCORE", "95", "82"),
        ):
            edge = head.index(header) + len(header)
            first_end = first.rindex(value) + len(value)
            second_end = second.rindex(other) + len(other)
            self.assertEqual(first_end, second_end)
            self.assertEqual(first_end, edge)
