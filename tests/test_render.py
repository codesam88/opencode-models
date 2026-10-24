"""Pure conflict-table rendering (`commands.render`).

`format_conflicts` is the printable core: rows in, text out, no I/O.
Byte-exact end-to-end output stays pinned by the `overrides` CLI
golden tests; these pin the pure function directly.
"""

import unittest

from opencode_models.commands.render import format_conflicts
from opencode_models.overrides import Conflict


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
