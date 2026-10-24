"""`usage` command: per-window Go allowance view, Week then Month."""

from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import availability
from .. import sync as sync_mod
from ..allowance import WINDOW_FRACTIONS, cost_per_request
from ..auth import load_key
from ..live import api_get as _api_get
from ..pricing import USAGE_EXPORT_URL
from . import fail
from .render import format_usage_tables

#: `usage` grid headings.
USAGE_LEFT_HEADERS = ("Model", "   Spent", "Limit")
USAGE_RIGHT_HEADERS = ("Reqs", "In", "Out", "Cached", "¢/req", "Est.")

#: credit balance in the header line (optional: person-only, a service
#: key gets a 403 and the balance reads n/a).
GO_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
GO_BILLING_URL = "https://opencode.ai/console/api/billing/status"

#: Append-only poll history under ``<data>/usage/``: one JSON line per
#: successful window poll, pruned to the newest few.
GO_HISTORY_NAME = "go_windows.jsonl"
GO_HISTORY_MAX_LINES = 200

#: Nominal window lengths — the page publishes the fractions (20/50/100%
#: of the monthly limit), not the clock, so a window's spend starts at
#: ``resetsAt`` minus this length. The 720h month is a 30-day
#: approximation of the subscription-anchored month: no API exposes the
#: true billing period, so a 28/31-day month skews the Month LIMIT
#: slightly. The weekly 168h is exact (Monday 00:00 UTC resets).
GO_WINDOW_HOURS = {"rolling": 5.0, "weekly": 168.0, "monthly": 720.0}


def _parse_iso(value):
    """An ISO-8601 timestamp as aware UTC, or None when unparseable."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_text(past: datetime, now: datetime) -> str:
    """How long ago *past* was, coarse: 59m, 23h, then days."""
    seconds = max(0.0, (now - past).total_seconds())
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _poll_reason(exc: BaseException) -> str:
    """Short text for a failed poll: the HTTP code when there is one."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError) and exc.reason:
        return str(exc.reason)
    if isinstance(exc, KeyError):
        return f"missing {exc}"
    return type(exc).__name__


def _fetch_go_usage(get):
    """(windows, error): the three API windows, or why they are absent."""
    try:
        usage = json.loads(get(GO_USAGE_URL))["usage"]
        windows = {}
        for name in WINDOW_FRACTIONS:
            window = dict(usage[name])
            window["percent"]  # KeyError: an incomplete payload degrades
            windows[name] = window
    except (OSError, ValueError, KeyError) as exc:
        return None, _poll_reason(exc)
    return windows, None


