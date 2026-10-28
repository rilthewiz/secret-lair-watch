"""End-to-end runner tests: baselines, dedupe, status changes, dry-run, ETag."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from lairwatch.config import SourceConfig
from lairwatch.http_client import FetchResult
from lairwatch.notifiers.base import NotificationBatch
from lairwatch.runner import Runner
from lairwatch.state import StateStore

from helpers import FakeClient, RecordingNotifier, make_config, temp_dir

STORE_URL = "https://secretlair.wizards.com/us"


def store_page(*events: str) -> str:
    slides = ",".join(events)
    return (
        "<html><script>upcoming_event_slides = ["
        f"{slides}"
        "];</script></html>"
    )


def slide(title: str, status: str, anchor: str) -> str:
    return (
        '{"visible":true,"title":"%s","subtitle":"%s","anchor_link":"%s"}'
        % (title, status, anchor)
    )


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(temp_dir())
        self.state_path = self.tmp / "state.json"
        self.source = SourceConfig(
            name="store",
            type="secret_lair_store",
            url=STORE_URL,
            notify_on_status_change=True,
        )

    def _runner(
        self,
        body: str,
        etag: str,
        dry_run: bool = False,
        notifier: RecordingNotifier | None = None,
    ):
        client = FakeClient(
            {STORE_URL: FetchResult(url=STORE_URL, status=200, body=body, etag=etag)}
        )
        config = make_config([self.source], self.state_path)
        state = StateStore(self.state_path).load()
        recorder = notifier or RecordingNotifier()
        runner = Runner(
            config=config,
            state=state,
            client=client,  # type: ignore[arg-type]
            notifiers=[recorder],
            dry_run=dry_run,
        )
        return runner, recorder

    # ------------------------------------------------------------------ baseline
    def test_first_run_is_a_silent_baseline(self) -> None:
        body = store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop"))
        runner, recorder = self._runner(body, '"v1"')
        result = runner.run_once()
        self.assertEqual(result.notifications, [])
        self.assertEqual(result.baselined, 1)
        self.assertEqual(recorder.batches, [])

        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertTrue(saved["sources"]["store"]["baseline_done"])

    def test_new_item_after_baseline_alerts_once(self) -> None:
        baseline = store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop"))
        runner, recorder = self._runner(baseline, '"v1"')
        runner.run_once()

        updated = store_page(
            slide("Doomsdrop", "Coming Soon", "doomsdrop"),
            slide("Strawberry Countdown Kit", "Coming Soon", "strawberry"),
        )
        runner, recorder = self._runner(updated, '"v2"')
        result = runner.run_once()
        self.assertEqual(len(result.notifications), 1)
        self.assertEqual(result.notifications[0].kind, "new")
        self.assertEqual(result.notifications[0].title, "Strawberry Countdown Kit")
        self.assertEqual(len(recorder.batches), 1)

        # Same content again (new ETag, so no 304 shortcut) must not alert.
        runner, recorder = self._runner(updated, '"v3"')
        result = runner.run_once()
        self.assertEqual(result.notifications, [])
        self.assertEqual(recorder.batches, [])

    # ------------------------------------------------------------ status changes
    def test_status_change_alerts_once_and_records_previous(self) -> None:
        runner, _ = self._runner(
            store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop")), '"v1"'
        )
        runner.run_once()

        runner, recorder = self._runner(
            store_page(slide("Doomsdrop", "On Sale Now", "doomsdrop")), '"v2"'
        )
        result = runner.run_once()
        self.assertEqual(len(result.notifications), 1)
        notification = result.notifications[0]
        self.assertEqual(notification.kind, "status_change")
        self.assertEqual(notification.previous_status, "Coming Soon")
        self.assertEqual(notification.status, "On Sale Now")

        runner, recorder = self._runner(
            store_page(slide("Doomsdrop", "On Sale Now", "doomsdrop")), '"v3"'
        )
        self.assertEqual(runner.run_once().notifications, [])

    def test_status_change_can_be_disabled(self) -> None:
        self.source = SourceConfig(
            name="store",
            type="secret_lair_store",
            url=STORE_URL,
            notify_on_status_change=False,
        )
        runner, _ = self._runner(
            store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop")), '"v1"'
        )
        runner.run_once()
        runner, _ = self._runner(
            store_page(slide("Doomsdrop", "On Sale Now", "doomsdrop")), '"v2"'
        )
        self.assertEqual(runner.run_once().notifications, [])

    def test_alert_on_first_run_explains_existing_items(self) -> None:
        self.source = SourceConfig(
            name="store",
            type="secret_lair_store",
            url=STORE_URL,
            alert_on_first_run=True,
        )
        runner, _ = self._runner(
            store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop")), '"v1"'
        )
        result = runner.run_once()
        self.assertEqual(len(result.notifications), 1)

    # -------------------------------------------------------------------- dry-run
    def test_dry_run_sends_nothing_and_writes_no_state(self) -> None:
        body = store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop"))
        runner, recorder = self._runner(body, '"v1"', dry_run=True)
        result = runner.run_once()
        self.assertEqual(result.notifications, [])
        self.assertEqual(recorder.batches, [])
        self.assertFalse(self.state_path.exists(), "dry-run must not create state")

    def test_dry_run_reports_would_be_notifications(self) -> None:
        baseline = store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop"))
        runner, _ = self._runner(baseline, '"v1"')
        runner.run_once()
        before = self.state_path.read_bytes()

        updated = store_page(
            slide("Doomsdrop", "Coming Soon", "doomsdrop"),
            slide("Brand New Drop", "Coming Soon", "brand-new"),
        )
        runner, recorder = self._runner(updated, '"v2"', dry_run=True)
        result = runner.run_once()
        self.assertEqual(len(result.notifications), 1)
        self.assertEqual(recorder.batches, [])
        self.assertEqual(self.state_path.read_bytes(), before)

    # ------------------------------------------------------------------- caching
    def test_matching_etag_returns_304_and_no_alerts(self) -> None:
        body = store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop"))
        runner, _ = self._runner(body, '"v1"')
        runner.run_once()

        runner, recorder = self._runner(body, '"v1"')
        result = runner.run_once()
        self.assertEqual(result.unchanged, 1)
        self.assertEqual(result.notifications, [])
        self.assertEqual(recorder.batches, [])

    def test_state_survives_reload(self) -> None:
        body = store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop"))
        runner, _ = self._runner(body, '"v1"')
        runner.run_once()

        reloaded = StateStore(self.state_path).load().source("store")
        self.assertTrue(reloaded.baseline_done)
        self.assertEqual(reloaded.etag, '"v1"')
        self.assertIn("doomsdrop", reloaded.items)


class DispatchTests(unittest.TestCase):
    def test_failing_notifier_does_not_stop_others(self) -> None:
        from lairwatch.notifiers import Notification, dispatch
        from lairwatch.notifiers.base import NotifierError

        from helpers import FailingNotifier

        recorder = RecordingNotifier()
        batch = NotificationBatch(
            [Notification(source="s", kind="new", title="t", url="u", status="s")]
        )
        failures = dispatch([FailingNotifier(), recorder], batch)
        self.assertEqual(len(failures), 1)
        self.assertIn("simulated failure", failures[0])
        self.assertEqual(len(recorder.batches), 1)


if __name__ == "__main__":
    unittest.main()
