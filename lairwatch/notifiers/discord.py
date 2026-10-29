"""Discord webhook notifier.

A webhook needs no bot account, no OAuth token, and no login: it is a URL the
user creates in their own server and stores in an environment variable. This
module POSTs the same plain-text digest the console and email notifiers use.

Only Discord-owned hosts are accepted, so a typo in the environment variable
cannot turn this into a request to an arbitrary address.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import List, Optional
from urllib.parse import urlparse

from .base import NotificationBatch, Notifier, NotifierError

LOGGER = logging.getLogger(__name__)

# Discord-issued webhook hosts. Anything else is refused.
ALLOWED_HOSTS = (
    "discord.com",
    "discordapp.com",
    "canary.discord.com",
    "ptb.discord.com",
)

# Discord rejects content over 2000 characters; stay well below it.
MAX_CONTENT_LENGTH = 1800

# Never wait longer than this for a rate-limit reset inside one send.
MAX_RATE_LIMIT_WAIT = 10.0


def split_message(text: str, limit: int = MAX_CONTENT_LENGTH) -> List[str]:
    """Split *text* on line boundaries into chunks no longer than *limit*."""
    chunks: List[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            # A single pathological line: hard-split it.
            if current:
                chunks.append(current.rstrip("\n"))
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current.rstrip("\n"))
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.rstrip("\n"))
    return [chunk for chunk in chunks if chunk.strip()]


class DiscordNotifier(Notifier):
    name = "discord"

    def __init__(
        self,
        webhook_url: str,
        timeout: int = 10,
        rate_limit_wait: float = 2.0,
    ) -> None:
        self.webhook_url = (webhook_url or "").strip()
        self.timeout = timeout
        self.rate_limit_wait = rate_limit_wait

    @staticmethod
    def validate_url(webhook_url: str) -> None:
        """Refuse anything that is not an https Discord webhook URL."""
        if not webhook_url:
            raise NotifierError("discord webhook URL is empty")
        parsed = urlparse(webhook_url)
        if parsed.scheme != "https":
            raise NotifierError("discord webhook URL must use https")
        if parsed.netloc.lower() not in ALLOWED_HOSTS:
            raise NotifierError(
                f"discord webhook host {parsed.netloc!r} is not a Discord host; "
                "refusing to send"
            )
        if "/api/webhooks/" not in parsed.path:
            raise NotifierError("discord webhook URL is not a webhook endpoint")

    def send(self, batch: NotificationBatch) -> None:
        self.validate_url(self.webhook_url)
        for chunk in split_message(batch.as_text()):
            self._post(chunk)

    # ------------------------------------------------------------------ internals
    def _post(self, content: str) -> None:
        payload = json.dumps(
            {
                "content": content,
                "username": "Secret Lair Watch",
                # Never ping @everyone or roles because a drop title contains '@'.
                "allowed_mentions": {"parse": []},
            }
        ).encode("utf-8")

        for attempt in (1, 2):
            request = urllib.request.Request(
                self.webhook_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "secret-lair-watch (discord webhook notifier)",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    response.read()
                return
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt == 1:
                    wait = self._retry_after(exc)
                    if wait is not None and wait <= MAX_RATE_LIMIT_WAIT:
                        LOGGER.warning("discord rate limited us; waiting %.1fs", wait)
                        time.sleep(wait)
                        continue
                raise NotifierError(
                    f"discord webhook returned HTTP {exc.code}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise NotifierError(f"discord webhook unreachable: {exc}") from exc

    def _retry_after(self, exc: urllib.error.HTTPError) -> Optional[float]:
        """Read Discord's retry hint from the JSON body or the header."""
        try:
            raw = exc.read()
            if raw:
                data = json.loads(raw.decode("utf-8", errors="replace"))
                if isinstance(data, dict) and data.get("retry_after") is not None:
                    return float(data["retry_after"])
        except (OSError, ValueError, TypeError):
            pass
        header = exc.headers.get("Retry-After") if exc.headers else None
        if header:
            try:
                return float(header)
            except ValueError:
                return None
        return self.rate_limit_wait
