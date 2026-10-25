"""Model markers: one registry for display symbols decided from source data."""

import unittest

from opencode_models.markers import MARKERS, legend, median_note, suffix


class RegistryTest(unittest.TestCase):
    def test_symbols_are_single_distinct_characters(self):
        symbols = [m.symbol for m in MARKERS.values()]
        self.assertEqual(len(symbols), len(set(symbols)))
        for symbol in symbols:
            self.assertEqual(len(symbol), 1)

    def test_every_marker_explains_itself(self):
        for key, marker in MARKERS.items():
            self.assertTrue(marker.blurb, key)

    def test_curated_keeps_the_scores_view_asterisk(self):
        self.assertEqual(MARKERS["curated"].symbol, "*")

    def test_median_marks_approximation(self):
        self.assertEqual(MARKERS["median"].symbol, "~")


class SuffixTest(unittest.TestCase):
    def test_no_markers_no_suffix(self):
        self.assertEqual(suffix(()), "")
        self.assertEqual(suffix([]), "")

    def test_marks_append_in_registry_order(self):
        self.assertEqual(suffix(("median", "curated")), " *~")

    def test_unknown_keys_are_ignored_not_crashed_on(self):
        self.assertEqual(suffix(("nope",)), "")


class LegendTest(unittest.TestCase):
    def test_no_markers_no_legend(self):
        self.assertEqual(legend(()), "")

    def test_only_present_markers_are_explained(self):
        line = legend(("median",))
        self.assertTrue(line.startswith("Markers: "))
        self.assertIn("~", line)
        self.assertNotIn("*", line)


class MedianNoteTest(unittest.TestCase):
    def test_empty_needs_no_note(self):
        self.assertEqual(median_note([]), [])

    def test_single_model_fits_one_terminal_line(self):
        (line,) = median_note(["m2"])
        self.assertTrue(line.startswith("Note: "))
        self.assertIn("m2", line)
        self.assertIn("no published assumptions", line)
        self.assertLessEqual(len(line), 80)

    def test_long_lists_wrap_with_hanging_indent(self):
        slugs = [f"model-{i:02d}-with-a-name" for i in range(10)]
        lines = median_note(slugs)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(len(line), 80)
        for slug in slugs:
            self.assertIn(slug, "\n".join(lines))
        self.assertTrue(all(line.startswith("  ") for line in lines[1:]))


if __name__ == "__main__":
    unittest.main()
