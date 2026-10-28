"""Email notifier over SMTP.

Credentials are read from environment variables at send time. They are never
written to the config file, the state file, or the logs.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from ..config import EmailConfig
from .base import NotificationBatch, Notifier, NotifierError

LOGGER = logging.getLogger(__name__)


class EmailNotifier(Notifier):
    name = "email"

    def __init__(self, config: EmailConfig) -> None:
        self.config = config

    def enabled(self) -> bool:
        return bool(self.config.enabled)

    def send(self, batch: NotificationBatch) -> None:
        message = EmailMessage()
        message["Subject"] = batch.subject()
        message["From"] = self.config.from_addr
        message["To"] = ", ".join(self.config.to_addrs)
        message.set_content(batch.as_text())

        username = os.environ.get(self.config.username_env) or None
        password = os.environ.get(self.config.password_env) or None
        if username and not password:
            raise NotifierError(
                f"email username is set via {self.config.username_env} but "
                f"{self.config.password_env} is empty"
            )

        try:
            with smtplib.SMTP(
                self.config.smtp_host, self.config.smtp_port, timeout=30
            ) as smtp:
                smtp.ehlo()
                if self.config.starttls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            # Never include the password or the raw SMTP transcript here.
            raise NotifierError(f"SMTP delivery failed: {exc}") from exc

        LOGGER.info(
            "email sent to %s via %s:%s",
            ", ".join(self.config.to_addrs),
            self.config.smtp_host,
            self.config.smtp_port,
        )
