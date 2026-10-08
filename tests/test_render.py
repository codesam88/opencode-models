"""Rendering the static HTML report: SVG scatter + frontier table."""

import unittest

from modelpareto.data import Model
from modelpareto.render import render_html


def _m(model_id, price, score, lab="x"):
    return Model(
        model_id=model_id,
        name=model_id.upper(),
        lab=lab,
        price_input=price,
        price_output=None,
        price_cached_input=None,
        scores={} if score is None else {"TB": score},
    )


class RenderHtmlTest(unittest.TestCase):
    BENCH = "TB"

    def _html(self, models, benchmark=BENCH):
        return render_html(models, benchmark)

    def test_names_the_benchmark_in_the_heading(self):
        html = self._html([_m("a", 1.0, 50.0)], benchmark="SWE-Bench Verified")
        self.assertIn("SWE-Bench Verified", html)

    def test_is_a_full_document(self):
        html = self._html([])
        self.assertTrue(html.lstrip().startswith("<!DOCTYPE html>"))
        self.assertIn("</html>", html)

    def test_empty_dataset_renders_explanation_not_crash(self):
        html = self._html([])
        self.assertIn("no models", html.lower())

    def test_frontier_models_appear_in_the_table(self):
        html = self._html([_m("a", 1.0, 50.0), _m("b", 2.0, 90.0)])
        self.assertIn("A", html)
        self.assertIn("B", html)

    def test_dominated_model_is_not_in_the_table(self):
        # b dominates c; c must not be listed as frontier
        html = self._html(
            [_m("a", 1.0, 50.0), _m("b", 2.0, 90.0), _m("c", 9.0, 40.0)]
        )
        self.assertNotIn(">C<", html)

    def test_model_without_score_does_not_crash_render(self):
        html = self._html([_m("a", 1.0, 50.0), _m("ns", 1.0, None)])
        self.assertIn("A", html)

    def test_svg_scatter_is_inline_without_external_assets(self):
        html = self._html([_m("a", 1.0, 50.0)])
        self.assertIn("<svg", html)
        self.assertNotIn("<script src=", html)
        self.assertNotIn('<link rel="stylesheet" href="http', html)

    def test_every_frontier_point_gets_a_svg_circle(self):
        html = self._html([_m("a", 1.0, 50.0), _m("b", 2.0, 90.0)])
        self.assertGreaterEqual(html.count("<circle"), 2)

    def test_scores_and_prices_shown_in_table_cells(self):
        html = self._html([_m("a", 1.5, 42.0)])
        self.assertIn("1.5", html)
        self.assertIn("42", html)


if __name__ == "__main__":
    unittest.main()
