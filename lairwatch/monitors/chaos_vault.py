"""Experimental monitor for the Secret Lair Chaos Vault page.

The vault is either open (products are listed) or closed (the page shows a
"get notified" signup form). Wizards does not publish a machine-readable flag
for this, so the state is *inferred*:

  * signup form present  -> "Closed"
  * no signup form, and the page lists products -> "Open"
  * anything else        -> ParseError (no alert, rather than a guess)

The open state could not be observed while this was written (the vault was
closed), so this source ships disabled by default. Enable it deliberately:
a wrong "vault open" alert is worse than a missed one.

This module never submits the signup form, never stores an email address, and
never touches a cart or queue.
"""

from __future__ import annotations

import logging
import re
from typing import List

from ..http_client import HttpClient
from .base import Item, Monitor, ParseError

LOGGER = logging.getLogger(__name__)

STATUS_OPEN = "Open"
STATUS_CLOSED = "Closed"

# Elements that only exist while the vault is closed and asking for signups.
SIGNUP_MARKERS = (
    "chaos_vault_email",
    "notifymecall(&quot;chaos_vault&quot;",
    'notifymecall("chaos_vault"',
    "id=\"chaos_vault_container\"",
)

# A product link on the storefront, e.g. /us/product/123456.
PRODUCT_LINK_RE = re.compile(r"/us/product/[\w-]+", re.IGNORECASE)

# If the page does not mention the vault at all, we are not where we think.
PAGE_MARKERS = ("chaos vault", "chaos_vault")


class ChaosVaultMonitor(Monitor):
    type_name = "secret_lair_chaos_vault"

    def fetch_items(self, client: HttpClient, body: str) -> List[Item]:
        status = self.detect_status(body)
        return [
            Item(
                key="chaos-vault",
                title="The Secret Lair Chaos Vault",
                url=self.config.url,
                status=status,
                aliases=("chaos-vault", "the-secret-lair-chaos-vault"),
            )
        ]

    @staticmethod
    def detect_status(body: str) -> str:
        lowered = body.lower()
        if not any(marker in lowered for marker in PAGE_MARKERS):
            raise ParseError(
                "the Chaos Vault page did not mention the vault; layout may have "
                "changed. No alert was generated."
            )

        if any(marker in lowered for marker in SIGNUP_MARKERS):
            return STATUS_CLOSED

        products = set(PRODUCT_LINK_RE.findall(body))
        if len(products) >= 2:
            return STATUS_OPEN

        raise ParseError(
            "could not tell whether the Chaos Vault is open: no signup form and "
            f"only {len(products)} product link(s) found. No alert was generated."
        )
