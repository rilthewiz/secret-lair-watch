"""Monitor registry."""

from __future__ import annotations

from typing import Dict, Type

from .base import Item, Monitor, ParseError
from .secret_lair_store import SecretLairStoreMonitor
from .wotc_sitemap import WotcSitemapMonitor

MONITORS: Dict[str, Type[Monitor]] = {
    SecretLairStoreMonitor.type_name: SecretLairStoreMonitor,
    WotcSitemapMonitor.type_name: WotcSitemapMonitor,
}


def create_monitor(source_config) -> Monitor:  # noqa: ANN001 - avoids cycle
    try:
        monitor_class = MONITORS[source_config.type]
    except KeyError as exc:
        raise ValueError(f"unknown monitor type: {source_config.type}") from exc
    return monitor_class(source_config)


__all__ = ["Item", "Monitor", "ParseError", "create_monitor", "MONITORS"]
