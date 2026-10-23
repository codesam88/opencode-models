"""Output presentation as properties, not literals.

Grid headers are title-cased, headers and rows carry equal cell counts
(where the grid dialect allows it to be read back), and no cell is ever
truncated: these hold for every view and every dataset, so they are
asserted structurally — by shape, never by snapshotting one rendering.

Ruled (──) blocks give the structure away: the header is the grid line
right after the rule, data follows until the next blank line. Band labels
("free — use first · 1") never enter the grid: they carry no interior
wide gap. The grade gutter that prefixes `list` rows sits outside the
column grid by design (it has no header), so each block is measured past
a prefix the header line itself dictates.
"""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import opencode_models.availability as availability
from opencode_models.cli import main


def _is_title_case(text):
    """Capital first letter, never ALL-CAPS; symbols have no case to keep."""
    text = text.strip()
    if not text:
        return True
    first = text[0]
    cased = [c for c in text if c.isalpha()]
    return (not first.isalpha() or first.isupper()) and (
        not cased or text != text.upper()
    )


def _ruled_blocks(out):
    """[(title, grid lines)] for every ── ruled block in the output."""
    lines = out.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("── "):
            grid = []
            j = i + 1
            while (
                j < len(lines) and lines[j].strip() and not lines[j].startswith("── ")
            ):
                if re.search(r"\S\s{2,}\S", lines[j]):
                    grid.append(lines[j])
                j += 1
            blocks.append((lines[i], grid))
            i = j
        else:
            i += 1
    return blocks


def _cells(line):
    return re.split(r"\s{2,}", line.strip())


class PresentationTest(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._orig_home = os.environ.get("XDG_CONFIG_HOME")
        self._orig_key = os.environ.pop("OPENCODE_API_KEY", None)
        os.environ["XDG_CONFIG_HOME"] = self._home
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._orig_key is not None:
            os.environ["OPENCODE_API_KEY"] = self._orig_key
        if self._orig_home is None:
            del os.environ["XDG_CONFIG_HOME"]
        else:
            os.environ["XDG_CONFIG_HOME"] = self._orig_home

    def _fixture(self, d):
        (d / "opencode").mkdir(parents=True, exist_ok=True)
        specs = [
            ("a/paid-hi", 1.0, 90.0, False, 60.0),
            ("b/paid-mid", 3.0, 70.0, False, 60.0),
            ("c/paid-lo", 9.0, 40.0, False, 60.0),
            ("d/freebie", 0.0, 95.0, True, None),
        ]
        for mid, price, score, _free, _limit in specs:
            doc = {
                "model": {
                    "id": mid,
                    "name": mid.split("/")[1].upper(),
                    "lab": mid.split("/")[0],
                    "priceUsdPer1M": {
                        "input": price,
                        "output": price,
                        "cachedInput": price,
                    },
                    "benchmarks": [
                        {"name": n, "score": score, "metric": "acc", "source": ""}
                        for n in ("Terminal-Bench 2.1", "SWE-Bench Verified")
                    ],
                }
            }
            (d / "opencode" / (mid.replace("/", "__") + ".json")).write_text(
                json.dumps(doc)
            )
        (d / "tier_prices").mkdir(parents=True, exist_ok=True)
        (d / "tier_prices" / "go_prices.json").write_text(
            json.dumps(
                [
                    {
                        "tier": "go",
                        "model_id": mid.split("/")[1],
                        "name": mid,
                        "input": price,
                        "output": price,
                        "cached_read": price,
                        "limit": limit,
                        "free": free,
                    }
                    for mid, price, _s, free, limit in specs
                ]
            )
        )
        return {mid.split("/")[1] for mid, *_ in specs}

    def _run_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            slugs = self._fixture(d)
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: set(slugs)
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    code = main(["--data", tmp, "list", "--source", "go"])
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        return buf.getvalue()

    def _run_usage(self):
        from opencode_models import cli as C
        from tests import test_usage as TU

        with tempfile.TemporaryDirectory() as tmp:
            TU._write_plan(tmp, datetime.now(timezone.utc) - timedelta(days=2))
            get = TU._routes(
                **{
                    C.GO_USAGE_URL: json.dumps(TU.USAGE_PAYLOAD),
                    C.USAGE_EXPORT_URL: TU.EXPORT_CSV,
                    C.GO_BILLING_URL: TU.BILLING_PAYLOAD,
                }
            )
            buf = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}),
                mock.patch.object(C, "_api_get", lambda k: get),
                contextlib.redirect_stdout(buf),
            ):
                code = main(["--data", tmp, "usage"])
        self.assertEqual(code, 0)
        return buf.getvalue()

    def test_title_case_headers(self):
        for out in (self._run_list(), self._run_usage()):
            for _title, grid in _ruled_blocks(out):
                self.assertTrue(grid)
                for token in grid[0].split():
                    self.assertTrue(_is_title_case(token), token)
        # unruled grids all carry single-token headers; multi-word scores
        # labels ("Swe vrfd") are covered per-label by HeaderCaseTest,
        # since running text cannot rejoin them into labels
        for out in (self._run_next(), self._run_models(), self._run_conflicts()):
            header = self._grid_header(out)
            for token in header.split():
                self.assertTrue(_is_title_case(token), token)

    def test_each_table_section_opens_with_its_header(self):
        # every ── rule is a separate table: the column header row sits
        # right below the divider, and every section in one output shares
        # the identical header line
        for out in (self._run_list(), self._run_usage()):
            blocks = _ruled_blocks(out)
            self.assertTrue(blocks)
            headers = [grid[0] for _title, grid in blocks]
            self.assertEqual(len(set(headers)), 1)
            lines = out.splitlines()
            for title, _grid in blocks:
                rule_at = lines.index(title)
                self.assertEqual(lines[rule_at + 1], headers[0])

    @staticmethod
    def _grid_header(out):
        """The first grid header line of an unruled view's output."""
        for line in out.splitlines():
            low = line.lower()
            if "endpoint" in low or "used" in low:
                return line
        raise AssertionError(f"no grid header in:\n{out}")

    def _run_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            slugs = self._fixture(d)
            orig = availability.load_roster
            availability.load_roster = lambda _d, name: set(slugs)
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    code = main(
                        ["--data", tmp, "next", "--source", "go", "--count", "2"]
                    )
            finally:
                availability.load_roster = orig
        self.assertEqual(code, 0)
        return buf.getvalue()

    def _write_available(self, d, slugs):
        (d / "available").mkdir(parents=True, exist_ok=True)
        (d / "available" / "go.json").write_text(
            json.dumps(
                {
                    "meta": {
                        "source": "test",
                        "fetched_at": "2026-10-22T00:00:00+00:00",
                    },
                    "models": {slug: True for slug in sorted(slugs)},
                }
            )
        )

    def _run_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            slugs = self._fixture(d)
            self._write_available(d, slugs)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main(["--data", tmp, "models"])
        self.assertEqual(code, 0)
        return buf.getvalue()

    def _run_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._fixture(d)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main(["--data", tmp, "scores", "--all-models"])
        self.assertEqual(code, 0)
        return buf.getvalue()

    def _run_conflicts(self):
        from opencode_models.cli import _print_conflicts
        from opencode_models.overrides import Conflict

        def conflict(mid, bench, upstream, override, used):
            return Conflict(
                model_id=mid,
                benchmark=bench,
                upstream=upstream,
                override=override,
                upstream_source=None,
                override_source="https://example.invalid/board",
                reason="board run",
                applied=used == override,
            )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _print_conflicts(
                [
                    conflict("l/aaa", "TB", 50.0, 60.0, 60.0),
                    conflict("l/bb", "TB", 70.0, 65.0, 70.0),
                ]
            )
        return buf.getvalue()

    def test_header_and_rows_carry_equal_cell_counts(self):
        # only double-spaced grids qualify: a single-spaced join (the usage
        # left block, the scores name column) is not segmentable from text
        # alone, so exact-fit cells would merge or split unpredictably.
        # What is asserted here is that every list row carries exactly the
        # header's cells — no dropped or extra columns.
        out = self._run_list()
        for _title, grid in _ruled_blocks(out):
            prefix = len(grid[0]) - len(grid[0].lstrip())
            counts = {len(_cells(line[prefix:])) for line in grid}
            self.assertEqual(counts, {len(_cells(grid[0][prefix:]))}, grid[0])

    def test_no_cell_is_truncated(self):
        for out in (
            self._run_list(),
            self._run_usage(),
            self._run_next(),
            self._run_models(),
            self._run_scores(),
            self._run_conflicts(),
        ):
            self.assertNotIn("…", out)