def _parse_export_rows(text: str) -> list[dict]:
    """The usage export as numeric rows; malformed lines are skipped.

    Same CSV shape the free-tier refresh reads (pricing.py): one line
    per UTC day x model. ``cost_micro_cents`` is literal micro-cents
    ($ = /1e8) — the scale the tokens x page-price spot-check confirms.
    """
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            rows.append(
                {
                    "day": date.fromisoformat(row["day"]),
                    "provider": (row.get("provider") or "").strip(),
                    "model": (row.get("model") or "").strip(),
                    "requests": int(row.get("requests") or 0),
                    "input_tokens": int(row.get("input_tokens") or 0),
                    "output_tokens": int(row.get("output_tokens") or 0),
                    "cache_read_tokens": int(row.get("cache_read_tokens") or 0),
                    "cache_write_tokens": int(row.get("cache_write_5m_tokens") or 0)
                    + int(row.get("cache_write_1h_tokens") or 0),
                    "cost": int(row.get("cost_micro_cents") or 0) / 1e8,
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def _fetch_go_export(get):
    """(rows, error): parsed trailing-30d export rows, or the failure."""
    try:
        rows = _parse_export_rows(get(USAGE_EXPORT_URL))
    except (OSError, ValueError) as exc:
        return None, _poll_reason(exc)
    return rows, None


def _fetch_go_balance(get):
    """(dollars, error): the Zen credit balance, or why it is absent.

    The route is person-only: a 403 here is expected with a service key
    and the caller degrades to a footnote instead of failing.
    """
    try:
        balance = json.loads(get(GO_BILLING_URL))["balanceMicroCents"]
        return int(balance) / 1e8, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, _poll_reason(exc)


def _usage_history_path(data_dir: Path) -> Path:
    return Path(data_dir) / "usage" / GO_HISTORY_NAME


def _read_usage_history(data_dir: Path) -> list[dict]:
    """Past polls, oldest first; lines that are not usable are skipped."""
    try:
        lines = _usage_history_path(data_dir).read_text().splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if all(window in record for window in WINDOW_FRACTIONS):
            records.append(record)
    return records


def _append_usage_history(data_dir: Path, windows: dict) -> dict:
    """Record one successful poll, pruning the file to the last few."""
    record = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **windows,
    }
    records = (_read_usage_history(data_dir) + [record])[-GO_HISTORY_MAX_LINES:]
    path = _usage_history_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return record


def _window_start(resets_at, window: str, first_day):
    """(first day counted, clamped, true start): where spend starts.

    The export is trailing 30d, so a window that opened before its first
    day is only partially observable — spend counts from the export's
    first day, clamped, and the caller marks it partial in the section
    header rather than silently under-counting.
    """
    resets = _parse_iso(resets_at)
    if resets is None:
        return first_day, first_day is not None, None
    start = (resets - timedelta(hours=GO_WINDOW_HOURS[window])).date()
    if first_day is None or start >= first_day:
        return start, False, start
    return first_day, True, start


def _window_totals(rows: list[dict], start_day) -> dict:
    """``{model: {spend, requests, tokens, rows}}`` for Go traffic since a day.

    Only ``opencode-go`` rows enter the Go allowance: Zen traffic shares
    model ids but draws on the balance, not the plan's dollar windows.
    Token fields (input/cache/output) feed the per-window mix columns.
    """
    totals = {}
    for row in rows:
        if row["provider"] != "opencode-go" or row["day"] < start_day:
            continue
        acc = totals.setdefault(
            row["model"],
            {
                "spend": 0.0,
                "requests": 0,
                "input_tokens": 0,
                "cache_tokens": 0,
                "output_tokens": 0,
                "rows": [],
            },
        )
        acc["spend"] += row["cost"]
        acc["requests"] += row["requests"]
        acc["input_tokens"] += row.get("input_tokens", 0)
        acc["cache_tokens"] += row.get("cache_read_tokens", 0) + row.get(
            "cache_write_tokens", 0
        )
        acc["output_tokens"] += row.get("output_tokens", 0)
        acc["rows"].append(row)
    return totals


def _window_section_header(window: str, resets_at, now, partial: bool = False) -> str:
    """`Week (~3 days since Monday 10-19 → resets 10-26 00:00 UTC)` header.

    The start is ``resetsAt`` minus the nominal window length (the page
    publishes fractions, not the clock); elapsed days are ``now`` minus
    that start. All timestamps are UTC (API, export days and ``now``);
    the reset time carries the UTC marker so it is never read as local.
    Unparseable resets fall back to ``→?``.
    """
    title = "Week" if window == "weekly" else "Month"
    resets = _parse_iso(resets_at)
    reset_text = resets.strftime("%m-%d %H:%M UTC") if resets else "?"
    if resets is None:
        return f"{title} (resets →?)"
    start = resets - timedelta(hours=GO_WINDOW_HOURS[window])
    elapsed = max(0, int((now - start).total_seconds() // 86400))
    if window == "weekly":
        since = f"{start.strftime('%A')} {start.strftime('%m-%d')}"
    else:
        since = start.strftime("%m-%d")
    tail = " · partial" if partial else ""
    return f"{title} (~{elapsed} days since {since} → resets {reset_text}{tail})"


def _window_model_rows(
    models: dict, plan: str, window: str, totals: dict
) -> list[dict]:
    """One row per model with window spend: reqs, token mix, rates, caps.

    Token shares are shares of tokens (in/cached/out over their sum).
    ``cap`` is this model's window cap in dollars (``None`` for free or
    cap-less models); ``use`` is spend over the cap; ``est`` is the
    page-based $/req estimate (``None`` without tiers+assumptions);
    ``off`` is measured avg over the estimate (``None`` when the
    estimate is missing); rows sort by spend descending.
    """
    rows = []
    for slug, acc in totals.items():
        reqs = acc.get("requests", 0)
        spend = acc.get("spend", 0.0)
        if reqs <= 0 and spend <= 0:
            continue
        tin = acc.get("input_tokens", 0)
        tcache = acc.get("cache_tokens", 0)
        tout = acc.get("output_tokens", 0)
        tsum = tin + tcache + tout
        if tsum > 0:
            pin, pcached, pout = (
                100.0 * tin / tsum,
                100.0 * tcache / tsum,
                100.0 * tout / tsum,
            )
        else:
            pin = pcached = pout = 0.0
        rec = models.get(slug, {})
        limit = (rec.get("limits") or {}).get(plan)
        est = cost_per_request(rec.get("tiers") or [], rec.get("assumptions"))
        avg = spend / reqs if reqs else 0.0
        off = avg / est if est else None
        if rec.get("free") or limit is None:
            use = None
            cap = None
        else:
            cap = WINDOW_FRACTIONS[window] * limit
            use = spend / cap if cap else 0.0
        rows.append(
            {
                "slug": slug,
                "reqs": reqs,
                "pin": pin,
                "pcached": pcached,
                "pout": pout,
                "avg": avg,
                "spend": spend,
                "use": use,
                "cap": cap,
                "est": est,
                "off": off,
            }
        )
    rows.sort(key=lambda r: (-r["spend"], -r["reqs"], r["slug"]))
    return rows


def _format_spent(row: dict) -> str:
    """``$cost (pct%)``, left-aligned with sub-$10 amounts space-padded.

    The leading space on amounts under $10 lines the decimals up with
    wider amounts (``$10.80`` vs `` $3.39``); free rows read `` $0.00``.
    """
    if row["use"] is None:
        return f"${row['spend']:.2f}" if row["spend"] >= 10 else f" ${row['spend']:.2f}"
    pct = f" ({row['use'] * 100:.0f}%)"
    if row["spend"] >= 10:
        return f"${row['spend']:.2f}" + pct
    return f" ${row['spend']:.2f}" + pct


def _format_avg(row: dict) -> str:
    """Measured ¢/req, or a dash when nothing metered was spent."""
    if not row["spend"]:
        return "-"
    return f"{row['avg'] * 100:.2f}"


def _format_off(row: dict) -> str:
    """Page-estimated ¢/req, or a dash without tiers+assumptions."""
    est = row["est"]
    if not est:
        return "-"
    return f"{est * 100:.2f}"


def _sections(models, plan, spend, record, starts, now) -> list:
    """One (title, left rows, right rows) per window, Week then Month."""
    sections = []
    for window in ("weekly", "monthly"):
        rows = _window_model_rows(models, plan, window, spend[window])
        left_rows, right_rows = [], []
        for row in rows:
            if row["use"] is None:
                limit_text = "-"
            else:
                limit_text = f"${row['cap']:.2f}"
            left_rows.append([row["slug"], _format_spent(row), limit_text])
            right_rows.append(
                [
                    f"{row['reqs']:,}",
                    f"{row['pin']:.0f}%",
                    f"{row['pout']:.0f}%",
                    f"{row['pcached']:.0f}%",
                    _format_avg(row),
                    _format_off(row),
                ]
            )
        title = _window_section_header(
            window,
            (record.get(window) or {}).get("resetsAt"),
            now,
            partial=starts[window][1],
        )
        sections.append((f"{title} · {len(left_rows)} models", left_rows, right_rows))
    return sections


def run(args: argparse.Namespace) -> int:
    """Per-window Go allowance view: Week then Month sections."""
    data_dir = Path(args.data)
    lines: list[str] = []
    plan = "go_plus" if args.go_pro else "go"
    _status, _msgs, _age = sync_mod.refresh_plan(data_dir, "go")
    if _status != "fresh":
        lines.extend(_msgs)
    try:
        snapshot, origin = availability.load_plan_snapshot(data_dir)
    except (OSError, ValueError) as exc:
        return fail(f"plan snapshot unreadable ({exc})")
    models = snapshot.get("models") or {}
    now = datetime.now(timezone.utc)
    history = _read_usage_history(data_dir)
    key = load_key()
    get = _api_get(key) if key else None
    if get:
        usage, usage_error = _fetch_go_usage(get)
    else:
        usage, usage_error = None, "no OPENCODE_API_KEY"
    if usage is not None:
        record = _append_usage_history(data_dir, usage)
    else:
        if not history:
            return fail("no usage data — set OPENCODE_API_KEY (`auth`) and retry")
        record = history[-1]
        if usage_error is not None:
            lines.append(
                f"warning: window API unavailable ({usage_error}) — using last poll"
            )
    if get:
        rows_export, export_error = _fetch_go_export(get)
        if export_error is not None:
            lines.append(f"warning: usage export unavailable ({export_error})")
    else:
        rows_export, export_error = None, None
    balance = None
    balance_error = None
    if get:
        balance, balance_error = _fetch_go_balance(get)
        if balance_error is not None:
            lines.append(f"warning: balance unavailable ({balance_error})")
    first_day = min((r["day"] for r in rows_export or []), default=None)
    starts = {
        w: _window_start((record.get(w) or {}).get("resetsAt"), w, first_day)
        for w in ("weekly", "monthly")
    }
    spend = {
        w: _window_totals(rows_export or [], starts[w][0])
        for w in ("weekly", "monthly")
    }

    fetched = _parse_iso((snapshot.get("meta") or {}).get("fetched_at"))
    snapshot_age = _age_text(fetched, now) if fetched else "?"
    label = "Go Plus" if plan == "go_plus" else "Go"
    balance_text = f"${balance:,.2f}" if balance is not None else "n/a"
    lines.append(
        f"{label} plan · snapshot {snapshot_age} old ({origin}) · "
        f"balance {balance_text}"
    )
    lines.extend(
        format_usage_tables(
            _sections(models, plan, spend, record, starts, now),
            USAGE_LEFT_HEADERS,
            USAGE_RIGHT_HEADERS,
        )
    )
    lines.append("")
    lines.append("¢/req: measured average from reported usage (spend ÷ requests)")
    lines.append("Est.: estimated average ¢/req from opencode.ai page assumptions")
    print("\n".join(lines))
    return 0
