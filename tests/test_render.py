"""Pure conflict-table rendering (`commands.render`).

`format_conflicts` is the printable core: rows in, text out, no I/O.
Byte-exact end-to-end output stays pinned by the `overrides` CLI
golden tests; these pin the pure function directly.
"""

import unittest

from opencode_models.commands.render import cells, format_conflicts
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
