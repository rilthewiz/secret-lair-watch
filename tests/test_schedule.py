"""Drop-time reminder and .ics calendar tests (no network, no sleeping)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lairwatch.schedule import (
    DropReminder,
    ScheduleError,
    build_ics,
    describe_upcoming,
    parse_schedule,
    upcoming_reminders,
    write_calendar,
)

from helpers import temp_dir

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def drop(minutes_ahead: int = 120, label: str = "Doomsdrop") -> DropReminder:
    return DropReminder(
        label=label,
        starts_at=NOW + timedelta(minutes=minutes_ahead),
        url="https://secretlair.wizards.com/us",
        reminders_minutes=(15, 0),
    )


class ParseScheduleTests(unittest.TestCase):
    def test_parses_iso_with_offset(self) -> None:
        drops = parse_schedule(
            [
                {
                    "label": "Doomsdrop",
                    "starts_at": "2026-11-02T09:00:00-08:00",
                    "url": "https://secretlair.wizards.com/us",
                    "reminders_minutes": [15, 0],
                }
            ]
        )
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0].starts_at.utcoffset(), timedelta(hours=-8))

    def test_parses_z_suffix(self) -> None:
        drops = parse_schedule(
            [
                {
                    "label": "Z",
                    "starts_at": "2026-11-02T17:00:00Z",
                    "url": "https://secretlair.wizards.com/us",
                }
            ]
        )
        self.assertEqual(drops[0].starts_at.tzinfo, timezone.utc)

    def test_rejects_missing_timezone(self) -> None:
        with self.assertRaises(ScheduleError):
            parse_schedule(
                [
                    {
                        "label": "Bad",
                        "starts_at": "2026-11-02T09:00:00",
                        "url": "https://secretlair.wizards.com/us",
                    }
                ]
            )

    def test_rejects_non_http_url(self) -> None:
        with self.assertRaises(ScheduleError):
            parse_schedule(
                [
                    {
                        "label": "Bad",
                        "starts_at": "2026-11-02T09:00:00Z",
                        "url": "javascript:alert(1)",
                    }
                ]
            )

    def test_empty_schedule_is_allowed(self) -> None:
        self.assertEqual(parse_schedule([]), [])
        self.assertEqual(parse_schedule(None), [])


class UpcomingTests(unittest.TestCase):
    def test_future_reminders_sorted(self) -> None:
        drops = [drop(60, "Second"), drop(120, "First")]
        pending = upcoming_reminders(drops, NOW)
        self.assertEqual(pending[0][1].label, "Second")
        self.assertTrue(all(fire > NOW for fire, _, _ in pending))

    def test_past_reminders_excluded(self) -> None:
        past = DropReminder(
            label="Past",
            starts_at=NOW - timedelta(days=1),
            url="https://secretlair.wizards.com/us",
        )
        self.assertEqual(upcoming_reminders([past], NOW), [])

    def test_describe_mentions_drop_time(self) -> None:
        text = describe_upcoming([drop(90)], NOW)
        self.assertIn("Doomsdrop", text)
        self.assertIn("at drop time", text)

    def test_describe_empty(self) -> None:
        self.assertEqual(describe_upcoming([], NOW), "no upcoming drop reminders configured")


class CalendarTests(unittest.TestCase):
    def test_ics_contains_event_and_alarms(self) -> None:
        content = build_ics([drop(120)], NOW)
        self.assertIn("BEGIN:VCALENDAR", content)
        self.assertIn("BEGIN:VEVENT", content)
        self.assertIn("DTSTART:", content)
        self.assertEqual(content.count("BEGIN:VALARM"), 2)  # T-15 and T-0
        self.assertIn("TRIGGER:-PT15M", content)
        self.assertIn("https://secretlair.wizards.com/us", content)

    def test_ics_uses_utc_timestamps(self) -> None:
        content = build_ics([drop(120)], NOW)
        expected = (NOW + timedelta(minutes=120)).strftime("%Y%m%dT%H%M%SZ")
        self.assertIn(f"DTSTART:{expected}", content)

    def test_ics_escapes_special_characters(self) -> None:
        weird = DropReminder(
            label="Drop, with; special\\chars",
            starts_at=NOW + timedelta(minutes=30),
            url="https://secretlair.wizards.com/us",
        )
        content = build_ics([weird], NOW)
        self.assertIn("SUMMARY:Secret Lair: Drop\\, with\\; special\\\\chars", content)

    def test_lines_are_folded(self) -> None:
        content = build_ics([drop(30, "A" * 200)], NOW)
        self.assertTrue(all(len(line.encode("utf-8")) <= 76 for line in content.split("\r\n")))

    def test_write_calendar_creates_file(self) -> None:
        target = Path(temp_dir()) / "drops.ics"
        written = write_calendar(str(target), [drop(120)])
        self.assertEqual(Path(written or "").resolve(), target.resolve())
        self.assertIn("BEGIN:VCALENDAR", target.read_text(encoding="utf-8"))

    def test_write_calendar_with_no_drops_writes_nothing(self) -> None:
        target = Path(temp_dir()) / "empty.ics"
        self.assertIsNone(write_calendar(str(target), []))
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
