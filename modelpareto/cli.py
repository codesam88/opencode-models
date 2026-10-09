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
from .ranking import by_score_per_price
from .render import render_html
from . import sources

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


def _cmd_list(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"no data directory at {data_dir} — run `fetch` first")
    try:
        slugs = sources.load_source(args.source)
    except KeyError:
        if args.source != "all":
            return _fail(
                f"unknown source {args.source!r} — available: "
                f"{', '.join(sources.available_sources())}, all"
            )
        slugs = None  # all = no tier filter
    models = load_models(data_dir)
    in_tier = models if slugs is None else [
        m for m in models
        if m.model_id.split("/")[-1] in slugs or m.model_id in slugs
    ]
    choices = dict(available(in_tier))
    benchmark = args.benchmark or (max(choices, key=choices.get) if choices else None)
    if benchmark is None:
        return _fail(f"no {args.source} model in {data_dir} has benchmark scores")
    if benchmark not in choices:
        listing = ", ".join(f"{n} ({c})" for n, c in list(available(in_tier))[:10])
        return _fail(f"unknown benchmark {benchmark!r} — available: {listing}")
    ranked = by_score_per_price(in_tier, benchmark)
    if slugs is None:
        print(f"all: {len(models)} models in catalog · "
              f"benchmark {benchmark} · sorted by score per $ input")
    else:
        print(f"{args.source}: {len(in_tier)}/{len(slugs)} models in catalog · "
              f"benchmark {benchmark} · sorted by score per $ input")
    print(f"{'score':>6}  {'$/1M':>7}  {'score/$':>8}  model")
    for m in ranked:
        score = m.scores[benchmark]
        ratio = "free" if m.price_input == 0 else f"{score / m.price_input:.1f}"
        price = "0" if m.price_input == 0 else f"{m.price_input:g}"
        print(f"{score:>6g}  {price:>7}  {ratio:>8}  {m.name}")
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

    p_list = sub.add_parser(
        "list", help="text table of a Console tier (zen/go), best value first"
    )
    p_list.add_argument("--source", required=True,
                        help="tier to list (zen or go)")
    p_list.add_argument("--benchmark",
                        help="benchmark to rank on (default: best coverage "
                             "within the tier)")

    args = parser.parse_args(argv)
    return {
        "fetch": _cmd_fetch,
        "benchmarks": _cmd_benchmarks,
        "render": _cmd_render,
        "list": _cmd_list,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
