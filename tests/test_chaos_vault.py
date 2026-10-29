"""Chaos Vault monitor tests. All bodies are synthetic; no network."""

from __future__ import annotations

import unittest
from pathlib import Path

from lairwatch.config import SourceConfig, load_config
from lairwatch.http_client import FetchResult
from lairwatch.monitors import MONITORS, ParseError, create_monitor
from lairwatch.monitors.chaos_vault import ChaosVaultMonitor
from lairwatch.runner import Runner
from lairwatch.state import StateStore

from helpers import FakeClient, RecordingNotifier, make_config, temp_dir

VAULT_URL = "https://secretlair.wizards.com/us/chaosvault"

# Shape of the page while the vault is closed: signup form + a couple of
# unrelated product links elsewhere on the page.
CLOSED_PAGE = """
<html><body>
  <div id="chaos_vault" ng-controller="NotifyMeEventController">
    <p class="notify-me-title">The Secret Lair Chaos Vault</p>
    <input id="chaos_vault_email" type="email">
    <button ng-click="notifyMeCall(&quot;Chaos_Vault&quot;, 'chaos_vault')">GET NOTIFIED</button>
  </div>
  <a href="/us/product/111111">Recent drop</a>
  <a href="/us/product/222222">Another drop</a>
</body></html>
"""

# Shape we would expect while the vault is open: the signup form is gone and
# the page lists vault products.
OPEN_PAGE = """
<html><body>
  <div id="chaos_vault_products">
    <a href="/us/product/333333">Chaos Vault foil</a>
    <a href="/us/product/444444">Chaos Vault non-foil</a>
    <a href="/us/product/555555">Chaos Vault bundle</a>
  </div>
</body></html>
"""


def monitor() -> ChaosVaultMonitor:
    return ChaosVaultMonitor(
        SourceConfig(name="chaos_vault", type="secret_lair_chaos_vault", url=VAULT_URL)
    )


class DetectionTests(unittest.TestCase):
    def test_signup_form_means_closed(self) -> None:
        self.assertEqual(ChaosVaultMonitor.detect_status(CLOSED_PAGE), "Closed")

    def test_signup_form_wins_even_with_product_links_present(self) -> None:
        # Regression guard: the closed page already links to other products.
        self.assertIn("/us/product/", CLOSED_PAGE)
        self.assertEqual(ChaosVaultMonitor.detect_status(CLOSED_PAGE), "Closed")

    def test_product_listing_without_signup_means_open(self) -> None:
        self.assertEqual(ChaosVaultMonitor.detect_status(OPEN_PAGE), "Open")

    def test_page_without_the_vault_is_a_parse_error(self) -> None:
        with self.assertRaises(ParseError):
            ChaosVaultMonitor.detect_status("<html><body>404</body></html>")

    def test_ambiguous_page_is_a_parse_error_not_a_guess(self) -> None:
        ambiguous = "<html>Chaos Vault <a href='/us/product/1'>one</a></html>"
        with self.assertRaises(ParseError) as raised:
            ChaosVaultMonitor.detect_status(ambiguous)
        self.assertIn("could not tell", str(raised.exception))

    def test_fetch_items_returns_one_stable_item(self) -> None:
        items = monitor().fetch_items(None, CLOSED_PAGE)  # type: ignore[arg-type]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].key, "chaos-vault")
        self.assertEqual(items[0].url, VAULT_URL)
        self.assertEqual(items[0].status, "Closed")


class RegistrationTests(unittest.TestCase):
    def test_type_is_registered(self) -> None:
        self.assertIn("secret_lair_chaos_vault", MONITORS)
        created = create_monitor(
            SourceConfig(
                name="vault", type="secret_lair_chaos_vault", url=VAULT_URL
            )
        )
        self.assertIsInstance(created, ChaosVaultMonitor)

    def test_ships_disabled_by_default(self) -> None:
        config = load_config(None)
        vault = [s for s in config.sources if s.type == "secret_lair_chaos_vault"]
        self.assertEqual(len(vault), 1)
        self.assertFalse(vault[0].enabled)
        self.assertTrue(vault[0].notify_on_status_change)


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_path = Path(temp_dir()) / "state.json"
        self.source = SourceConfig(
            name="chaos_vault",
            type="secret_lair_chaos_vault",
            url=VAULT_URL,
            notify_on_status_change=True,
        )

    def _run(self, body: str, etag: str):
        client = FakeClient(
            {VAULT_URL: FetchResult(url=VAULT_URL, status=200, body=body, etag=etag)}
        )
        recorder = RecordingNotifier()
        runner = Runner(
            config=make_config([self.source], self.state_path),
            state=StateStore(self.state_path).load(),
            client=client,  # type: ignore[arg-type]
            notifiers=[recorder],
        )
        return runner.run_once(), recorder

    def test_baseline_is_silent_then_opening_alerts_once(self) -> None:
        result, _ = self._run(CLOSED_PAGE, '"v1"')
        self.assertEqual(result.notifications, [])
        self.assertEqual(result.baselined, 1)

        result, _ = self._run(OPEN_PAGE, '"v2"')
        self.assertEqual(len(result.notifications), 1)
        notification = result.notifications[0]
        self.assertEqual(notification.kind, "status_change")
        self.assertEqual(notification.previous_status, "Closed")
        self.assertEqual(notification.status, "Open")

        result, _ = self._run(OPEN_PAGE, '"v3"')
        self.assertEqual(result.notifications, [])

    def test_closing_again_alerts_once(self) -> None:
        self._run(CLOSED_PAGE, '"v1"')
        self._run(OPEN_PAGE, '"v2"')
        result, _ = self._run(CLOSED_PAGE, '"v3"')
        self.assertEqual(len(result.notifications), 1)
        self.assertEqual(result.notifications[0].status, "Closed")

    def test_ambiguous_page_alerts_nothing(self) -> None:
        self._run(CLOSED_PAGE, '"v1"')
        result, _ = self._run("<html>Chaos Vault, no products</html>", '"v2"')
        self.assertEqual(result.notifications, [])
        self.assertEqual(len(result.errors), 1)


if __name__ == "__main__":
    unittest.main()
