"""Refresh the data directory from opencode.ai without hitting the network
in tests: HTTP access is an injected callable."""

import io
import tempfile
import unittest
import urllib.error
from pathlib import Path

from opencode_models.fetch import SITEMAP_URL, refresh, urls_from_sitemap

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://opencode.ai/data/</loc></url>
  <url><loc>https://opencode.ai/data/ai21</loc></url>
  <url><loc>https://opencode.ai/data/ai21/jamba-large</loc></url>
  <url><loc>https://opencode.ai/data/deepseek/deepseek-v4-pro</loc></url>
</urlset>
"""


class SitemapParseTest(unittest.TestCase):
    def test_model_pages_only(self):
        # Root page and lab index have a single path segment after /data/;
        # real models have lab/name.
        urls = urls_from_sitemap(SITEMAP_XML)
        self.assertEqual(
            urls,
            [
                "https://opencode.ai/data/ai21/jamba-large",
                "https://opencode.ai/data/deepseek/deepseek-v4-pro",
            ],
        )

    def test_ignores_sub_sitemaps_and_other_hosts(self):
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://opencode.ai/sitemap.xml</loc></url>
          <url><loc>https://example.com/data/a/b</loc></url>
          <url><loc>https://opencode.ai/data/lab/model</loc></url>
        </urlset>"""
        self.assertEqual(urls_from_sitemap(xml), ["https://opencode.ai/data/lab/model"])


