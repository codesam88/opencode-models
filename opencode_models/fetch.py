"""Refresh the data directory from opencode.ai.

The site publishes one page per model and serves its structured data at the
same URL with a ``.json`` suffix; the sitemap at SITEMAP_URL lists every
page. HTTP access is an injected ``get(url) -> str`` callable so tests can
exercise the full flow offline (see tests/test_fetch.py).
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

from . import __version__
from .availability import offered

SITEMAP_URL = "https://opencode.ai/data/sitemap.xml"
_BASE = "https://opencode.ai/data/"
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def http_get(url: str) -> str:
    """Real transport: plain GET with a browser-ish user agent.

    Failed responses are closed before the error propagates: urlopen
    leaves the error response open, and dropping it unclosed warns at
    GC time. No caller reads error bodies, so nothing is lost.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": f"opencode-models/{__version__}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        exc.close()
        raise


def urls_from_sitemap(xml: str) -> list[str]:
    """Model-page URLs from sitemap XML, skipping the root and lab indexes."""
    root = ElementTree.fromstring(xml)
    urls = []
    for loc in root.iter(f"{_SITEMAP_NS}loc"):
        text = (loc.text or "").strip()
        # Only <scheme>://opencode.ai/data/<lab>/<model> — lab indexes and
        # other paths have fewer segments or a different host.
        if text.startswith(_BASE) and text.count("/") == 5:
            urls.append(text)
    return urls


def _filename_for(url: str) -> str:
    """``https://opencode.ai/data/<lab>/<model>`` → ``<lab>__<model>.json``."""
    lab, model = url[len(_BASE) :].rsplit("/", 1)
    return f"{lab}__{model}.json"


def refresh(
    data_dir: Path, get=http_get, progress=None, only: set[str] | None = None
) -> list[str]:
    """Download every model's JSON into *data_dir*/opencode; return names.

    The upstream layer is verbatim: ``fetch`` writes only here, and the
    curated overlays in ``data/overrides/`` are layered on at load time
    (see overrides.py). A failed or empty download for one model is
    skipped, not fatal — the rest of the dataset still refreshes.

    *progress* is an optional ``(done, total, name)`` callback, fired
    after every model URL is attempted (saved or skipped), so a caller
    can show the long sequential walk instead of silence.

    *only* restricts downloads to the models the plans offer (slugs or
    ids — ``-free`` variants reaching their model, as elsewhere). Files
    already on disk are never deleted: a model the plans dropped keeps
    its record, so the scales it anchors do not move under the scores.
    None downloads everything.
    """
    out = Path(data_dir) / "opencode"
    out.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    try:
        urls = urls_from_sitemap(get(SITEMAP_URL))
    except (OSError, ValueError, SyntaxError):
        return saved
    if only is not None:
        urls = [u for u in urls if offered(u[len(_BASE) :], only)]
    total = len(urls)
    for done, url in enumerate(urls, 1):
        try:
            body = get(f"{url}.json")
        except (urllib.error.URLError, OSError):
            body = ""
        if body.strip():
            name = _filename_for(url)
            (out / name).write_text(body)
            saved.append(name)
        else:
            name = _filename_for(url)
        if progress is not None:
            progress(done, total, name)
    return saved
