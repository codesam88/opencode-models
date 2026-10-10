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

SITEMAP_URL = "https://opencode.ai/data/sitemap.xml"
_BASE = "https://opencode.ai/data/"
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def http_get(url: str) -> str:
    """Real transport: plain GET with a browser-ish user agent."""
    req = urllib.request.Request(url, headers={"User-Agent": "opencode-rank/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


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
    lab, model = url[len(_BASE):].rsplit("/", 1)
    return f"{lab}__{model}.json"


def refresh(data_dir: Path, get=http_get) -> list[str]:
    """Download every model's JSON into *data_dir*/opencode; return names.

    The upstream layer is verbatim: ``fetch`` writes only here, and the
    curated overlays in ``data/overrides/`` are layered on at load time
    (see overrides.py). A failed or empty download for one model is
    skipped, not fatal — the rest of the dataset still refreshes.
    """
    out = Path(data_dir) / "opencode"
    out.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for url in urls_from_sitemap(get(SITEMAP_URL)):
        try:
            body = get(f"{url}.json")
        except (urllib.error.URLError, OSError):
            continue
        if not body.strip():
            continue
        name = _filename_for(url)
        (out / name).write_text(body)
        saved.append(name)
    return saved
