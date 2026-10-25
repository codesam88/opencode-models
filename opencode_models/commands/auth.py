"""`auth` command: prompt, save owner-only, verify against live endpoints."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from ..auth import save_key, save_plan_preference
from ..live import api_get as _api_get
from ..live import fetch_live, fetch_served
from . import fail


@dataclass(frozen=True)
class VerifyResult:
    """The verification decision: verified counts, or a rejection reason."""

    ok: bool
    model_count: int = 0
    tier_count: int = 0
    error: str | None = None


def verify_key(key: str, get=None) -> VerifyResult:
    """Verify a key against both live endpoints. No printing, no exit codes.

    The transport is a parameter (tests); production passes the
    key-bound getter. A rejection is data, not a failure — the key may
    still be valid offline.
    """
    try:
        get = get if get is not None else _api_get(key)
        live = fetch_served(get)
        fetch_live(get)
    except (OSError, ValueError, KeyError) as exc:
        return VerifyResult(ok=False, error=str(exc))
    served = set().union(*live.values())
    return VerifyResult(
        ok=True,
        model_count=len(served),
        tier_count=len([t for t in live.values() if t]),
    )


def run(args: argparse.Namespace) -> int:
    """Ask for the Console service-account key and save it owner-only.

    The prompt never echoes, the key is never printed back, and the file
    is created (or tightened) to owner-only access. The saved key is then
    verified against both live endpoints — the inference gateway's model
    lists and the config API — so a personal key is proven before it is
    accepted. A rejection warns but keeps the file, so an offline first
    run is not punished.
    """
    import getpass

    if getattr(args, "plan", None) is not None:
        path = save_plan_preference({"go-plus": "go_plus", "go": "go"}[args.plan])
        print(f"workspace plan recorded as {args.plan} ({path})")
    if not sys.stdin.isatty():
        return fail(
            "auth needs an interactive terminal — pipe a key via "
            "OPENCODE_API_KEY instead"
        )
    key = getpass.getpass("OPENCODE_API_KEY: ").strip()
    if not key:
        return fail("no key entered — nothing saved")
    path = save_key(key)
    print(f"saved to {path} (owner-only)")
    result = verify_key(key)
    if not result.ok:
        print(f"warning: key saved but not verified ({result.error})")
        return 0
    print(
        f"verified live: {result.model_count} models across {result.tier_count} tiers"
    )
    return 0
