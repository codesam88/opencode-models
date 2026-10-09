"""Refresh the data directory from opencode.ai without hitting the network
in tests: HTTP access is an injected callable."""

import io
import unittest
import urllib.error
from pathlib import Path
import tempfile

from modelpareto.fetch import SITEMAP_URL, refresh, urls_from_sitemap


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
                raise urllib.error.HTTPError(url, 404, "gone", {}, io.BytesIO())
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


if __name__ == "__main__":
    unittest.main()
