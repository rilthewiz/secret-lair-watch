"""Notifier factory."""

from __future__ import annotations

import logging
from typing import List

from ..config import NotifierConfig
from .base import Notification, NotificationBatch, Notifier, NotifierError
from .console import ConsoleNotifier
from .desktop import DesktopNotifier
from .email_notifier import EmailNotifier

LOGGER = logging.getLogger(__name__)


def build_notifiers(config: NotifierConfig, dry_run: bool = False) -> List[Notifier]:
    """Return enabled notifiers. In dry-run, only the console notifier is used."""
    notifiers: List[Notifier] = []
    if config.console_enabled:
        notifiers.append(ConsoleNotifier())
    if dry_run:
        return notifiers

    if config.desktop_enabled:
        desktop = DesktopNotifier()
        if desktop.enabled():
            notifiers.append(desktop)
        else:
            LOGGER.warning("desktop notifications unavailable on this system; skipping")

    if config.email.enabled:
        notifiers.append(EmailNotifier(config.email))

    return notifiers


def dispatch(notifiers: List[Notifier], batch: NotificationBatch) -> List[str]:
    """Send *batch* to every notifier; return human-readable failure messages."""
    if not batch.notifications:
        return []

    failures: List[str] = []
    for notifier in notifiers:
        try:
            notifier.send(batch)
        except NotifierError as exc:
            LOGGER.error("%s notifier failed: %s", notifier.name, exc)
            failures.append(f"{notifier.name}: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("%s notifier crashed", notifier.name)
            failures.append(f"{notifier.name}: unexpected error: {exc}")
    return failures


__all__ = [
    "Notification",
    "NotificationBatch",
    "Notifier",
    "NotifierError",
    "build_notifiers",
    "dispatch",
]
