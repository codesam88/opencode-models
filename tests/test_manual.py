"""Manual catalog: hand-written records for offered models upstream lacks."""

import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from opencode_models.models import load_manual
from opencode_models.overrides import load_curated

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
                            "fetched_at": "2026-01-01T00:00:00+00:00",
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


if __name__ == "__main__":
    unittest.main()
