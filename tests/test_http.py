"""HTTP client behaviour tests (no sockets opened)."""

from __future__ import annotations

import io
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from lairwatch.http_client import (
    AccessBlocked,
    FetchError,
    FetchResult,
    HttpClient,
    RateLimited,
)

URL = "https://magic.wizards.com/en/sitemap.xml"


def client() -> HttpClient:
    return HttpClient(user_agent="secret-lair-watch/0.1 (test)", timeout=5, retry_delay=0)


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(URL, code, "error", headers, io.BytesIO(b""))


class HeaderTests(unittest.TestCase):
    def test_identifies_itself_and_uses_conditional_headers(self) -> None:
        headers = client()._headers('"abc"', "Wed, 21 Oct 2026 07:28:00 GMT", "text/html")
        self.assertEqual(headers["User-Agent"], "secret-lair-watch/0.1 (test)")
        self.assertEqual(headers["If-None-Match"], '"abc"')
        self.assertIn("If-Modified-Since", headers)


class FetchBehaviourTests(unittest.TestCase):
    def test_304_is_reported_as_not_modified(self) -> None:
        http = client()
        with mock.patch.object(http, "_request_once", side_effect=http_error(304)):
            result = http.get_text(URL)
        self.assertTrue(result.not_modified)
        self.assertEqual(result.status, 304)
        self.assertEqual(result.body, "")

    def test_403_is_an_access_block(self) -> None:
        http = client()
        with mock.patch.object(http, "_request_once", side_effect=http_error(403)):
            with self.assertRaises(AccessBlocked):
                http.get_text(URL)

    def test_429_reports_retry_after_without_retrying(self) -> None:
        http = client()
        with mock.patch.object(http, "_request_once", side_effect=http_error(429, "120")):
            with self.assertRaises(RateLimited) as raised:
                http.get_text(URL)
        self.assertIn("120", str(raised.exception))

    def test_5xx_is_retried_once_then_raises(self) -> None:
        http = client()
        with mock.patch.object(
            http, "_request_once", side_effect=http_error(500)
        ) as patched:
            with self.assertRaises(FetchError):
                http.get_text(URL)
        self.assertEqual(patched.call_count, 2)

    def test_network_error_retried_once(self) -> None:
        http = client()
        with mock.patch.object(
            http, "_request_once", side_effect=urllib.error.URLError("down")
        ) as patched:
            with self.assertRaises(FetchError):
                http.get_text(URL)
        self.assertEqual(patched.call_count, 2)

    def test_successful_response_returned(self) -> None:
        http = client()
        with mock.patch.object(
            http,
            "_request_once",
            return_value=FetchResult(url=URL, status=200, body="<xml/>", etag='"1"'),
        ):
            result = http.get_text(URL)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.body, "<xml/>")
        self.assertEqual(result.etag, '"1"')


class BlockDetectionTests(unittest.TestCase):
    def test_queue_interstitial_is_detected(self) -> None:
        with self.assertRaises(AccessBlocked) as raised:
            HttpClient.raise_if_blocked(
                "<html><script src='//static.queue-it.net/script/queueclient.min.js'>"
                "</script></html>",
                URL,
            )
        self.assertIn("queue", str(raised.exception))

    def test_captcha_marker_detected_case_insensitively(self) -> None:
        with self.assertRaises(AccessBlocked):
            HttpClient.raise_if_blocked("<div>Please complete the CAPTCHA</div>", URL)

    def test_cloudflare_challenge_detected(self) -> None:
        with self.assertRaises(AccessBlocked):
            HttpClient.raise_if_blocked("<title>Just a moment...</title>", URL)

    def test_normal_page_is_not_flagged(self) -> None:
        HttpClient.raise_if_blocked(
            "<html><title>Secret Lair</title><p>Coming Soon</p></html>", URL
        )

    def test_healthy_page_embedding_queue_assets_is_not_flagged(self) -> None:
        # Regression: the real storefront always includes Queue-it scripts.
        healthy = (
            "<html><script src='//static.queue-it.net/script/queueclient.min.js'>"
            "</script><script>upcoming_event_slides = [];</script></html>"
        )
        HttpClient.raise_if_blocked(
            healthy, URL, content_markers=("upcoming_event_slides", "notify-me-title")
        )

    def test_queue_only_interstitial_is_still_flagged(self) -> None:
        interstitial = (
            "<html><title>You are now in line</title>"
            "<script src='//static.queue-it.net/script/queueclient.min.js'></script>"
            "</html>"
        )
        with self.assertRaises(AccessBlocked):
            HttpClient.raise_if_blocked(
                interstitial, URL, content_markers=("upcoming_event_slides",)
            )

    def test_get_text_passes_content_markers_through(self) -> None:
        http = client()
        healthy = "<html>queue-it<script>upcoming_event_slides = [];</script></html>"
        with mock.patch.object(
            http,
            "_request_once",
            return_value=FetchResult(url=URL, status=200, body=healthy),
        ):
            result = http.get_text(
                URL, content_markers=("upcoming_event_slides", "notify-me-title")
            )
        self.assertEqual(result.status, 200)

    def test_host_of(self) -> None:
        self.assertEqual(
            HttpClient.host_of("https://secretlair.wizards.com/us"), "secretlair.wizards.com"
        )


if __name__ == "__main__":
    unittest.main()