class RefreshTest(unittest.TestCase):
    def _fetcher(self, responses):
        def get(url):
            if url not in responses:
                raise KeyError(url)
            return responses[url]

        return get

    def test_saves_one_json_file_per_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            get = self._fetcher(
                {
                    SITEMAP_URL: SITEMAP_XML,
                    "https://opencode.ai/data/ai21/jamba-large.json": '{"model":{"id":"ai21/jamba-large"}}',
                    "https://opencode.ai/data/deepseek/deepseek-v4-pro.json": '{"model":{"id":"deepseek/deepseek-v4-pro"}}',
                }
            )
            saved = refresh(data_dir, get)
        self.assertEqual(len(saved), 2)
        self.assertEqual(
            sorted(saved),
            ["ai21__jamba-large.json", "deepseek__deepseek-v4-pro.json"],
        )

    def test_file_content_is_the_response_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            get = self._fetcher(
                {
                    SITEMAP_URL: SITEMAP_XML,
                    "https://opencode.ai/data/ai21/jamba-large.json": '{"a":1}',
                    "https://opencode.ai/data/deepseek/deepseek-v4-pro.json": "{}",
                }
            )
            saved = refresh(Path(tmp), get)
            body = (Path(tmp) / "opencode" / "ai21__jamba-large.json").read_text()
        self.assertEqual(body, '{"a":1}')
        self.assertIn("ai21__jamba-large.json", saved)

    def test_model_filename_uses_double_underscore_separator(self):
        with tempfile.TemporaryDirectory() as tmp:
            get = self._fetcher(
                {
                    SITEMAP_URL: SITEMAP_XML,
                    "https://opencode.ai/data/ai21/jamba-large.json": "{}",
                    "https://opencode.ai/data/deepseek/deepseek-v4-pro.json": "{}",
                }
            )
            saved = refresh(Path(tmp), get)
        self.assertNotIn("ai21/jamba-large.json", saved)

    def test_failed_model_download_is_skipped_not_fatal(self):
        def get(url):
            if url == SITEMAP_URL:
                return SITEMAP_XML
            if "jamba" in url:
                # a raised HTTPError the transport never closed would warn
                # at GC time, mirroring the real-transport contract
                err = urllib.error.HTTPError(url, 404, "gone", {}, io.BytesIO())
                err.close()
                raise err
            return "{}"

        with tempfile.TemporaryDirectory() as tmp:
            saved = refresh(Path(tmp), get)
        self.assertEqual(saved, ["deepseek__deepseek-v4-pro.json"])

    def test_empty_body_is_not_saved(self):
        def get(url):
            if url == SITEMAP_URL:
                return SITEMAP_XML
            if "jamba" in url:
                return ""
            return "{}"

        with tempfile.TemporaryDirectory() as tmp:
            saved = refresh(Path(tmp), get)
        self.assertEqual(saved, ["deepseek__deepseek-v4-pro.json"])

    def test_progress_callback_reports_each_model(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            get = self._fetcher(
                {
                    SITEMAP_URL: SITEMAP_XML,
                    "https://opencode.ai/data/ai21/jamba-large.json": '{"a":1}',
                    "https://opencode.ai/data/deepseek/deepseek-v4-pro.json": "{}",
                }
            )
            refresh(Path(tmp), get, progress=lambda d, t, n: calls.append((d, t, n)))
        self.assertEqual(
            calls,
            [
                (1, 2, "ai21__jamba-large.json"),
                (2, 2, "deepseek__deepseek-v4-pro.json"),
            ],
        )

    def test_sitemap_failure_returns_empty_and_keeps_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            out = data_dir / "opencode"
            out.mkdir(parents=True)
            kept = out / "old.json"
            kept.write_text('{"old":true}')

            def get(url):
                if url == SITEMAP_URL:
                    raise OSError("sitemap down")
                return "{}"

            saved = refresh(data_dir, get)
            self.assertEqual(saved, [])
            self.assertEqual(kept.read_text(), '{"old":true}')


if __name__ == "__main__":
    unittest.main()


class OnlyFilterTest(unittest.TestCase):
    def _get(self, responses):
        def get(url):
            if url not in responses:
                raise KeyError(url)
            return responses[url]

        return get

    def test_only_downloads_the_offered_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            get = self._get(
                {
                    SITEMAP_URL: SITEMAP_XML,
                    "https://opencode.ai/data/ai21/jamba-large.json": "{}",
                    "https://opencode.ai/data/deepseek/deepseek-v4-pro.json": "{}",
                }
            )
            saved = refresh(Path(tmp), get, only={"deepseek-v4-pro"})
        self.assertEqual(saved, ["deepseek__deepseek-v4-pro.json"])

    def test_only_reaches_a_model_through_its_free_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            get = self._get(
                {
                    SITEMAP_URL: SITEMAP_XML,
                    "https://opencode.ai/data/ai21/jamba-large.json": "{}",
                    "https://opencode.ai/data/deepseek/deepseek-v4-pro.json": "{}",
                }
            )
            saved = refresh(Path(tmp), get, only={"jamba-large-free"})
        self.assertEqual(saved, ["ai21__jamba-large.json"])

    def test_what_is_on_disk_is_kept_whatever_the_filter_says(self):
        with tempfile.TemporaryDirectory() as tmp:
            kept = Path(tmp) / "opencode" / "ai21__jamba-large.json"
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_text('{"old":true}')
            get = self._get(
                {
                    SITEMAP_URL: SITEMAP_XML,
                    "https://opencode.ai/data/deepseek/deepseek-v4-pro.json": "{}",
                }
            )
            saved = refresh(Path(tmp), get, only={"deepseek-v4-pro"})
            self.assertEqual(saved, ["deepseek__deepseek-v4-pro.json"])
            self.assertEqual(kept.read_text(), '{"old":true}')


class TransportHygieneTest(unittest.TestCase):
    def test_http_get_closes_failed_responses(self):
        import gc
        import urllib.request
        import warnings
        from unittest import mock

        from opencode_models.fetch import http_get

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", {}, io.BytesIO()
            )

        err = None
        with mock.patch.object(urllib.request, "urlopen", boom):
            try:
                http_get("https://example.invalid/x")
            except urllib.error.HTTPError as caught:
                err = caught
            else:
                self.fail("expected HTTPError")
        self.assertIsNotNone(err)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            del err
            gc.collect()
        leaked = [w for w in caught if issubclass(w.category, ResourceWarning)]
        self.assertEqual(leaked, [])

    def test_user_agent_carries_the_package_version(self):
        import urllib.request
        from importlib.metadata import version as pkg_version
        from unittest import mock

        from opencode_models.fetch import http_get

        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["req"] = req
            raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, io.BytesIO())

        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(urllib.error.HTTPError):
                http_get("https://example.invalid/x")
        ua = next(v for k, v in seen["req"].header_items() if k.lower() == "user-agent")
        self.assertEqual(ua, f"opencode-models/{pkg_version('opencode-models')}")
