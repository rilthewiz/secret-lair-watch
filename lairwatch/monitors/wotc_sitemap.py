"""Monitor for the official Wizards of the Coast sitemap.

Verified 2026-10: https://magic.wizards.com/en/sitemap.xml is advertised in
robots.txt, returns public XML with <loc> + <lastmod>, and answers conditional
requests with an ETag. It currently contains ~97 Secret Lair announcement URLs,
so a new URL is a reliable "new official announcement" signal. The Magic news
pages themselves expose no RSS/Atom feed (checked), so we do not pretend one
exists.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Optional

from ..http_client import HttpClient
from .base import Item, Monitor, ParseError

LOGGER = logging.getLogger(__name__)


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _pretty_title(url: str) -> str:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    if not slug:
        return url
    words = re.split(r"[-_]+", slug)
    titled = []
    for word in words:
        if not word:
            continue
        if word.isupper() and len(word) <= 4:
            titled.append(word)
        else:
            titled.append(word[:1].upper() + word[1:])
    return " ".join(titled)


class WotcSitemapMonitor(Monitor):
    type_name = "wotc_sitemap"

    def _pattern(self) -> Optional[re.Pattern]:
        pattern = self.config.url_pattern
        if not pattern:
            return None
        try:
            return re.compile(pattern)
        except re.error as exc:
            raise ParseError(f"invalid url_pattern {pattern!r}: {exc}") from exc

    def fetch_items(self, client: HttpClient, body: str) -> List[Item]:
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise ParseError(f"sitemap is not valid XML: {exc}") from exc

        pattern = self._pattern()
        urls: List[Item] = []
        seen = set()
        total = 0

        for element in root.iter():
            if _localname(element.tag) != "url":
                continue
            total += 1
            loc = ""
            lastmod = ""
            for child in element:
                name = _localname(child.tag)
                if name == "loc" and child.text:
                    loc = child.text.strip()
                elif name == "lastmod" and child.text:
                    lastmod = child.text.strip()

            if not loc or loc in seen:
                continue
            if pattern is not None and not pattern.search(loc):
                continue

            seen.add(loc)
            status = f"published {lastmod[:10]}" if lastmod else "published"
            if lastmod:
                try:
                    parsed = datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
                    age_days = (datetime.now(timezone.utc) - parsed).days
                    if age_days > 3650:
                        LOGGER.debug("suspiciously old lastmod for %s", loc)
                except ValueError:
                    LOGGER.debug("unparseable lastmod %r for %s", lastmod, loc)
            urls.append(
                Item(key=loc, title=_pretty_title(loc), url=loc, status=status)
            )

        if total == 0:
            raise ParseError(
                "sitemap contained no <url> entries; the document format may have changed"
            )
        if not urls:
            LOGGER.warning(
                "sitemap parsed (%s URLs) but none matched url_pattern %r",
                total, getattr(self.config, "url_pattern", None),
            )
        else:
            LOGGER.info(
                "sitemap parsed: %s matching URL(s) of %s total", len(urls), total
            )
        return urls
