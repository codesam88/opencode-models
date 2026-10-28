"""Pure conflict-table rendering (`commands.render`).

`format_conflicts` is the printable core: rows in, text out, no I/O.
Byte-exact end-to-end output stays pinned by the `overrides` CLI
golden tests; these pin the pure function directly.
"""

import unittest

from opencode_models.commands.render import (
    cells,
    format_conflicts,
    headings,
    next_headings,
    pick_value,
    value_unit,
)
from opencode_models.models import Model
from opencode_models.overrides import Conflict
from opencode_models.ranking import BAND_PAYG, Row


def _conflict(**kw):
    base = dict(
        model_id="a/m1",
        benchmark="Terminal-Bench 4.0",
        upstream=55.8,
        override=57.88,
        upstream_source="https://example.invalid/blog/introducing",
        override_source="https://www.tbench.ai/leaderboard/terminal-bench/4.0",
        reason="official board",
        applied=True,
    )
    base.update(kw)
    return Conflict(**base)


class CostCellFormatTest(unittest.TestCase):
    """The ¢/req column keeps its fraction; ¢/1m rates stay whole cents."""

    def _row(self, price: float, free: bool = False) -> Row:
        model = Model(
            model_id="l/m1",
            name="M1",
            lab="l",
            price_input=1.0,
            price_output=1.0,
            price_cached_input=1.0,
        )
        return Row(
            model=model,
            name="M1",
            price=price,
            free=free,
            band=BAND_PAYG,
            score=50.0,
            benchmark=None,
        )

    def test_a_fractional_cost_keeps_its_decimals(self):
        # 0.0265c and 6.12c: rounding to whole cents was hiding both
        self.assertEqual(cells(self._row(0.000265))[1], "0.03")
        self.assertEqual(cells(self._row(0.0612))[1], "6.12")
        self.assertEqual(cells(self._row(0.0044))[1], "0.44")

    def test_a_whole_cost_stays_whole(self):
        self.assertEqual(cells(self._row(0.66))[1], "66")
        self.assertEqual(cells(self._row(1.8))[1], "180")

    def test_a_free_line_reads_zero(self):
        self.assertEqual(cells(self._row(0.0, free=True))[1], "0")

    def test_the_per_million_rate_is_still_whole_cents(self):
        # ¢/1m under --cost input|output: 6.12c is 6c there, as before
        self.assertEqual(cells(self._row(0.0612), True, "input")[1], "6")
        self.assertEqual(cells(self._row(0.0612), True, "output")[1], "6")


class ValueUnitTest(unittest.TestCase):
    """The value column names its unit from the values in its own table."""

    def test_every_value_above_100_reads_score_per_cent(self):
        self.assertEqual(value_unit(["8301", "1121"]), "Score/¢")

    def test_a_value_at_or_below_100_keeps_score_per_dollar(self):
        # 100/100 is not above 100, and 38 would round away entirely
        self.assertEqual(value_unit(["100", "38"]), "Score/$")
        self.assertEqual(value_unit(["0"]), "Score/$")

    def test_a_dash_never_holds_the_column_at_dollars(self):
        self.assertEqual(value_unit(["-", "8301"]), "Score/¢")
        self.assertEqual(value_unit(["-", "-"]), "Score/¢")

    def test_a_priced_zero_line_counts_as_above_100(self):
        # its ratio is unbounded, so it cannot be the value that keeps
        # the column at dollars
        self.assertEqual(value_unit(["free", "5000"]), "Score/¢")

    def test_a_table_without_rows_keeps_score_per_dollar(self):
        self.assertEqual(value_unit([]), "Score/$")


class ValueCellUnitTest(unittest.TestCase):
    """A value is expressed in the unit its header claims, never the other."""

    def _row(self, price: float, score: float | None = 50.0, free=False) -> Row:
        model = Model(
            model_id="l/m1",
            name="M1",
            lab="l",
            price_input=1.0,
            price_output=1.0,
            price_cached_input=1.0,
        )
        return Row(
            model=model,
            name="M1",
            price=price,
            free=free,
            band=BAND_PAYG,
            score=score,
            benchmark=None,
        )

    def test_score_per_cent_is_the_per_dollar_ratio_divided_by_100(self):
        row = self._row(0.004)
        self.assertEqual(pick_value(row), "12500")
        self.assertEqual(pick_value(row, "Score/¢"), "125")

    def test_score_per_dollar_stays_the_default(self):
        row = self._row(0.004)
        self.assertEqual(cells(row)[2], "12500")
        self.assertEqual(cells(row, True, "est", "Score/¢")[2], "125")

    def test_unscored_and_free_lines_read_the_dash_in_either_unit(self):
        self.assertEqual(pick_value(self._row(0.004, None), "Score/¢"), "-")
        self.assertEqual(pick_value(self._row(0.0, 50.0, free=True), "Score/¢"), "-")

    def test_a_priced_zero_line_reads_free_in_either_unit(self):
        self.assertEqual(pick_value(self._row(0.0)), "free")
        self.assertEqual(pick_value(self._row(0.0), "Score/¢"), "free")

    def test_the_header_names_the_unit_it_is_given(self):
        self.assertEqual(headings(True, "est", "Score/$")[2], "Score/$")
        self.assertEqual(headings(True, "est", "Score/¢")[2], "Score/¢")
        self.assertEqual(headings(False, "est", "Score/¢")[3], "Score/¢")
        self.assertEqual(next_headings("est", "Score/$")[4], "Score/$")
        self.assertEqual(next_headings("est", "Score/¢")[4], "Score/¢")


class FormatConflictsTest(unittest.TestCase):
    def test_empty_renders_empty(self):
        self.assertEqual(format_conflicts([]), "")

    def test_row_carries_both_values_with_tiers_and_the_used_one(self):
        text = format_conflicts([_conflict()])
        self.assertIn("Model", text)
        self.assertIn("a/m1", text)
        self.assertIn("55.8 self", text)
        self.assertIn("57.88 board", text)
        self.assertIn("57.88", text.splitlines()[-1])

    def test_orphans_render_as_unmatched_rows(self):
        text = format_conflicts([], orphans=["x/gone"])
        self.assertIn("x/gone", text)
        self.assertIn("override file has no upstream record", text)

    def test_manual_orphans_render_as_shadowed_rows(self):
        text = format_conflicts([], manual_orphans=["x/manual"])
        self.assertIn("x/manual", text)
        self.assertIn("manual record shadowed by upstream record", text)


if __name__ == "__main__":
    unittest.main()
