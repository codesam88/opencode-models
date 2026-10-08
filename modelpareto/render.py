"""Render the Pareto frontier as one self-contained HTML file.

Everything is inlined (CSS in a <style> tag, chart as raw <svg>) so the
output opens from disk with no network and no build step. The chart uses a
log-scaled price axis because model prices span free to tens of dollars per
1M tokens; a linear axis would crush the interesting low end into a corner.
"""

from __future__ import annotations

import html as html_mod
import math

from .data import Model
from .pareto import frontier

_STYLE = """
:root { color-scheme: light dark; }
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 60rem;
       padding: 0 1rem; line-height: 1.5; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { text-align: left; padding: .4rem .6rem;
         border-bottom: 1px solid color-mix(in srgb, currentColor 20%, transparent); }
th { cursor: default; }
svg text { font-size: 11px; }
.pareto-stroke { stroke: currentColor; stroke-opacity: .5; fill: none; }
.dot { fill: color-mix(in srgb, currentColor 30%, transparent); }
.dot-frontier { fill: currentColor; }
.note { opacity: .7; font-size: .9rem; }
"""

# Chart geometry (viewBox coordinates).
_W, _H = 640, 400
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 56, 16, 16, 40


def _fmt_price(p: float) -> str:
    return f"${p:g}"


def _scatter_svg(models: list[Model], benchmark: str) -> str:
    """Inline SVG scatter of score vs log10(input price), frontier connected."""
    if not models:
        return ""
    prices = [m.price_input for m in models]
    scores = [m.scores[benchmark] for m in models]
    # Log10 domain; free (0) models clamp to the axis floor.
    floor = min(p for p in prices if p > 0) / 2 if any(p > 0 for p in prices) else 1
    logs = [math.log10(max(p, floor)) for p in prices]
    lo, hi = min(logs), max(logs)
    span = (hi - lo) or 1.0
    s_lo, s_hi = min(scores), max(scores)
    s_span = (s_hi - s_lo) or 1.0

    def x(p: float, lg: float) -> float:
        return _PAD_L + (lg - lo) / span * (_W - _PAD_L - _PAD_R)

    def y(s: float) -> float:
        return _PAD_T + (1 - (s - s_lo) / s_span) * (_H - _PAD_T - _PAD_B)

    parts = [f'<svg viewBox="0 0 {_W} {_H}" role="img" '
             f'aria-label="score vs price scatter">']
    # Axis ticks: price at decade steps, score at lo/mid/hi.
    dec = math.ceil(lo)
    while dec <= math.floor(hi) + 1e-9:
        px = x(0, dec)
        if _PAD_L <= px <= _W - _PAD_R:
            parts.append(
                f'<line x1="{px:.1f}" y1="{_PAD_T}" x2="{px:.1f}" '
                f'y2="{_H - _PAD_B}" stroke="currentColor" stroke-opacity=".12"/>'
                f'<text x="{px:.1f}" y="{_H - _PAD_B + 14}" '
                f'text-anchor="middle">${10 ** dec:g}</text>'
            )
        dec += 1
    for s in (s_lo, (s_lo + s_hi) / 2, s_hi):
        py = y(s)
        parts.append(
            f'<text x="{_PAD_L - 6}" y="{py + 3:.1f}" '
            f'text-anchor="end">{s:g}</text>'
        )
    # Frontier polyline (models are price-sorted already).
    pts = " ".join(
        f"{x(m.price_input, math.log10(max(m.price_input, floor))):.1f},"
        f"{y(m.scores[benchmark]):.1f}"
        for m in models
    )
    parts.append(f'<polyline class="pareto-stroke" points="{pts}"/>')
    for m in models:
        cx = x(m.price_input, math.log10(max(m.price_input, floor)))
        cy = y(m.scores[benchmark])
        parts.append(
            f'<circle class="dot-frontier" cx="{cx:.1f}" cy="{cy:.1f}" r="4">'
            f"<title>{html_mod.escape(m.name)} — {_fmt_price(m.price_input)}, "
            f"{m.scores[benchmark]:g}</title></circle>"
        )
    parts.append(f'<text x="{_W / 2}" y="{_H - 6}" text-anchor="middle">'
                 "input price / 1M tokens (log scale)</text>")
    parts.append(f'<text x="12" y="{_H / 2}" text-anchor="middle" '
                 f'transform="rotate(-90 12 {_H / 2})">score</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def render_html(models: list[Model], benchmark: str) -> str:
    """Build the full report: heading, scatter, frontier table."""
    rows = frontier(models, benchmark)
    esc = html_mod.escape
    if rows:
        body = [
            f"<h1>{esc(benchmark)} — price/score Pareto frontier</h1>",
            f'<p class="note">{len(rows)} of {len(models)} models are on the '
            "frontier: no other model is both cheaper and better on this "
            "benchmark. Input price per 1M tokens (log scale).</p>",
            _scatter_svg(rows, benchmark),
            "<table><thead><tr><th>#</th><th>Model</th><th>Lab</th>"
            "<th>Score</th><th>Input $/1M</th></tr></thead><tbody>",
        ]
        for i, m in enumerate(rows, 1):
            body.append(
                f"<tr><td>{i}</td><td>{esc(m.name)}</td><td>{esc(m.lab)}</td>"
                f"<td>{m.scores[benchmark]:g}</td>"
                f"<td>{_fmt_price(m.price_input)}</td></tr>"
            )
        body.append("</tbody></table>")
    else:
        body = [
            f"<h1>{esc(benchmark)} — price/score Pareto frontier</h1>",
            f'<p class="note">no models in the dataset carry both a price and '
            f"a {esc(benchmark)} score — nothing to plot.</p>",
        ]
    return (
        "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{esc(benchmark)} Pareto</title><style>{_STYLE}</style></head>"
        f"<body>{''.join(body)}</body></html>\n"
    )
