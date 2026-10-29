"""Minimal, polite HTTP client built on urllib (no third-party dependencies).

Features:
  * descriptive User-Agent on every request
  * conditional requests via ETag / If-Modified-Since (304 handling)
  * one retry for transient network/5xx failures, with backoff
  * explicit 429 handling: no retry storm, no bypass attempts
  * detection of interstitials that mean "stop, do not automate this"
"""

from __future__ import annotations

import gzip
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional, Sequence
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)

# Markers that indicate a bot challenge, queue, or interstitial rather than the
# real page. When one is found we stop for that source and report the limit.
BLOCK_MARKERS = (
    "cf-browser-verification",
    "cf-challenge",
    "just a moment...",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
    "queue-it",
    "queueclient",
    "are you a robot",
    "captcha",
    "access denied",
)


class FetchError(Exception):
    """Network or HTTP failure that is not an access block."""


class RateLimited(FetchError):
    """The server answered 429/503 with a retry hint; do not hammer it."""


class AccessBlocked(FetchError):
    """The source presented a queue, CAPTCHA, or anti-bot challenge."""


@dataclass
class FetchResult:
    url: str
    status: int
    body: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    not_modified: bool = False

    @property
    def content_type(self) -> str:
        return ""


class HttpClient:
    def __init__(
        self,
        user_agent: str,
        timeout: int = 30,
        retry_delay: int = 5,
        max_retries: int = 1,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retry_delay = retry_delay
        self.max_retries = max_retries

    # ------------------------------------------------------------------ helpers
    def _headers(
        self,
        etag: Optional[str],
        last_modified: Optional[str],
        accept: str,
    ) -> Dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.8",
            "Accept-Encoding": "gzip",
        }
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        return headers

    @staticmethod
    def _decode(raw: bytes, encoding: str, content_encoding: str) -> str:
        if content_encoding.lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                LOGGER.warning("could not decompress gzip response; using raw bytes")
        return raw.decode(encoding or "utf-8", errors="replace")

    def _request_once(
        self,
        url: str,
        etag: Optional[str],
        last_modified: Optional[str],
        accept: str,
    ) -> FetchResult:
        request = urllib.request.Request(
            url, headers=self._headers(etag, last_modified, accept), method="GET"
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            body = self._decode(
                raw, charset, response.headers.get("Content-Encoding", "")
            )
            return FetchResult(
                url=response.geturl(),
                status=response.status,
                body=body,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
            )

    # -------------------------------------------------------------------- public
    def get_text(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        detect_blocks: bool = True,
        content_markers: Sequence[str] = (),
    ) -> FetchResult:
        """Fetch *url*, returning a FetchResult or raising a FetchError subclass."""
        attempt = 0
        while True:
            try:
                result = self._request_once(url, etag, last_modified, accept)
            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    return FetchResult(url=url, status=304, body="", not_modified=True)
                if exc.code in (429, 503):
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    hint = f" (Retry-After: {retry_after})" if retry_after else ""
                    raise RateLimited(
                        f"{url} answered HTTP {exc.code}{hint}; "
                        "backing off instead of retrying aggressively"
                    ) from exc
                if exc.code in (401, 403):
                    raise AccessBlocked(
                        f"{url} answered HTTP {exc.code}: access is not permitted for "
                        "automated clients. Not attempting to bypass it."
                    ) from exc
                transient = exc.code >= 500
                if transient and attempt < self.max_retries:
                    attempt += 1
                    LOGGER.warning(
                        "HTTP %s from %s; retrying in %ss (attempt %s/%s)",
                        exc.code, url, self.retry_delay, attempt, self.max_retries,
                    )
                    time.sleep(self.retry_delay)
                    continue
                raise FetchError(f"{url} answered HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    attempt += 1
                    LOGGER.warning(
                        "network error for %s (%s); retrying in %ss (attempt %s/%s)",
                        url, exc, self.retry_delay, attempt, self.max_retries,
                    )
                    time.sleep(self.retry_delay)
                    continue
                raise FetchError(f"network error for {url}: {exc}") from exc

            if detect_blocks:
                self.raise_if_blocked(result.body, url, content_markers)
            return result

    @staticmethod
    def raise_if_blocked(
        body: str,
        url: str,
        content_markers: Sequence[str] = (),
    ) -> None:
        """Flag interstitials, but never a healthy page that merely embeds them.

        A normal storefront page can include Queue-it scripts as part of its own
        assets. Presence of expected content therefore wins over marker matching.
        """
        lowered = body.lower()
        if any(marker.lower() in lowered for marker in content_markers):
            return
        for marker in BLOCK_MARKERS:
            if marker in lowered:
                raise AccessBlocked(
                    f"{url} returned an interstitial that looks like a queue, CAPTCHA, "
                    f"or anti-bot challenge (matched {marker!r}). "
                    "Stopping for this cycle rather than attempting to bypass it."
                )

    @staticmethod
    def host_of(url: str) -> str:
        return urlparse(url).netloc.lower()
