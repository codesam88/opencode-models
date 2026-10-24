"""`scores` decision/report split (`commands.scores`).

Column selection and cell rendering are pure functions of loaded
models: a column a model was not measured on renders blank, never
zero. End-to-end output stays pinned by the `scores` CLI golden
tests; these pin the pure split directly.
"""

import unittest

from opencode_models.commands.scores import _cells, _columns
from opencode_models.models import Model
from opencode_models.scoring import scales


def _model(mid, scores):
    lab = mid.split("/")[0]
    return Model(
        model_id=mid,
        name=mid.split("/")[-1].upper(),
        lab=lab,
        price_input=1.0,
        price_output=1.0,
        price_cached_input=1.0,
        scores=dict(scores),
        sources={k: "" for k in scores},
    )


class ScoreSplitTest(unittest.TestCase):
    def _table(self):
        m1 = _model("a/m1", {"Terminal-Bench 2.1": 90.0, "SWE-Bench Pro": 80.0})
        m2 = _model("a/m2", {"Terminal-Bench 2.1": 50.0, "SWE-Bench Pro": 40.0})
        m3 = _model("a/m3", {"Terminal-Bench 2.1": 10.0})
        return m1, m2, m3, scales([m1, m2, m3])

    def test_columns_cover_only_benchmarks_with_numbers(self):
        m1, m2, m3, table = self._table()
        keys = [k for k, _label, _name in _columns([m1, m2, m3], table)]
        self.assertIn("Terminal Bench 2.1", keys)
        self.assertIn("SWE Bench Pro", keys)

    def test_unmeasured_cell_is_blank_not_zero(self):
        m1, m2, m3, table = self._table()
        columns = _columns([m1, m2, m3], table)
        swe = columns[[k for k, _l, _n in columns].index("SWE Bench Pro")]
        self.assertEqual(
            _cells(m3, table, [swe]),
            [""],
            "an unmeasured column must not read as worst in the catalog",
        )
        self.assertTrue(_cells(m1, table, [swe])[0].startswith("80"))


if __name__ == "__main__":
    unittest.main()
