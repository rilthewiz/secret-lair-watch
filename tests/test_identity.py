"""Stable identity: a renamed drop must not arrive as a brand-new drop."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from lairwatch.config import SourceConfig
from lairwatch.http_client import FetchResult
from lairwatch.runner import Runner
from lairwatch.state import ItemRecord, StateStore

from helpers import FakeClient, RecordingNotifier, make_config, temp_dir

STORE_URL = "https://secretlair.wizards.com/us"


def slide(title: str, status: str, anchor: str) -> str:
    return (
        '{"visible":true,"title":"%s","subtitle":"%s","anchor_link":"%s"}'
        % (title, status, anchor)
    )


def store_page(*events: str) -> str:
    return "<html><script>upcoming_event_slides = [%s];</script></html>" % ",".join(
        events
    )


class RenameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(temp_dir())
        self.state_path = self.tmp / "state.json"
        self.source = SourceConfig(
            name="store",
            type="secret_lair_store",
            url=STORE_URL,
            notify_on_status_change=True,
        )

    def _run(self, body: str, etag: str):
        client = FakeClient(
            {STORE_URL: FetchResult(url=STORE_URL, status=200, body=body, etag=etag)}
        )
        state = StateStore(self.state_path).load()
        recorder = RecordingNotifier()
        runner = Runner(
            config=make_config([self.source], self.state_path),
            state=state,
            client=client,  # type: ignore[arg-type]
            notifiers=[recorder],
        )
        return runner.run_once(), recorder

    def _items(self) -> dict:
        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        return saved["sources"]["store"]["items"]

    def test_anchor_change_with_same_title_is_not_a_new_drop(self) -> None:
        self._run(store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop")), '"v1"')

        result, recorder = self._run(
            store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop-2026")), '"v2"'
        )

        self.assertEqual(result.notifications, [])
        self.assertEqual(recorder.batches, [])
        items = self._items()
        self.assertIn("doomsdrop-2026", items, "record should migrate to the new key")
        self.assertNotIn("doomsdrop", items)
        self.assertIn("doomsdrop", items["doomsdrop-2026"]["aliases"])

    def test_rename_does_not_repeat_on_the_next_run(self) -> None:
        self._run(store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop")), '"v1"')
        self._run(
            store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop-2026")), '"v2"'
        )
        result, _ = self._run(
            store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop-2026")), '"v3"'
        )
        self.assertEqual(result.notifications, [])

    def test_rename_keeps_first_seen(self) -> None:
        self._run(store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop")), '"v1"')
        first_seen = self._items()["doomsdrop"]["first_seen"]

        self._run(
            store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop-2026")), '"v2"'
        )

        record = self._items()["doomsdrop-2026"]
        self.assertEqual(record["first_seen"], first_seen)
        self.assertGreaterEqual(record["last_seen"], first_seen)

    def test_status_change_during_a_rename_alerts_once(self) -> None:
        self._run(store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop")), '"v1"')

        result, _ = self._run(
            store_page(slide("Doomsdrop", "On Sale Now", "doomsdrop-2026")), '"v2"'
        )

        self.assertEqual(len(result.notifications), 1)
        notification = result.notifications[0]
        self.assertEqual(notification.kind, "status_change")
        self.assertEqual(notification.previous_status, "Coming Soon")
        self.assertEqual(notification.status, "On Sale Now")

        result, _ = self._run(
            store_page(slide("Doomsdrop", "On Sale Now", "doomsdrop-2026")), '"v3"'
        )
        self.assertEqual(result.notifications, [])

    def test_changed_title_and_anchor_alerts_as_new(self) -> None:
        self._run(store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop")), '"v1"')

        result, _ = self._run(
            store_page(slide("Strawberry Countdown Kit", "Coming Soon", "strawberry")),
            '"v2"',
        )

        self.assertEqual(len(result.notifications), 1)
        self.assertEqual(result.notifications[0].kind, "new")

    def test_unrelated_second_drop_is_still_new(self) -> None:
        self._run(store_page(slide("Doomsdrop", "Coming Soon", "doomsdrop")), '"v1"')

        result, _ = self._run(
            store_page(
                slide("Doomsdrop", "Coming Soon", "doomsdrop-2026"),
                slide("Set Phasers to Superdrop", "Coming Soon", "set-phasers"),
            ),
            '"v2"',
        )

        self.assertEqual(len(result.notifications), 1)
        self.assertEqual(result.notifications[0].title, "Set Phasers to Superdrop")


class StateSchemaTests(unittest.TestCase):
    def test_aliases_survive_a_save_and_reload(self) -> None:
        path = Path(temp_dir()) / "state.json"
        store = StateStore(path)
        state = store.source("store")
        state.items["new-key"] = ItemRecord(
            key="new-key",
            title="Doomsdrop",
            url="",
            status="Coming Soon",
            first_seen=1.0,
            last_seen=2.0,
            aliases=["doomsdrop", "old-key"],
        )
        store.store_source(state)
        store.save()

        reloaded = StateStore(path).load().source("store")
        self.assertEqual(
            reloaded.items["new-key"].aliases, ["doomsdrop", "old-key"]
        )

    def test_state_written_before_aliases_existed_still_loads(self) -> None:
        record = ItemRecord.from_dict(
            "doomsdrop",
            {
                "title": "Doomsdrop",
                "url": "",
                "status": "Coming Soon",
                "first_seen": 1.0,
                "last_seen": 2.0,
            },
        )
        self.assertEqual(record.aliases, [])

    def test_bad_alias_values_are_discarded(self) -> None:
        record = ItemRecord.from_dict(
            "k", {"title": "T", "status": "s", "aliases": "not-a-list"}
        )
        self.assertEqual(record.aliases, [])


if __name__ == "__main__":
    unittest.main()
