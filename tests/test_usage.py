"""`usage`: Week/Month Go allowance view, offline in tests.

Small hand-built fixtures, no network and no live data: a usage payload
(3 windows), a 3-day export excerpt (2 priced + 1 free model, one Zen
row that must stay out of the Go math, a weekly-reset day boundary), a
billing payload, a plan-snapshot excerpt and a usage-history file.

Expected numbers (page prices from the fixture snapshot):
  muse-spark-1.3: cpr = (620*0.1 + 71400*0.002 + 300*0.2)/1e6 = 0.0002648
    weekly spend = 3.60 + 7.20 = 10.80 (day 10-18 is before the 10-19
    weekly start), monthly = 14.40; weekly implied = 10.80/30 = 36%.
  step-5-preview: cpr = (800*0.6 + 60000*0.06 + 500*2.4)/1e6 = 0.00528
    weekly = 1.1304 + 2.2608 = 3.3912 (45% of the 7.50 weekly cap).
  kimi-k3: cpr = 0.02415, monthly = weekly = 0.2415 (10-20 only).
  freebie-free: weekly 800 req, monthly 1,000 req, no dollars.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from opencode_models import cli as cli_mod
from opencode_models.cli import main
from opencode_models.commands import usage as usage_cmd

#: Fixture tests must be hermetic: no key in the environment and none in
#: the user config file, so the no-key paths are actually reachable.
os.environ.pop("OPENCODE_API_KEY", None)
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="opencode-models-test-home-")

RESETS = {
    "rolling": "2026-10-22T00:59:03.000Z",
    "weekly": "2026-10-26T00:00:00.000Z",
    "monthly": "2026-11-17T00:00:00.000Z",
}

USAGE_PAYLOAD = {
    "usage": {
        "rolling": {"status": "ok", "percent": 1, "resetsAt": RESETS["rolling"]},
        "weekly": {"status": "ok", "percent": 5, "resetsAt": RESETS["weekly"]},
        "monthly": {"status": "ok", "percent": 2, "resetsAt": RESETS["monthly"]},
    }
}

BILLING_PAYLOAD = json.dumps(
    {
        "billingMode": "prepaid",
        "mode": "pay-as-you-go",
        "balanceMicroCents": "241248195",
        "creditLimitMicroCents": None,
        "availableMicroCents": "241248195",
        "canPurchaseCredits": True,
        "canEnableAutoRecharge": True,
    }
)


def _csv_row(day, provider, model, req, inp, out, cached, cost):
    return (
        f"{day},service_account,svc_1,Test User,{provider},{model},"
        f"{req},{inp},{out},{cached},0,0,{cost}"
    )


EXPORT_HEADER = (
    "day,user_type,user_id,user_name,provider,model,requests,input_tokens,"
    "output_tokens,cache_read_tokens,cache_write_5m_tokens,"
    "cache_write_1h_tokens,cost_micro_cents"
)

EXPORT_CSV = "\n".join(
    [
        EXPORT_HEADER,
        # muse-spark-1.3: $3.60/day at page prices (in .1, cached .002, out .2)
        _csv_row(
            "2026-10-18",
            "opencode-go",
            "muse-spark-1.3",
            100,
            10000000,
            3000000,
            1000000000,
            360000000,
        ),
        _csv_row(
            "2026-10-19",
            "opencode-go",
            "muse-spark-1.3",
            100,
            10000000,
            3000000,
            1000000000,
            360000000,
        ),
        _csv_row(
            "2026-10-20",
            "opencode-go",
            "muse-spark-1.3",
            200,
            20000000,
            6000000,
            2000000000,
            720000000,
        ),
        # step-5-preview: $1.1304/day (in .6, cached .06, out 2.4)
        _csv_row(
            "2026-10-18",
            "opencode-go",
            "step-5-preview",
            30,
            24000,
            15000,
            18000000,
            113040000,
        ),
        _csv_row(
            "2026-10-19",
            "opencode-go",
            "step-5-preview",
            30,
            24000,
            15000,
            18000000,
            113040000,
        ),
        _csv_row(
            "2026-10-20",
            "opencode-go",
            "step-5-preview",
            60,
            48000,
            30000,
            36000000,
            226080000,
        ),
        # kimi-k3: $0.2415 on the last day only (in 1.0, cached .25, out 5)
        _csv_row(
            "2026-10-20",
            "opencode-go",
            "kimi-k3",
            10,
            9000,
            4000,
            850000,
            24150000,
        ),
        # freebie-free: requests only, zero cost
        _csv_row(
            "2026-10-18",
            "opencode-go",
            "freebie-free",
            200,
            500000,
            100000,
            100000000,
            0,
        ),
        _csv_row(
            "2026-10-19",
            "opencode-go",
            "freebie-free",
            500,
            1250000,
            250000,
            250000000,
            0,
        ),
        _csv_row(
            "2026-10-20",
            "opencode-go",
            "freebie-free",
            300,
            750000,
            150000,
            150000000,
            0,
        ),
        # Zen traffic for a Go model: must never enter the Go windows
        _csv_row(
            "2026-10-20",
            "opencode",
            "muse-spark-1.3",
            999,
            99000000,
            99000000,
            9900000000,
            9900000000,
        ),
    ]
)


def _model(name, tiers, limits, assumptions, free=False):
    return {
        "name": name,
        "free": free,
        "tiers": tiers,
        "limits": {"go": limits[0], "go_plus": limits[1]},
        "requests_per_month": {"go": None, "go_plus": None},
        "assumptions": assumptions,
    }


SNAPSHOT_MODELS = {
    "muse-spark-1.3": _model(
        "Muse Spark 1.3",
        [{"label": None, "in": 0.1, "out": 0.2, "cached": 0.002, "cached_write": None}],
        (60, 120),
        {"in": 620, "cached": 71400, "out": 300},
    ),
    "step-5-preview": _model(
        "Step 5 Preview",
        [{"label": None, "in": 0.6, "out": 2.4, "cached": 0.06, "cached_write": 0.15}],
        (15, 60),
        {"in": 800, "cached": 60000, "out": 500},
    ),
    "kimi-k3": _model(
        "Kimi K3",
        [{"label": None, "in": 1.0, "out": 5.0, "cached": 0.25, "cached_write": None}],
        (15, 60),
        {"in": 900, "cached": 85000, "out": 400},
    ),
    "no-assume": _model(
        "No Assume",
        [{"label": None, "in": 4.0, "out": 20.0, "cached": 0.2, "cached_write": None}],
        (60, 240),
        None,
    ),
    "freebie-free": _model("Freebie", [], (None, None), None, free=True),
}


def _write_plan(tmp, fetched_at):
    plan = {
        "meta": {
            "source": "https://opencode.ai/v2/docs/console/go",
            "fetched_at": fetched_at.isoformat(),
            "plans": ["go", "go_plus"],
        },
        "windows": {"rolling": 0.2, "weekly": 0.5, "monthly": 1.0},
        "models": SNAPSHOT_MODELS,
    }
    out = Path(tmp) / "plans"
    out.mkdir(parents=True, exist_ok=True)
    (out / "go.json").write_text(json.dumps(plan))


def _history_line(fetched_at, percents, resets=RESETS):
    record = {"fetched_at": fetched_at.isoformat()}
    for window in ("rolling", "weekly", "monthly"):
        record[window] = {
            "status": "ok",
            "percent": percents[window],
            "resetsAt": resets[window],
        }
    return json.dumps(record)


def _write_history(tmp, lines):
    out = Path(tmp) / "usage"
    out.mkdir(parents=True, exist_ok=True)
    (out / "go_windows.jsonl").write_text("\n".join(lines) + "\n")


def _routes(**by_url):
    """A fake transport: exact-URL map to a body or an exception to raise."""

    def get(url):
        value = by_url.get(url)
        if value is None:
            raise AssertionError(f"unexpected URL {url}")
        if isinstance(value, BaseException):
            raise value
        return value

    return get


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "err", None, None)


class UsageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._refresh_patch = mock.patch(
            "opencode_models.cli.sync_mod.refresh_plan",
            return_value=("fresh", (), None),
        )
        self._refresh_patch.start()
        self.addCleanup(self._refresh_patch.stop)
        now = datetime.now(timezone.utc)
        _write_plan(self.tmp.name, now - timedelta(days=2))
        self.get = _routes(
            **{
                cli_mod.GO_USAGE_URL: json.dumps(USAGE_PAYLOAD),
                cli_mod.USAGE_EXPORT_URL: EXPORT_CSV,
                cli_mod.GO_BILLING_URL: BILLING_PAYLOAD,
            }
        )

    def _run(self, *extra, get=None, key="oc_sk_test"):
        buf = io.StringIO()
        patches = [
            mock.patch.object(usage_cmd, "_api_get", lambda k: get or self.get),
        ]
        if key is not None:
            patches.append(mock.patch.dict(os.environ, {"OPENCODE_API_KEY": key}))
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with contextlib.redirect_stdout(buf):
                code = main(["--data", self.tmp.name, "usage", *extra])
        return code, buf.getvalue()

    def _history_file(self):
        path = Path(self.tmp.name) / "usage" / "go_windows.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()]


class HeaderTest(UsageCase):
    def test_sections_have_ruled_headers(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertRegex(out, r"(?m)^── Week \(")
        self.assertRegex(out, r"(?m)^── Month \(")

    def test_footnotes_explain_avg_and_est_columns(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("¢/req: measured average from reported usage", out)
        self.assertIn("Est.: estimated average ¢/req from opencode.ai", out)

    def test_windows_resets_plan_and_balance(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("Go plan", out)
        self.assertIn("10-26 00:00", out)
        self.assertIn("balance $2.41", out)
        self.assertIn("snapshot 2d old (local)", out)
        self.assertIn("Week", out)
        self.assertIn("Month", out)

    def test_old_snapshot_shows_age_in_header(self):
        now = datetime.now(timezone.utc)
        _write_plan(self.tmp.name, now - timedelta(days=40))
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("40d old", out)

    def test_clamped_window_is_marked_partial_in_header(self):
        resets = dict(RESETS, monthly="2026-11-10T00:00:00.000Z")
        payload = {
            "usage": {
                w: {"status": "ok", "percent": p, "resetsAt": resets[w]}
                for w, p in (("rolling", 1), ("weekly", 5), ("monthly", 2))
            }
        }
        self.get = _routes(
            **{
                cli_mod.GO_USAGE_URL: json.dumps(payload),
                cli_mod.USAGE_EXPORT_URL: EXPORT_CSV,
                cli_mod.GO_BILLING_URL: BILLING_PAYLOAD,
            }
        )
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("partial", out)


class ModelTableTest(UsageCase):
    def test_spend_implied_percent_and_remaining(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        # Week section header (elapsed days tick with the real clock, so the
        # expectation is computed the same way rather than hardcoded)
        start = datetime(2026, 10, 19, tzinfo=timezone.utc)
        elapsed = max(
            0,
            int((datetime.now(timezone.utc) - start).total_seconds() // 86400),
        )
        self.assertIn(
            f"Week (~{elapsed} days since Monday 10-19 → resets 10-26 00:00 UTC)",
            out,
        )
        # muse: weekly excludes the 10-18 day
        self.assertIn("10.80", out)
        self.assertIn("36%", out)
        # monthly
        self.assertIn("14.40", out)
        self.assertIn("24%", out)
        # step-5: 45% of the weekly cap
        self.assertIn("3.39", out)
        self.assertIn("45%", out)
        # token mix present (headers carry no %; body cells do)
        self.assertNotIn("%IN", out.splitlines()[3])
        header = next(l for l in out.splitlines() if "Reqs" in l)
        self.assertLess(header.index("In"), header.index("Out"))
        self.assertLess(header.index("Out"), header.index("Cached"))
        # avg
        self.assertIn("3.60", out)
        # free model appears as a $0.00 row with no cap to use
        self.assertIn("freebie-free", out)
        free_line = next(l for l in out.splitlines() if "freebie-free" in l)
        self.assertIn("$0.00", free_line)
        self.assertNotIn("(", free_line)

    def test_rows_sorted_by_monthly_spend_desc(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        order = [
            out.index("muse-spark-1.3"),
            out.index("step-5-preview"),
            out.index("kimi-k3"),
        ]
        self.assertEqual(order, sorted(order))

    def test_zen_rows_never_enter_the_go_windows(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertNotIn("113.40", out)
        self.assertIn("14.40", out)

    def test_free_models_appear_as_rows_with_dash(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("freebie-free", out)
        self.assertIn("$0.00", out)

    def test_go_pro_switches_to_plus_limits(self):
        code, out = self._run("--go-pro")
        self.assertEqual(code, 0)
        self.assertIn("Go Plus plan", out)
        self.assertIn("18%", out)

    def test_section_headers_present(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("Week", out)
        self.assertIn("Month", out)
        self.assertIn("Model", out)
        self.assertIn("Reqs", out)
        self.assertIn("¢/req", out)
        self.assertIn("Spent", out)
        self.assertIn("Limit", out)
        self.assertIn("Est.", out)
        self.assertNotIn("USAGE", out)
        self.assertNotIn("AVAIL", out)


class DegradedSourceTest(UsageCase):
    def test_no_key_and_no_history_fails_with_instruction(self):
        code, out = self._run(key=None)
        self.assertEqual(code, 1)
        self.assertIn("OPENCODE_API_KEY", out)

    def test_usage_403_falls_back_to_history_with_age(self):
        now = datetime.now(timezone.utc)
        _write_history(
            self.tmp.name,
            [
                _history_line(
                    now - timedelta(hours=3), {"rolling": 1, "weekly": 4, "monthly": 1}
                ),
                _history_line(
                    now - timedelta(hours=1), {"rolling": 1, "weekly": 5, "monthly": 2}
                ),
            ],
        )
        get = _routes(
            **{
                cli_mod.GO_USAGE_URL: _http_error(cli_mod.GO_USAGE_URL, 403),
                cli_mod.USAGE_EXPORT_URL: EXPORT_CSV,
                cli_mod.GO_BILLING_URL: BILLING_PAYLOAD,
            }
        )
        code, out = self._run(get=get)
        self.assertEqual(code, 0)
        self.assertIn("Week", out)
        self.assertIn("Month", out)

    def test_export_failure_keeps_a_header_only_table(self):
        get = _routes(
            **{
                cli_mod.GO_USAGE_URL: json.dumps(USAGE_PAYLOAD),
                cli_mod.USAGE_EXPORT_URL: urllib.error.URLError("down"),
                cli_mod.GO_BILLING_URL: BILLING_PAYLOAD,
            }
        )
        code, out = self._run(get=get)
        self.assertEqual(code, 0)
        self.assertIn("Month", out)  # the header still prints
        self.assertIn("Model", out)
        self.assertNotIn("muse-spark-1.3", out)

    def test_billing_403_shows_balance_na_not_a_failure(self):
        get = _routes(
            **{
                cli_mod.GO_USAGE_URL: json.dumps(USAGE_PAYLOAD),
                cli_mod.USAGE_EXPORT_URL: EXPORT_CSV,
                cli_mod.GO_BILLING_URL: _http_error(cli_mod.GO_BILLING_URL, 403),
            }
        )
        code, out = self._run(get=get)
        self.assertEqual(code, 0)
        self.assertIn("balance n/a", out)
        self.assertIn("muse-spark-1.3", out)  # the rest still renders

    def test_window_failure_warns(self):
        now = datetime.now(timezone.utc)
        _write_history(
            self.tmp.name,
            [
                _history_line(
                    now - timedelta(hours=1), {"rolling": 1, "weekly": 5, "monthly": 2}
                )
            ],
        )
        get = _routes(
            **{
                cli_mod.GO_USAGE_URL: urllib.error.URLError("down"),
                cli_mod.USAGE_EXPORT_URL: EXPORT_CSV,
                cli_mod.GO_BILLING_URL: BILLING_PAYLOAD,
            }
        )
        code, out = self._run(get=get)
        self.assertEqual(code, 0)
        self.assertIn("warning", out.lower())
        self.assertIn("window api", out.lower())

    def test_export_failure_warns(self):
        get = _routes(
            **{
                cli_mod.GO_USAGE_URL: json.dumps(USAGE_PAYLOAD),
                cli_mod.USAGE_EXPORT_URL: urllib.error.URLError("down"),
                cli_mod.GO_BILLING_URL: BILLING_PAYLOAD,
            }
        )
        code, out = self._run(get=get)
        self.assertEqual(code, 0)
        self.assertIn("warning", out.lower())
        self.assertIn("usage export", out.lower())

    def test_balance_failure_warns(self):
        get = _routes(
            **{
                cli_mod.GO_USAGE_URL: json.dumps(USAGE_PAYLOAD),
                cli_mod.USAGE_EXPORT_URL: EXPORT_CSV,
                cli_mod.GO_BILLING_URL: urllib.error.URLError("down"),
            }
        )
        code, out = self._run(get=get)
        self.assertEqual(code, 0)
        self.assertIn("warning", out.lower())
        self.assertIn("balance", out.lower())


class HistoryTest(UsageCase):
    def test_successful_poll_appends_one_record(self):
        code, _out = self._run()
        self.assertEqual(code, 0)
        lines = self._history_file()
        self.assertEqual(len(lines), 1)
        record = lines[0]
        self.assertIn("fetched_at", record)
        self.assertEqual(record["weekly"]["percent"], 5)
        self.assertEqual(record["monthly"]["resetsAt"], RESETS["monthly"])

    def test_history_is_pruned_to_200_lines(self):
        now = datetime.now(timezone.utc)
        _write_history(
            self.tmp.name,
            [
                _history_line(
                    now - timedelta(hours=5), {"rolling": 0, "weekly": 0, "monthly": 0}
                )
            ]
            * 250,
        )
        code, _out = self._run()
        self.assertEqual(code, 0)
        lines = self._history_file()
        self.assertEqual(len(lines), 200)
        self.assertEqual(lines[-1]["weekly"]["percent"], 5)

    def test_degraded_poll_does_not_append(self):
        now = datetime.now(timezone.utc)
        _write_history(
            self.tmp.name,
            [
                _history_line(
                    now - timedelta(hours=1), {"rolling": 1, "weekly": 5, "monthly": 2}
                )
            ],
        )
        get = _routes(
            **{
                cli_mod.GO_USAGE_URL: _http_error(cli_mod.GO_USAGE_URL, 401),
                cli_mod.USAGE_EXPORT_URL: EXPORT_CSV,
                cli_mod.GO_BILLING_URL: BILLING_PAYLOAD,
            }
        )
        code, _out = self._run(get=get)
        self.assertEqual(code, 0)
        self.assertEqual(len(self._history_file()), 1)


class SnapshotResolutionTest(unittest.TestCase):
    def test_local_snapshot_wins(self):
        from opencode_models.availability import (
            load_plan_snapshot as _load_plan_snapshot,
        )

        with tempfile.TemporaryDirectory() as tmp:
            _write_plan(tmp, datetime.now(timezone.utc))
            snapshot, origin = _load_plan_snapshot(Path(tmp))
        self.assertEqual(origin, "local")
        self.assertIn("muse-spark-1.3", snapshot["models"])

    def test_shipped_fallback_when_local_is_absent(self):
        from opencode_models.availability import (
            load_plan_snapshot as _load_plan_snapshot,
        )

        with tempfile.TemporaryDirectory() as tmp:
            snapshot, origin = _load_plan_snapshot(Path(tmp))
        self.assertEqual(origin, "shipped")
        self.assertIn("models", snapshot)
        self.assertIn("meta", snapshot)


class UsageWidthTest(unittest.TestCase):
    def test_lines_fit_a_narrow_terminal(self):
        wide = "s" * 60
        SNAPSHOT_MODELS[wide] = _model(
            wide,
            [
                {
                    "label": None,
                    "in": 0.1,
                    "out": 0.2,
                    "cached": 0.002,
                    "cached_write": None,
                }
            ],
            (60, 120),
            {"in": 620, "cached": 71400, "out": 300},
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                now = datetime.now(timezone.utc)
                _write_plan(tmp, now - timedelta(days=2))
                export = "\n".join(
                    [
                        EXPORT_HEADER,
                        _csv_row(
                            "2026-10-20",
                            "opencode-go",
                            wide,
                            10,
                            10000000,
                            3000000,
                            1000000000,
                            360000000,
                        ),
                    ]
                )
                get = _routes(
                    **{
                        cli_mod.GO_USAGE_URL: json.dumps(USAGE_PAYLOAD),
                        cli_mod.USAGE_EXPORT_URL: export,
                        cli_mod.GO_BILLING_URL: BILLING_PAYLOAD,
                    }
                )
                buf = io.StringIO()
                with (
                    mock.patch.object(usage_cmd, "_api_get", lambda k: get),
                    mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}),
                    contextlib.redirect_stdout(buf),
                ):
                    code = main(["--data", tmp, "usage"])
        finally:
            del SNAPSHOT_MODELS[wide]
        self.assertEqual(code, 0)
        out = buf.getvalue()
        # full slug is shown, never truncated
        self.assertIn(wide, out)
        self.assertNotIn("…", out)


if __name__ == "__main__":
    unittest.main()
