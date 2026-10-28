"""A small robots.txt parser and cache.

Python's stdlib ``urllib.robotparser`` does not implement ``*``/``$`` rules and
has no caching hooks, so this module implements the parts we need:

  * group selection by User-Agent token (with ``*`` fallback)
  * Allow/Disallow prefix rules, ``*`` wildcards and ``$`` end anchors
  * longest-match wins, Allow wins ties (RFC 9309 style)

If robots.txt cannot be fetched, behaviour is configurable by the caller. This
app defaults to "allow with a warning": it never hides the failure.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .http_client import FetchError, HttpClient

LOGGER = logging.getLogger(__name__)


@dataclass
class RobotsRules:
    fetched_at: float
    allow: List[str] = field(default_factory=list)
    disallow: List[str] = field(default_factory=list)
    sitemaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "fetched_at": self.fetched_at,
            "allow": self.allow,
            "disallow": self.disallow,
            "sitemaps": self.sitemaps,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "RobotsRules":
        return cls(
            fetched_at=float(data.get("fetched_at") or 0),
            allow=[str(item) for item in (data.get("allow") or [])],
            disallow=[str(item) for item in (data.get("disallow") or [])],
            sitemaps=[str(item) for item in (data.get("sitemaps") or [])],
        )


def _pattern_to_regex(pattern: str) -> str:
    regex = ""
    for char in pattern:
        if char == "*":
            regex += ".*"
        elif char == "$":
            regex += "$"
        else:
            regex += re.escape(char)
    return "^" + regex


def parse_robots(text: str, user_agent: str = "*") -> Tuple[List[str], List[str], List[str]]:
    """Return (allow, disallow, sitemaps) for *user_agent*."""
    token = user_agent.split("/")[0].strip().lower()
    groups: List[Dict[str, object]] = []
    current: Optional[Dict[str, object]] = None
    in_agent_block = False

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        if key == "user-agent":
            if current is None or not in_agent_block:
                current = {"agents": [], "allow": [], "disallow": []}
                groups.append(current)
                in_agent_block = True
            current["agents"].append(value.lower())  # type: ignore[index]
            continue

        in_agent_block = False
        if current is None:
            current = {"agents": ["*"], "allow": [], "disallow": []}
            groups.append(current)

        if key in ("allow", "disallow"):
            current[key].append(value)  # type: ignore[index]
        elif key == "sitemap":
            current.setdefault("sitemaps", [])  # type: ignore[union-attr]
            current["sitemaps"].append(value)  # type: ignore[index]

    allow: List[str] = []
    disallow: List[str] = []
    sitemaps: List[str] = []

    for group in groups:
        agents = group.get("agents", [])
        if not isinstance(agents, list):
            continue
        matches = any(
            agent == "*" or (token and agent in token) or (token and token in agent)
            for agent in agents
        )
        if not matches:
            continue
        allow.extend(str(item) for item in group.get("allow", []))  # type: ignore[union-attr]
        disallow.extend(str(item) for item in group.get("disallow", []))  # type: ignore[union-attr]
        for sitemap in group.get("sitemaps", []) or []:  # type: ignore[union-attr]
            sitemaps.append(str(sitemap))

    return allow, disallow, sitemaps


def is_allowed(path: str, allow: List[str], disallow: List[str]) -> bool:
    """Longest-match rule; Allow wins ties."""
    best_allow = -1
    best_disallow = -1

    for rule in allow:
        if rule and re.match(_pattern_to_regex(rule), path):
            best_allow = max(best_allow, len(rule))
    for rule in disallow:
        if rule and re.match(_pattern_to_regex(rule), path):
            best_disallow = max(best_disallow, len(rule))

    if best_disallow == -1:
        return True
    return best_allow >= best_disallow


class RobotsCache:
    """Fetches and caches robots.txt per host inside the app's state file."""

    def __init__(
        self,
        client: HttpClient,
        user_agent: str,
        cache_seconds: int = 86400,
        enabled: bool = True,
    ) -> None:
        self.client = client
        self.user_agent = user_agent
        self.cache_seconds = cache_seconds
        self.enabled = enabled

    def rules_for(
        self, url: str, cached: Optional[Dict[str, object]]
    ) -> Tuple[RobotsRules, bool]:
        """Return (rules, from_cache). Raises FetchError if robots is unreachable."""
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        if cached:
            rules = RobotsRules.from_dict(cached)
            age = time.time() - rules.fetched_at
            if 0 <= age < self.cache_seconds:
                return rules, True

        result = self.client.get_text(
            robots_url,
            accept="text/plain,*/*;q=0.8",
            detect_blocks=False,
        )
        allow, disallow, sitemaps = parse_robots(result.body, self.user_agent)
        rules = RobotsRules(
            fetched_at=time.time(), allow=allow, disallow=disallow, sitemaps=sitemaps
        )
        return rules, False

    def allowed(self, url: str, cached: Optional[Dict[str, object]]) -> Tuple[bool, RobotsRules]:
        """Decide whether *url* may be fetched. Raises FetchError on network errors."""
        if not self.enabled:
            return True, RobotsRules(fetched_at=0)

        rules, from_cache = self.rules_for(url, cached)
        parsed = urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        allowed = is_allowed(path, rules.allow, rules.disallow)
        source = "cached" if from_cache else "fresh"
        if allowed:
            LOGGER.debug("robots.txt (%s) allows %s", source, url)
        else:
            LOGGER.warning("robots.txt (%s) disallows %s", source, url)
        return allowed, rules

    @staticmethod
    def failure_allowed(url: str, exc: Exception) -> bool:
        """Policy when robots.txt itself cannot be fetched."""
        LOGGER.warning(
            "could not fetch robots.txt for %s (%s); continuing with the configured "
            "conservative interval because no explicit prohibition was received",
            url, exc,
        )
        return True
