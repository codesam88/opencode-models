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


def _write_modelsdev(directory, hours_old=1):
    """A fresh models.dev slice, so the sync run does not fetch it."""
    directory = Path(directory)
    (directory / "modelsdev.json").write_text(
        json.dumps(
            {
                "meta": _old_meta(hours_old),
                "providers": {
                    "opencode-go": {"models": {}},
                    "opencode": {"models": {}},
                },
            }
        )
    )


def _modelsdev_payload():
    return json.dumps(
        {
            "opencode-go": {"id": "opencode-go", "models": {"a": {"id": "a"}}},
            "opencode": {"id": "opencode", "models": {"b": {"id": "b"}}},
            "some-other-lab": {"id": "some-other-lab", "models": {"x": {"id": "x"}}},
        }
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
            _write_modelsdev(tmp)
            code, out = _run_sync(Path(tmp), get=fail)
        self.assertEqual(code, 0)
        self.assertIn("up-to-date", out)
        self.assertIn("0 refreshed", out)
        self.assertNotIn("warning", out)

    def test_stale_modelsdev_refreshes_from_its_source(self):
        def fake_get(url):
            if "models.dev" in url:
                return _modelsdev_payload()
            if "console/go" in url:
                return go_fixture.FIXTURE_HTML
            if url.endswith("/docs/zen/"):
                return zen_fixture.FIXTURE_HTML
            raise OSError(f"unexpected url: {url}")

        with tempfile.TemporaryDirectory() as tmp:
            _write_snapshot(tmp, "go", hours_old=1)
            _write_snapshot(tmp, "zen", hours_old=1)
            _write_available(tmp, "go", hours_old=1)
            _write_available(tmp, "zen", hours_old=1)
            _write_modelsdev(tmp, hours_old=30)
            code, out = _run_sync(Path(tmp), get=fake_get)
            body = json.loads((Path(tmp) / "modelsdev.json").read_text())
        self.assertEqual(code, 0)
        self.assertIn("modelsdev: refreshed (2 models)", out)
        self.assertNotIn("some-other-lab", body["providers"])


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
            _write_modelsdev(d)
            code, _ = _run_sync(
                d,
                get=lambda url: (_ for _ in ()).throw(
                    AssertionError("no network expected")
                ),
            )
            after = (catalog.stat().st_mtime_ns, boards.stat().st_mtime_ns)
        self.assertEqual(code, 0)
        self.assertEqual(before, after)


class SyncForceCheckTest(unittest.TestCase):
    def _cli(self, *argv):
        import io as _io

        from opencode_models.cli import main

        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(list(argv))
        return code, buf.getvalue()

    def _fixture_get(self, url):
        if "console/go" in url:
            return go_fixture.FIXTURE_HTML
        if url.endswith("/docs/zen/"):
            return zen_fixture.FIXTURE_HTML
        raise OSError(f"unexpected url: {url}")

    def test_force_refreshes_fresh_sources(self):
        import opencode_models.cli as cli

        with tempfile.TemporaryDirectory() as tmp:
            _write_snapshot(tmp, "go", hours_old=1)
            _write_snapshot(tmp, "zen", hours_old=1)
            _write_available(tmp, "go", hours_old=1)
            _write_available(tmp, "zen", hours_old=1)
            orig = cli.http_get
            cli.http_get = self._fixture_get
            try:
                code, out = self._cli("--data", tmp, "sync", "--force")
            finally:
                cli.http_get = orig
            models = json.loads((Path(tmp) / "plans" / "go.json").read_text())["models"]
        self.assertEqual(code, 0)
        self.assertIn("plans/go: refreshed", out)
        self.assertIn("model-a", models)

    def test_check_passes_on_freshly_synced_snapshots(self):
        import opencode_models.cli as cli

        with tempfile.TemporaryDirectory() as tmp:
            orig = cli.http_get
            cli.http_get = self._fixture_get
            try:
                code, _ = self._cli("--data", tmp, "sync", "--force")
                self.assertEqual(code, 0)
                code, _ = self._cli("--data", tmp, "sync", "--check")
            finally:
                cli.http_get = orig
        self.assertEqual(code, 0)

    def test_check_fails_on_corrupted_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_snapshot(tmp, "go", hours_old=1)
            _write_snapshot(tmp, "zen", hours_old=1)
            path = Path(tmp) / "plans" / "go.json"
            doc = json.loads(path.read_text())
            doc["models"]["m"] = {
                "name": "M",
                "free": False,
                "tiers": [{"label": None, "in": 1.0, "out": 1.0, "cached": 1.0}],
                "limits": {"go": 600, "go_plus": 600},
                "requests_per_month": {"go": 1, "go_plus": 1},
                "assumptions": {"in": 1, "cached": 1, "out": 1},
            }
            path.write_text(json.dumps(doc))
            code, out = self._cli("--data", tmp, "sync", "--check")
        self.assertEqual(code, 1)
        self.assertIn("gate error", out)

    def test_check_fails_on_missing_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = self._cli("--data", tmp, "sync", "--check")
        self.assertEqual(code, 1)


class RefreshPlanAsJobTest(unittest.TestCase):
    """A read command's lazy plan refresh reports itself as one job.

    The refresh used to drop its report lines wherever the caller
    collected them, with nothing marking where the background work began
    or where it ended — so the command's own output ran straight on.
    """

    @staticmethod
    def _fake_get(url):
        if "console/go" in url:
            return go_fixture.FIXTURE_HTML
        if url.endswith("/docs/zen/"):
            return zen_fixture.FIXTURE_HTML
        raise OSError(f"unexpected url: {url}")

    def _run(self, data_dir, tiers, get=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sync_mod.refresh_plan_as_job(
                Path(data_dir), tiers, get=get or self._fake_get
            )
        return out.getvalue()

    @staticmethod
    def _trailing_blank_lines(text: str) -> int:
        return len(text) - len(text.rstrip("\n"))

    def test_a_stale_tier_opens_with_a_header_and_closes_with_a_blank_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_snapshot(tmp, "go", hours_old=30)
            text = self._run(tmp, ["go"])
        self.assertTrue(
            text.startswith("Checking models... (last update: 1d)"), repr(text)
        )
        self.assertIn("plans/go: refreshed", text)
        # exactly one blank line closes the job: the report's own final
        # newline, then the separator the caller's output starts after
        self.assertEqual(self._trailing_blank_lines(text), 2, repr(text))

    def test_several_stale_tiers_share_one_header_and_one_separator(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_snapshot(tmp, "go", hours_old=30)
            _write_snapshot(tmp, "zen", hours_old=50)
            text = self._run(tmp, ["go", "zen"])
        self.assertEqual(text.count("Checking models..."), 1, repr(text))
        self.assertIn("plans/go: refreshed", text)
        self.assertIn("plans/zen: refreshed", text)
        self.assertEqual(self._trailing_blank_lines(text), 2, repr(text))

    def test_fresh_snapshots_print_nothing_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_snapshot(tmp, "go", hours_old=1)
            _write_snapshot(tmp, "zen", hours_old=1)
            text = self._run(tmp, ["go", "zen"])
        self.assertEqual(text, "")

    def test_a_frozen_refresh_leaves_no_job_block_behind(self):
        # read-command tests freeze refresh_plan to keep the network out;
        # freezing must suppress the header and the separator too, or the
        # job claims work that never happened
        with tempfile.TemporaryDirectory() as tmp:
            _write_snapshot(tmp, "go", hours_old=30)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                with mock.patch.object(
                    sync_mod, "refresh_plan", return_value=("fresh", (), None)
                ):
                    sync_mod.refresh_plan_as_job(Path(tmp), ["go"])
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
