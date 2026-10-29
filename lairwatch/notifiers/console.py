"""Console/log notifier: always safe, always available."""

from __future__ import annotations

import logging

from .base import NotificationBatch, Notifier

LOGGER = logging.getLogger(__name__)


class ConsoleNotifier(Notifier):
    name = "console"

    def send(self, batch: NotificationBatch) -> None:
        LOGGER.info("notification digest: %s", batch.subject())
        for notification in batch.notifications:
            for line in notification.body_lines():
                LOGGER.info("  %s", line)
