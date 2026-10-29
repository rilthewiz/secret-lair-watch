"""Notifier factory."""

from __future__ import annotations

import logging
import os
from typing import List

from ..config import NotifierConfig
from .base import Notification, NotificationBatch, Notifier, NotifierError
from .console import ConsoleNotifier
from .desktop import DesktopNotifier
from .discord import DiscordNotifier
from .email_notifier import EmailNotifier

LOGGER = logging.getLogger(__name__)


def _discord_from_config(config: NotifierConfig) -> DiscordNotifier | None:
    """Build the Discord notifier from the webhook URL in the environment."""
    env_name = config.discord.webhook_env
    url = (os.environ.get(env_name) or "").strip()
    if not url:
        LOGGER.warning(
            "discord notifier enabled but %s is not set; skipping it",
            env_name,
        )
        return None
    try:
        DiscordNotifier.validate_url(url)
    except NotifierError as exc:
        # Never echo the URL: it is a credential.
        LOGGER.error("discord notifier misconfigured: %s", exc)
        return None
    return DiscordNotifier(url, timeout=config.discord.timeout_seconds)


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

    if config.discord.enabled:
        discord = _discord_from_config(config)
        if discord is not None:
            notifiers.append(discord)

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
