"""Manual catalog: hand-written records for offered models upstream lacks."""

import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from opencode_models.boards import BoardRow
from opencode_models.catalog import compose_catalog
from opencode_models.models import load_manual
from opencode_models.overrides import load_curated, load_overrides
from opencode_models.scoring import scales, score_of

#: Hermetic fixtures: never touch the real key file, or `list` reaches
#: for the live gateway and the test goes nondeterministic (401 vs served).
os.environ.pop("OPENCODE_API_KEY", None)
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="opencode-models-test-home-")


def _doc(mid, name="M", price=1.0, benchmarks=()):
    return {
        "model": {
            "id": mid,
            "name": name,
            "lab": mid.split("/")[0],
            "priceUsdPer1M": {
                "input": price,
                "output": price,
                "cachedInput": None,
            },
            "benchmarks": list(benchmarks),
        }
    }


def _write_upstream(d: Path, mid, benchmarks=()):
    out = d / "opencode"
    out.mkdir(parents=True, exist_ok=True)
    doc = _doc(mid, mid.split("/")[1].upper(), benchmarks=benchmarks)
    (out / f"{mid.replace('/', '__')}.json").write_text(json.dumps(doc))


def _write_manual(d: Path, mid, name="M", price=1.0, drop=()):
    out = d / "manual"
    out.mkdir(parents=True, exist_ok=True)
    doc = _doc(mid, name, price)
    for key in drop:
        if key == "benchmarks":
            doc["model"].pop(key, None)
        elif key in ("input", "output"):
            doc["model"]["priceUsdPer1M"].pop(key, None)
        else:
            doc["model"].pop(key, None)
    (out / f"{mid.replace('/', '__')}.json").write_text(json.dumps(doc))


class LoadManualTest(unittest.TestCase):
    def test_valid_record_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_manual(d, "l/m1")
            models = load_manual(d / "manual")
        self.assertEqual([m.model_id for m in models], ["l/m1"])

    def test_record_missing_id_name_lab_or_prices_is_refused(self):
        for key in ("id", "name", "lab", "input", "output"):
            with self.subTest(drop=key):
                with tempfile.TemporaryDirectory() as tmp:
                    d = Path(tmp)
                    _write_manual(d, "l/m1", drop=[key])
                    models = load_manual(d / "manual")
                self.assertEqual(models, [])

    def test_unreadable_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            out = d / "manual"
            out.mkdir(parents=True, exist_ok=True)
            (out / "broken.json").write_text("{not json")
            self.assertEqual(load_manual(d / "manual"), [])

    def test_sourceless_benchmark_is_dropped_not_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            out = d / "manual"
            out.mkdir(parents=True, exist_ok=True)
            doc = _doc(
                "l/m",
                benchmarks=[
                    {
                        "name": "Terminal-Bench",
                        "version": "2.1",
                        "score": 50.0,
                        "source": "https://example.invalid/run",
                    },
                    {
                        "name": "SWE-Bench",
                        "version": "Verified",
                        "score": 40.0,
                    },
                ],
            )
            (out / "l__m.json").write_text(json.dumps(doc))
            (models,) = load_manual(d / "manual")
        self.assertEqual(models.scores, {"Terminal-Bench 2.1": 50.0})
        self.assertEqual(
            models.sources, {"Terminal-Bench 2.1": "https://example.invalid/run"}
        )

    def test_missing_dir_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_manual(Path(tmp) / "manual"), [])


class ManualMergeTest(unittest.TestCase):
    def test_upstream_wins_conflict_and_manual_orphan_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_upstream(d, "l/dup")
            _write_manual(d, "l/dup", name="Manual Dup")
            _write_manual(d, "l/only-manual", name="Only Manual")
            (d / "overrides").mkdir(exist_ok=True)
            curated = load_curated(d / "opencode", d / "overrides", d / "manual")
        by_id = {m.model_id: m for m in curated.models}
        self.assertEqual(by_id["l/dup"].name, "DUP")
        self.assertIn("l/only-manual", by_id)
        self.assertEqual(curated.manual_orphans, {"l/dup"})

    def test_overrides_layer_onto_manual_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_upstream(d, "l/plain")
            _write_manual(d, "l/m", name="M", price=2.0)
            over = d / "overrides"
            over.mkdir(exist_ok=True)
            (over / "l__m.json").write_text(
                json.dumps(
                    {
                        "model": {
                            "id": "l/m",
                            "benchmarks": [
                                {
                                    "name": "Terminal-Bench",
                                    "version": "2.1",
                                    "score": 60.0,
                                    "metric": "acc",
                                    "source": "https://example.invalid/run",
                                }
                            ],
                        }
                    }
                )
            )
            curated = load_curated(d / "opencode", d / "overrides", d / "manual")
        by_id = {m.model_id: m for m in curated.models}
        self.assertEqual(by_id["l/m"].scores.get("Terminal-Bench 2.1"), 60.0)


class ManualGateTest(unittest.TestCase):
    def test_shipped_manual_ids_are_docs_documented(self):
        root = Path(__file__).resolve().parent.parent
        doc_slugs = set()
        for tier in ("go", "zen"):
            raw = json.loads(
                (
                    root / "opencode_models" / "data" / "plans" / f"{tier}.json"
                ).read_text()
            )
            doc_slugs.update(raw.get("models", {}))
        manual = list((root / "data" / "manual").glob("*.json"))
        self.assertTrue(manual, "no shipped manual records to gate")
        for path in manual:
            rec = json.loads(path.read_text())["model"]
            with self.subTest(record=path.name):
                self.assertIn(rec["id"].split("/")[-1], doc_slugs)
                for entry in rec.get("benchmarks", []):
                    self.assertTrue(
                        entry.get("source"),
                        f"{path.name}: {entry.get('name')} "
                        "has no source — an uncited value cannot be tiered",
                    )


