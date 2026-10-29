"""Source monitors: turn a public page/feed into a list of Item objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from ..http_client import HttpClient


class ParseError(Exception):
    """The page was fetched but could not be understood (layout/format change)."""


@dataclass(frozen=True)
class Item:
    key: str
    title: str
    url: str
    status: str
    # Other stable identifiers for the same thing (slugged anchor, title, ...).
    # Used so that renaming a drop does not look like a brand-new drop.
    aliases: Tuple[str, ...] = ()

    def summary(self) -> str:
        return f"{self.title} [{self.status}]"


class Monitor:
    """Base class for a source monitor."""

    type_name = "base"

    def __init__(self, source_config) -> None:  # noqa: ANN001 - avoids cycle
        self.config = source_config

    def fetch_items(self, client: HttpClient, body: str) -> List[Item]:
        raise NotImplementedError

    def describe(self) -> str:
        return f"{self.config.name} ({self.type_name}) -> {self.config.url}"
