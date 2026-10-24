"""compose_catalog: the single curation entry point.

Alias twins fold before curation, then curated overlays, arena scores,
and official boards apply in that order — on synthetic inputs, with no
data_dir and no I/O. The order itself is the contract under test.
"""

import unittest

from opencode_models.boards import BoardRow
from opencode_models.catalog import compose_catalog
from opencode_models.models import Model


def _model(mid, name=None, scores=None, sources=None):
    lab = mid.split("/")[0]
    return Model(
        model_id=mid,
        name=name or mid.split("/")[-1],
        lab=lab,
        price_input=1.0,
        price_output=1.0,
        price_cached_input=1.0,
        scores=dict(scores or {}),
        sources=dict(sources or {}),
    )


def _override(mid, score, version="2.1"):
    return {
        mid: {
            "id": mid,
            "benchmarks": [
                {
                    "name": "Terminal-Bench",
                    "version": version,
                    "score": score,
                    "metric": "acc",
                    "source": "https://example.com",
                }
            ],
        }
    }


def _board(name, org, benchmark="Terminal-Bench 4.0", score=70.0):
    return BoardRow(
        benchmark=benchmark,
        model_name=name,
        score=score,
        source="https://www.tbench.ai",
        model_org=org,
    )


class ComposeTest(unittest.TestCase):
    def test_full_composition_applies_in_order(self):
        upstream = [
            _model(
                "meta/muse-spark-1-3",
                name="Muse Spark 1.3",
                scores={"Terminal-Bench 2.1": 50.0},
                sources={"Terminal-Bench 2.1": ""},
            )
        ]
        comp = compose_catalog(
            upstream,
            [],
            _override("meta/muse-spark-1-3", 60.0),
            {"muse-spark-1-3": 1500.0},
            [_board("Muse Spark 1.3", "Meta")],
        )
        self.assertEqual([m.model_id for m in comp.models], ["meta/muse-spark-1-3"])
        merged = comp.models[0]
        self.assertEqual(merged.scores["Terminal-Bench 2.1"], 60.0)
        self.assertEqual(merged.scores["LMArena Elo"], 1500.0)
        self.assertEqual(merged.scores["Terminal-Bench 4.0"], 70.0)
        self.assertEqual(comp.board_conflicts, [])
        self.assertEqual(list(comp.merge_warnings), [])

    def test_twins_fold_before_the_override_applies(self):
        upstream = [
            _model(
                "meta/muse-spark-1-3",
                name="Muse Spark 1.3",
                scores={"Terminal-Bench 2.1": 50.0},
                sources={"Terminal-Bench 2.1": ""},
            ),
            _model(
                "meta/muse-spark-1-3-contributor", name="Muse Spark 1.3 Contributor"
            ),
        ]
        comp = compose_catalog(
            upstream, [], _override("meta/muse-spark-1-3", 60.0), {}, []
        )
        self.assertEqual([m.model_id for m in comp.models], ["meta/muse-spark-1-3"])
        self.assertEqual(comp.models[0].scores["Terminal-Bench 2.1"], 60.0)
        self.assertIn("meta/muse-spark-1-3", comp.curated.curated)

    def test_designation_keyed_override_is_an_orphan(self):
        upstream = [
            _model(
                "meta/muse-spark-1-3",
                name="Muse Spark 1.3",
                scores={"Terminal-Bench 2.1": 50.0},
                sources={"Terminal-Bench 2.1": ""},
            ),
            _model(
                "meta/muse-spark-1-3-contributor", name="Muse Spark 1.3 Contributor"
            ),
        ]
        comp = compose_catalog(
            upstream, [], _override("meta/muse-spark-1-3-contributor", 99.0), {}, []
        )
        self.assertIn("meta/muse-spark-1-3-contributor", comp.curated.orphans)
        self.assertEqual(comp.models[0].scores["Terminal-Bench 2.1"], 50.0)

    def test_merge_conflicts_surface_as_warnings(self):
        upstream = [
            _model("meta/muse-spark-1-3", scores={"Terminal-Bench 2.1": 50.0}),
            _model(
                "meta/muse-spark-1-3-contributor",
                scores={"Terminal-Bench 2.1": 70.0},
            ),
        ]
        comp = compose_catalog(upstream, [], {}, {}, [])
        self.assertEqual(comp.models[0].scores["Terminal-Bench 2.1"], 50.0)
        self.assertTrue(any("Terminal-Bench 2.1" in w for w in comp.merge_warnings))


if __name__ == "__main__":
    unittest.main()