class ManualConflictsTest(unittest.TestCase):
    def test_manual_orphans_are_reported_with_a_shadow_note(self):
        import contextlib
        import io

        from opencode_models.cli import _print_conflicts

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            n = _print_conflicts([], [], ["l/ghost"])
        self.assertEqual(n, 1)
        self.assertIn("l/ghost", out.getvalue())
        self.assertIn("shadowed", out.getvalue())


class ManualListTest(unittest.TestCase):
    def _run(self, *argv):
        import contextlib
        import io

        from opencode_models.cli import main

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def test_manual_scored_model_renders_graded_through_the_cli(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "opencode").mkdir(parents=True, exist_ok=True)
            (d / "manual").mkdir(parents=True, exist_ok=True)
            doc = _doc(
                "l/m-scored",
                name="M SCORED",
                price=1.0,
                benchmarks=[
                    {
                        "name": "Terminal-Bench",
                        "version": "2.1",
                        "score": 90.0,
                        "metric": "acc",
                        "source": "https://example.invalid/run",
                    },
                    {
                        "name": "SWE-Bench",
                        "version": "Verified",
                        "score": 80.0,
                        "metric": "acc",
                        "source": "https://example.invalid/run",
                    },
                ],
            )
            (d / "manual" / "l__m-scored.json").write_text(json.dumps(doc))
            # scales need a second scored model to derive anything at all
            (d / "opencode" / "l__m-peer.json").write_text(
                json.dumps(
                    _doc(
                        "l/m-peer",
                        benchmarks=[
                            {
                                "name": "Terminal-Bench",
                                "version": "2.1",
                                "score": 50.0,
                                "metric": "acc",
                                "source": "https://example.invalid/run",
                            },
                            {
                                "name": "SWE-Bench",
                                "version": "Verified",
                                "score": 40.0,
                                "metric": "acc",
                                "source": "https://example.invalid/run",
                            },
                        ],
                    )
                )
            )
            tp = d / "plans"
            tp.mkdir(parents=True, exist_ok=True)
            (tp / "zen.json").write_text(
                json.dumps(
                    {
                        "meta": {
                            "source": "test",
                            "fetched_at": "2099-01-01T00:00:00+00:00",
                            "schema_rev": 1,
                            "plans": ["zen"],
                        },
                        "models": {
                            "m-scored": {
                                "name": "M SCORED",
                                "free": False,
                                "tiers": [
                                    {
                                        "label": None,
                                        "in": 1.0,
                                        "out": 1.0,
                                        "cached": None,
                                        "cached_write": None,
                                    }
                                ],
                                "limits": None,
                                "requests_per_month": None,
                                "assumptions": None,
                            }
                        },
                    }
                )
            )
            with unittest.mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENCODE_API_KEY", None)
                code, out = self._run("--data", tmp, "list", "--source", "zen")
        self.assertEqual(code, 0)
        row = next(l for l in out.splitlines() if "M SCORED" in l)
        self.assertFalse(row.lstrip().startswith("·"), row)


class OrphanScoreCoverageTest(unittest.TestCase):
    """Orphan pins that can score get a manual base record.

    Four curated pins name models upstream never carried; each pairs its
    pinned axis with a second (board TB 4.0 or a second pinned cell), so a
    manual base record is what turns them from orphans into scored rows.
    Singles with no pairing stay orphans and are not covered here.
    """

    IDS = (
        "anthropic/claude-fable-5-1",
        "anthropic/claude-opus-5",
        "openai/gpt-6-astra",
        "minimax/minimax-m3.1-flash",
    )

    def test_each_orphan_scores_from_manual_plus_override_plus_board(self):
        root = Path(__file__).resolve().parent.parent
        comp = compose_catalog(
            [],
            load_manual(root / "data" / "manual"),
            load_overrides(root / "data" / "overrides"),
            {},
            [
                BoardRow(
                    benchmark="Terminal-Bench 4.0",
                    model_name="Fable 5.1",
                    score=57.88,
                    source="https://www.tbench.ai/leaderboard/terminal-bench/4.0",
                ),
                BoardRow(
                    benchmark="Terminal-Bench 4.0",
                    model_name="Opus 5",
                    score=53.94,
                    source="https://www.tbench.ai/leaderboard/terminal-bench/4.0",
                ),
                BoardRow(
                    benchmark="Terminal-Bench 4.0",
                    model_name="GPT-6 Astra",
                    score=58.18,
                    source="https://www.tbench.ai/leaderboard/terminal-bench/4.0",
                ),
            ],
        )
        self.assertEqual(set(comp.curated.orphans) & set(self.IDS), set())
        table = scales(comp.models)
        by_id = {m.model_id: m for m in comp.models}
        for mid in self.IDS:
            with self.subTest(model=mid):
                self.assertIn(mid, by_id)
                self.assertIsNotNone(score_of(by_id[mid], table), mid)


if __name__ == "__main__":
    unittest.main()