class BuilderDeterminismTest(unittest.TestCase):
    """Grid builders return plain text whatever the terminal says.

    Styling is a print-time concern: `_style` sniffs the ambient stdout,
    so any ANSI baked into a *returned* string leaks the caller's tty into
    the value. Builders must return identical ANSI-free text under a tty
    and a pipe; only the `print` call sites may style.
    """

    def _picks(self):
        from opencode_models.models import Model
        from opencode_models.ranking import Row

        def row(name, provider, score, price, free=False):
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

        return [
            row("aaa", "go,zen", 95.0, 0.0, free=True),
            row("bb", "zen", 81.5, 15.0),
        ]

    def test_next_table_ignores_the_terminal(self):
        import sys
        from unittest import mock

        from opencode_models.cli import _next_table

        with mock.patch.object(sys.stdout, "isatty", return_value=False):
            piped = _next_table(self._picks())
        with mock.patch.object(sys.stdout, "isatty", return_value=True):
            terminal = _next_table(self._picks())
        self.assertEqual(piped, terminal)
        self.assertNotIn("\x1b", piped)

    def test_header_ignores_the_terminal(self):
        import sys
        from unittest import mock

        from opencode_models.cli import _header

        with mock.patch.object(sys.stdout, "isatty", return_value=False):
            piped = _header((5, 6, 7, 4), True, "fix")
        with mock.patch.object(sys.stdout, "isatty", return_value=True):
            terminal = _header((5, 6, 7, 4), True, "fix")
        self.assertEqual(piped, terminal)
        self.assertNotIn("\x1b", piped)


class HeaderCaseTest(unittest.TestCase):
    """Every grid header label, from every source, is title-cased.

    Property, not a snapshot: new columns inherit the rule by construction
    instead of freezing today's literal header rows.
    """

    def test_every_header_label_is_title_case(self):
        from opencode_models import cli as C
        from opencode_models import scoring as S

        labels = [
            *C._headings(True, "fix"),
            *C._headings(True, "est"),
            *C._headings(False, "input"),
            *C.USAGE_LEFT_HEADERS,
            *C.USAGE_RIGHT_HEADERS,
            *C._next_headings("fix"),
            *C._next_headings("est"),
            *C.MODELS_HEADERS,
            *C.MODELS_ALL_HEADERS,
            *C.CONFLICT_HEADERS,
            *C.SCORES_STATIC_HEADERS,
            *(C._column_label(S._folded(n)) for n in S._TRACKED),
        ]
        self.assertTrue(labels)
        for label in labels:
            self.assertTrue(_is_title_case(label), label)


if __name__ == "__main__":
    unittest.main()
