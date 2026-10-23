"""`sync` refreshes only stale T2 sources, warning instead of failing."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from opencode_models import availability
from opencode_models import sync as sync_mod
from tests import test_go_pricing as go_fixture
from tests import test_zen_pricing as zen_fixture


def _old_meta(hours=30):
    return {
        "source": "test",
        "fetched_at": (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
            timespec="seconds"
        ),
        "schema_rev": 1,
        "plans": ["go"],
    }


def _write_snapshot(directory, tier, hours_old=30, models=None):
    directory = Path(directory)
    plans = directory / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / f"{tier}.json").write_text(
        json.dumps(
            {
                "meta": _old_meta(hours_old),
                "models": models if models is not None else {},
            }
        )
    )


def _write_available(directory, tier, hours_old=30):
    directory = Path(directory)
    available = directory / "available"
    available.mkdir(parents=True, exist_ok=True)
    (available / f"{tier}.json").write_text(
        json.dumps({"meta": _old_meta(hours_old), "models": {}})
    )


def _run_sync(data_dir, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = sync_mod.sync(data_dir, **kw)
    return code, buf.getvalue()


class SyncFreshTest(unittest.TestCase):
    def test_fresh_sources_are_not_refreshed(self):
        def fail(url):
            raise AssertionError(f"no network expected: {url}")

        with tempfile.TemporaryDirectory() as tmp:
            _write_snapshot(tmp, "go", hours_old=1)
            _write_snapshot(tmp, "zen", hours_old=1)
            _write_available(tmp, "go", hours_old=1)
            _write_available(tmp, "zen", hours_old=1)
            code, out = _run_sync(Path(tmp), get=fail)
        self.assertEqual(code, 0)
        self.assertIn("up-to-date", out)
        self.assertIn("0 refreshed", out)
        self.assertNotIn("warning", out)


class SyncStalePlansTest(unittest.TestCase):
    def test_stale_plan_snapshot_refreshes_from_docs(self):
        def fake_get(url):
            if "console/go" in url:
                return go_fixture.FIXTURE_HTML
            if url.endswith("/docs/zen/"):
                return zen_fixture.FIXTURE_HTML
            raise OSError(f"unexpected url: {url}")

        with tempfile.TemporaryDirectory() as tmp:
            _write_available(tmp, "go", hours_old=1)
            _write_available(tmp, "zen", hours_old=1)
            code, out = _run_sync(Path(tmp), get=fake_get)
            models = json.loads((Path(tmp) / "plans" / "go.json").read_text())["models"]
        self.assertEqual(code, 0)
        self.assertIn("plans/go: refreshed", out)
        self.assertIn("model-a", models)


class SyncFailureTest(unittest.TestCase):
    def test_unreachable_docs_warn_and_keep_snapshots(self):
        def down(url):
            raise OSError("offline")

        with tempfile.TemporaryDirectory() as tmp:
            _write_snapshot(tmp, "go", hours_old=30)
            before = (Path(tmp) / "plans" / "go.json").read_text()
            _write_available(tmp, "go", hours_old=1)
            _write_available(tmp, "zen", hours_old=1)
            code, out = _run_sync(Path(tmp), get=down)
            after = (Path(tmp) / "plans" / "go.json").read_text()
        self.assertEqual(code, 0)
        self.assertIn("warning", out)
        self.assertEqual(before, after)

    def test_keyless_availability_warns_and_keeps_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_snapshot(tmp, "go", hours_old=1)
            _write_snapshot(tmp, "zen", hours_old=1)
            _write_available(tmp, "go", hours_old=30)
            _write_available(tmp, "zen", hours_old=1)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENCODE_API_KEY", None)
                with mock.patch.object(availability, "load_key", return_value=None):
                    code, out = _run_sync(Path(tmp), get=lambda url: "{}")
        self.assertEqual(code, 0)
        self.assertIn("available/go: kept existing snapshot", out)


class SyncT1UntouchedTest(unittest.TestCase):
    def test_catalog_and_boards_are_never_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "opencode").mkdir()
            catalog = d / "opencode" / "l__m.json"
            catalog.write_text("{}")
            (d / "boards").mkdir()
            boards = d / "boards" / "tbench.json"
            boards.write_text("[]")
            before = (catalog.stat().st_mtime_ns, boards.stat().st_mtime_ns)
            _write_snapshot(d, "go", hours_old=1)
            _write_snapshot(d, "zen", hours_old=1)
            _write_available(d, "go", hours_old=1)
            _write_available(d, "zen", hours_old=1)
            code, _ = _run_sync(
                d,
                get=lambda url: (_ for _ in ()).throw(
                    AssertionError("no network expected")
                ),
            )
            after = (catalog.stat().st_mtime_ns, boards.stat().st_mtime_ns)
        self.assertEqual(code, 0)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
