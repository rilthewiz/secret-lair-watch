"""Tests for the official sitemap parser (synthetic fixture, no network)."""

from __future__ import annotations

import unittest

from lairwatch.config import SourceConfig
from lairwatch.monitors.base import ParseError
from lairwatch.monitors.wotc_sitemap import WotcSitemapMonitor

from helpers import fixture

SITEMAP_URL = "https://magic.wizards.com/en/sitemap.xml"


def make_monitor(pattern: str = r"/en/news/.*secret-lair") -> WotcSitemapMonitor:
    return WotcSitemapMonitor(
        SourceConfig(
            name="news",
            type="wotc_sitemap",
            url=SITEMAP_URL,
            url_pattern=pattern,
        )
    )


class SitemapParserTests(unittest.TestCase):
    def test_filters_to_secret_lair_announcements(self) -> None:
        items = make_monitor().fetch_items(None, fixture("sitemap.xml"))  # type: ignore[arg-type]
        self.assertEqual(len(items), 2)
        self.assertTrue(all("secret-lair" in item.url for item in items))
        self.assertIn("set-phasers", items[0].url)

    def test_lastmod_becomes_reported_status(self) -> None:
        items = make_monitor().fetch_items(None, fixture("sitemap.xml"))  # type: ignore[arg-type]
        self.assertEqual(items[0].status, "published 2026-10-27")

    def test_title_derived_from_url_slug(self) -> None:
        items = make_monitor().fetch_items(None, fixture("sitemap.xml"))  # type: ignore[arg-type]
        self.assertEqual(items[0].title, "Secret Lair Set Phasers To Superdrop")

    def test_empty_pattern_keeps_every_entry(self) -> None:
        items = WotcSitemapMonitor(
            SourceConfig(name="all", type="wotc_sitemap", url=SITEMAP_URL, url_pattern=None)
        ).fetch_items(None, fixture("sitemap.xml"))  # type: ignore[arg-type]
        self.assertEqual(len(items), 4)

    def test_duplicate_urls_collapsed(self) -> None:
        body = (
            '<urlset><url><loc>https://x.test/a-secret-lair</loc></url>'
            '<url><loc>https://x.test/a-secret-lair</loc></url></urlset>'
        )
        items = make_monitor("secret-lair").fetch_items(None, body)  # type: ignore[arg-type]
        self.assertEqual(len(items), 1)

    def test_invalid_xml_raises_parse_error(self) -> None:
        with self.assertRaises(ParseError):
            make_monitor().fetch_items(None, "<urlset><url>")  # type: ignore[arg-type]

    def test_empty_urlset_raises_parse_error(self) -> None:
        with self.assertRaises(ParseError):
            make_monitor().fetch_items(None, "<urlset></urlset>")  # type: ignore[arg-type]

    def test_pattern_with_no_matches_returns_empty_list(self) -> None:
        items = make_monitor(r"/en/news/.*nonexistent-topic").fetch_items(  # type: ignore[arg-type]
            None, fixture("sitemap.xml")
        )
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
