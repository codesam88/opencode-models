"""Aligned text tables from declarative input.

Every console view was hand-rolling the same loop: measure the widest
cell per column (header included), pad, join, print — with the widths
and the alignment re-decided at each site, so a wide cell silently
broke the grid instead of widening it. These helpers take headers plus
rows of plain strings and return aligned text; the views only declare
*what* goes in each column, never how wide it is.
"""

import unittest


class TableTest(unittest.TestCase):
    def test_cells_pad_to_the_widest_in_their_column(self):
        from opencode_rank.tables import render
        out = render(["A", "BB"], [["x", "yy"], ["zzz", "w"]], aligns="<")
        self.assertEqual(out.splitlines(),
                         ["A    BB", "x    yy", "zzz  w"])

    def test_right_is_the_default_alignment(self):
        from opencode_rank.tables import render
        out = render(["N"], [["1"], ["22"]])
        self.assertEqual(out.splitlines(), [" N", " 1", "22"])

    def test_alignment_is_per_column(self):
        from opencode_rank.tables import render
        out = render(["L", "R"], [["ab", "1"]], aligns=("<", ">"))
        self.assertEqual(out.splitlines(), ["L   R", "ab  1"])

    def test_a_single_alignment_covers_every_column(self):
        from opencode_rank.tables import render
        out = render(["A", "B"], [["x", "y"]], aligns="<")
        self.assertEqual(out.splitlines(), ["A  B", "x  y"])

    def test_header_alignment_defaults_to_the_cells(self):
        from opencode_rank.tables import render
        out = render(["BB"], [["a"]], aligns="<")
        self.assertEqual(out.splitlines()[0], "BB")

    def test_header_alignment_can_differ(self):
        from opencode_rank.tables import render
        out = render(["BB"], [["a"]], aligns=">", header_aligns="<")
        self.assertEqual(out.splitlines()[0], "BB")

    def test_min_widths_hold_narrow_columns_open(self):
        from opencode_rank.tables import render
        out = render(["#", "P"], [["1", "x"], ["22", "yy"]],
                     aligns="<", min_widths=(2, 5))
        self.assertEqual(out.splitlines(), ["#   P", "1   x", "22  yy"])

    def test_every_line_is_indented(self):
        from opencode_rank.tables import render
        out = render(["A"], [["x"]], indent="  ")
        self.assertEqual(out.splitlines(), ["  A", "  x"])

    def test_trailing_padding_is_stripped(self):
        from opencode_rank.tables import render
        out = render(["A", "B"], [["x", ""]])
        self.assertEqual(out.splitlines(), ["A  B", "x"])

    def test_short_rows_pad_out_with_blanks(self):
        from opencode_rank.tables import render
        out = render(["A", "B", "C"], [["x"]])
        self.assertEqual(out.splitlines(), ["A  B  C", "x"])

    def test_an_empty_table_is_just_the_header(self):
        from opencode_rank.tables import render
        self.assertEqual(render(["A", "B"], []), "A  B")


if __name__ == "__main__":
    unittest.main()
