"""Command-line interface.

Usage::

    python3 -m modelpareto fetch            # refresh data/ from opencode.ai
    python3 -m modelpareto benchmarks       # list benchmarks and coverage
    python3 -m modelpareto render [...]     # write the Pareto HTML report

Exit codes: 0 success, 1 usage or data error (message on stdout).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .benchmarks import available
from .data import load_models
from .fetch import http_get, refresh
from .render import render_html

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "pareto.html"


def _fail(msg: str) -> int:
    print(msg)
    return 1


def _cmd_benchmarks(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")
    for name, count in available(load_models(data_dir)):
        print(f"{count:4d}  {name}")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")
    models = load_models(data_dir)
    choices = available(models)
    if not choices:
        return _fail(f"no benchmark scores found in {data_dir} — run `fetch`")
    benchmark = args.benchmark or choices[0][0]
    if benchmark not in dict(choices):
        listing = ", ".join(f"{n} ({c})" for n, c in choices[:10])
        return _fail(
            f"unknown benchmark {benchmark!r} — available: {listing}"
        )
    out = Path(args.out)
    out.write_text(render_html(models, benchmark))
    print(f"wrote {out} ({benchmark}, {len(models)} models scanned)")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    saved = refresh(Path(args.data), get=http_get)
    print(f"saved {len(saved)} model files to {args.data}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="modelpareto",
        description="Price/score Pareto frontier of models from opencode.ai/data",
    )
    parser.add_argument("--data", default=str(DEFAULT_DATA),
                        help="data directory (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fetch", help="refresh data from opencode.ai")

    sub.add_parser("benchmarks", help="list benchmarks with model coverage")

    p_render = sub.add_parser("render", help="write the HTML report")
    p_render.add_argument("--benchmark",
                          help="benchmark to plot (default: best coverage)")
    p_render.add_argument("--out", default=str(DEFAULT_OUT),
                          help="output HTML path (default: %(default)s)")

    args = parser.parse_args(argv)
    return {
        "fetch": _cmd_fetch,
        "benchmarks": _cmd_benchmarks,
        "render": _cmd_render,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
