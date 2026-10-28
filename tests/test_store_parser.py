"""Tests for the official storefront parser (synthetic fixture, no network)."""

from __future__ import annotations

import unittest

from lairwatch.config import SourceConfig
from lairwatch.monitors.base import ParseError
from lairwatch.monitors.secret_lair_store import (
    SecretLairStoreMonitor,
    extract_json_array,
)

from helpers import fixture

STORE_URL = "https://secretlair.wizards.com/us"


def make_monitor() -> SecretLairStoreMonitor:
    return SecretLairStoreMonitor(
        SourceConfig(name="store", type="secret_lair_store", url=STORE_URL)
    )


class ExtractJsonArrayTests(unittest.TestCase):
    def test_handles_brackets_and_braces_inside_strings(self) -> None:
        body = 'x = [{"title":"a ] b","nested":[1,2]},{"title":"c"}]  trailing'
        raw = extract_json_array(body, "x =")
        self.assertIsNotNone(raw)
        self.assertTrue(raw.endswith("}]"))

    def test_returns_none_when_marker_missing(self) -> None:
        self.assertIsNone(extract_json_array("<html></html>", "upcoming_event_slides"))

    def test_returns_none_when_unbalanced(self) -> None:
        self.assertIsNone(
            extract_json_array('upcoming_event_slides = [{"title":"a"', "upcoming_event_slides")
        )


class StoreParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.monitor = make_monitor()

    def test_parses_visible_slides_and_skips_hidden(self) -> None:
        items = self.monitor.fetch_items(None, fixture("store_page.html"))  # type: ignore[arg-type]
        self.assertEqual([item.key for item in items], ["doomsdrop", "set-phasers"])
        self.assertTrue(all(item.status == "Coming Soon" for item in items))
        self.assertTrue(items[0].url.endswith("#doomsdrop"))

    def test_tolerates_trailing_comma_in_page_json(self) -> None:
        # The fixture intentionally contains a trailing comma after anchor_link.
        items = self.monitor.fetch_items(None, fixture("store_page.html"))  # type: ignore[arg-type]
        self.assertEqual(len(items), 2)

    def test_html_fallback_when_slides_missing(self) -> None:
        body = (
            '<p class="notify-me-title">Alpha Drop</p>'
            '<p class="notify-me-subtitle">On Sale Now</p>'
        )
        items = self.monitor.fetch_items(None, body)  # type: ignore[arg-type]
        self.assertEqual([item.key for item in items], ["alpha-drop"])
        self.assertEqual(items[0].status, "On Sale Now")

    def test_duplicate_titles_are_collapsed(self) -> None:
        body = (
            '<script>upcoming_event_slides = '
            '[{"title":"Same Drop","subtitle":"Coming Soon","anchor_link":""},'
            '{"title":"Same Drop","subtitle":"Coming Soon","anchor_link":""}];</script>'
        )
        items = self.monitor.fetch_items(None, body)  # type: ignore[arg-type]
        self.assertEqual(len(items), 1)

    def test_uses_richest_payload_when_marker_repeats(self) -> None:
        # Regression: the real page assigns upcoming_event_slides more than once,
        # starting with an empty initialiser.
        body = (
            "<script>$scope.upcoming_event_slides = [];"
            "$scope.upcoming_event_slides = "
            '[{"visible":true,"title":"Alpha","subtitle":"Coming Soon",'
            '"anchor_link":"alpha"},'
            '{"visible":true,"title":"Beta","subtitle":"Coming Soon",'
            '"anchor_link":"beta"}];</script>'
        )
        items = self.monitor.fetch_items(None, body)  # type: ignore[arg-type]
        self.assertEqual([item.key for item in items], ["alpha", "beta"])

    def test_raises_parse_error_when_page_has_no_drop_data(self) -> None:
        with self.assertRaises(ParseError):
            self.monitor.fetch_items(None, "<html><body>maintenance</body></html>")  # type: ignore[arg-type]

    def test_slug_is_stable_for_status_updates(self) -> None:
        first = self.monitor.fetch_items(  # type: ignore[arg-type]
            None,
            '<script>upcoming_event_slides = [{"title":"Doomsdrop",'
            '"subtitle":"Coming Soon","anchor_link":"doomsdrop"}];</script>',
        )
        second = self.monitor.fetch_items(  # type: ignore[arg-type]
            None,
            '<script>upcoming_event_slides = [{"title":"Doomsdrop",'
            '"subtitle":"On Sale Now","anchor_link":"doomsdrop"}];</script>',
        )
        self.assertEqual(first[0].key, second[0].key)
        self.assertNotEqual(first[0].status, second[0].status)


if __name__ == "__main__":
    unittest.main()
