"""Monitor for the official Secret Lair storefront.

Verified structure (2026-10): https://secretlair.wizards.com/us is server
rendered and embeds an AngularJS controller payload:

    $scope.upcoming_event_slides = [ { "title": "Doomsdrop",
                                       "subtitle": "Coming Soon",
                                       "anchor_link": "doomsdrop", ... }, ... ];

We read the public titles/subtitles only. No account, cart, checkout, or queue
interaction happens anywhere in this module.
"""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ..http_client import HttpClient
from .base import Item, Monitor, ParseError

LOGGER = logging.getLogger(__name__)

SLIDES_MARKER = "upcoming_event_slides"
FALLBACK_TITLE_RE = re.compile(
    r'<p class="notify-me-title">\s*(?P<title>.*?)\s*</p>.*?'
    r'<p class="notify-me-subtitle">\s*(?P<status>.*?)\s*</p>',
    re.IGNORECASE | re.DOTALL,
)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unnamed"


def _aliases(anchor: str, title: str, event_name: str) -> Tuple[str, ...]:
    """Stable alternative identifiers for one drop.

    Wizards sometimes edits a title (``Doomsdrop`` -> ``Doomsdrop II``) or the
    anchor, but rarely both at once. Recording both means a rename is recognised
    as the same drop instead of arriving as a brand-new alert.
    """
    values = {
        _slugify(anchor) if anchor else "",
        _slugify(title) if title else "",
        _slugify(event_name) if event_name else "",
    }
    return tuple(sorted(value for value in values if value and value != "unnamed"))


def _balanced_array(text: str, start: int) -> Optional[str]:
    """Return the balanced JSON array starting at *start*, or None."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def iter_json_arrays(text: str, marker: str) -> List[str]:
    """Yield every balanced JSON array that follows an occurrence of *marker*.

    The real page assigns ``upcoming_event_slides`` more than once (an empty
    initialiser plus the populated payload), so callers must consider all of them.
    """
    arrays: List[str] = []
    position = 0
    while True:
        marker_index = text.find(marker, position)
        if marker_index == -1:
            break
        start = text.find("[", marker_index)
        if start == -1:
            break
        raw = _balanced_array(text, start)
        if raw is not None:
            arrays.append(raw)
        position = marker_index + len(marker)
    return arrays


def extract_json_array(text: str, marker: str) -> Optional[str]:
    """Return the first balanced JSON array following *marker*, or None.

    Kept for backwards compatibility; prefer :func:`iter_json_arrays`.
    """
    arrays = iter_json_arrays(text, marker)
    return arrays[0] if arrays else None



def _load_lenient_array(raw: str) -> List[Dict[str, Any]]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # The page is hand-maintained JSON; tolerate trailing commas.
        cleaned = re.sub(r",\s*([\]}])", r"\1", raw)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ParseError(f"could not decode {SLIDES_MARKER} JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise ParseError(f"{SLIDES_MARKER} did not contain a JSON array")
    return [entry for entry in parsed if isinstance(entry, dict)]


class SecretLairStoreMonitor(Monitor):
    type_name = "secret_lair_store"

    def _base_url(self) -> str:
        parsed = urlparse(self.config.url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def fetch_items(self, client: HttpClient, body: str) -> List[Item]:
        items = self._items_from_slides(body)
        if not items:
            LOGGER.info("no upcoming_event_slides data; trying HTML fallback")
            items = self._items_from_html(body)
        if not items:
            raise ParseError(
                "no Secret Lair drop data found on the storefront page. "
                "The page layout may have changed; no alerts were generated."
            )
        return items

    # ------------------------------------------------------------------ parsers
    def _items_from_slides(self, body: str) -> List[Item]:
        best: List[Item] = []
        for raw in iter_json_arrays(body, SLIDES_MARKER):
            try:
                slides = _load_lenient_array(raw)
            except ParseError as exc:
                LOGGER.debug("skipping undecodable %s payload: %s", SLIDES_MARKER, exc)
                continue
            items = self._items_from_entries(slides)
            if len(items) > len(best):
                best = items
        return best

    def _items_from_entries(self, slides: List[Dict[str, Any]]) -> List[Item]:
        items: List[Item] = []
        seen = set()
        for slide in slides:
            if slide.get("visible") is False:
                continue
            title = html.unescape(
                str(slide.get("title") or slide.get("event_name") or "").strip()
            )
            if not title:
                continue
            anchor = str(slide.get("anchor_link") or "").strip().lstrip("#/")
            key = _slugify(anchor or title)
            if key in seen:
                continue
            seen.add(key)
            status = html.unescape(str(slide.get("subtitle") or "Unknown").strip())
            url = f"{self._base_url()}/us"
            if anchor:
                url = f"{url}#{anchor}"
            items.append(
                Item(
                    key=key,
                    title=title,
                    url=url,
                    status=status,
                    aliases=_aliases(anchor, title, str(slide.get("event_name") or "")),
                )
            )
        return items

    @staticmethod
    def _items_from_html(body: str) -> List[Item]:
        items: List[Item] = []
        seen = set()
        for match in FALLBACK_TITLE_RE.finditer(body):
            title = html.unescape(re.sub(r"<[^>]+>", "", match.group("title"))).strip()
            status = html.unescape(
                re.sub(r"<[^>]+>", "", match.group("status"))
            ).strip()
            if not title:
                continue
            key = _slugify(title)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                Item(key=key, title=title, url="", status=status, aliases=_aliases("", title, ""))
            )
        return items
