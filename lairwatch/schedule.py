"""Drop-time reminders and calendar export.

This is deliberately *not* queue automation. It turns published drop times into
local alarms and an .ics file, so the human can be at the official page when it
opens and join the queue themselves, in normal order.

Nothing here contacts the store, refreshes pages, or holds a queue slot.
"""

from __future__ import annotations

import logging
import re
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Tuple

LOGGER = logging.getLogger(__name__)

DEFAULT_REMINDERS = (15, 0)


class ScheduleError(Exception):
    """Invalid drop schedule configuration."""


@dataclass(frozen=True)
class DropReminder:
    label: str
    starts_at: datetime
    url: str
    reminders_minutes: Tuple[int, ...] = DEFAULT_REMINDERS

    def __post_init__(self) -> None:
        if self.starts_at.tzinfo is None:
            raise ScheduleError(
                f"drop {self.label!r} starts_at must include a timezone offset"
            )
        if not self.url.startswith(("https://", "http://")):
            raise ScheduleError(f"drop {self.label!r} has an invalid url")

    def reminder_times(self) -> List[Tuple[datetime, int]]:
        return [
            (self.starts_at - timedelta(minutes=minutes), minutes)
            for minutes in sorted(set(self.reminders_minutes), reverse=True)
        ]


def parse_schedule(raw_schedule: object) -> List[DropReminder]:
    if raw_schedule is None:
        return []
    if not isinstance(raw_schedule, list):
        raise ScheduleError("'drop_schedule' must be a list")

    drops: List[DropReminder] = []
    for index, entry in enumerate(raw_schedule):
        if not isinstance(entry, dict):
            raise ScheduleError(f"drop_schedule[{index}] must be an object")
        label = str(entry.get("label") or "").strip()
        starts_raw = str(entry.get("starts_at") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not label:
            raise ScheduleError(f"drop_schedule[{index}] is missing 'label'")
        if not starts_raw:
            raise ScheduleError(f"drop_schedule[{index}] is missing 'starts_at'")
        try:
            starts_at = _parse_datetime(starts_raw)
        except ValueError as exc:
            raise ScheduleError(
                f"drop_schedule[{index}].starts_at must be ISO 8601 with an offset, "
                f"e.g. 2026-11-02T09:00:00-08:00 (got {starts_raw!r})"
            ) from exc
        if not url:
            raise ScheduleError(f"drop_schedule[{index}] is missing 'url'")

        minutes_raw = entry.get("reminders_minutes", list(DEFAULT_REMINDERS))
        if not isinstance(minutes_raw, list):
            raise ScheduleError(
                f"drop_schedule[{index}].reminders_minutes must be a list"
            )
        try:
            minutes = tuple(int(value) for value in minutes_raw)
        except (TypeError, ValueError) as exc:
            raise ScheduleError(
                f"drop_schedule[{index}].reminders_minutes must be integers"
            ) from exc
        if any(value < 0 for value in minutes):
            raise ScheduleError(
                f"drop_schedule[{index}].reminders_minutes must be >= 0"
            )

        drops.append(
            DropReminder(
                label=label,
                starts_at=starts_at,
                url=url,
                reminders_minutes=minutes or DEFAULT_REMINDERS,
            )
        )
    return drops


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("missing timezone offset")
    return parsed


def upcoming_reminders(
    drops: Sequence[DropReminder], now: Optional[datetime] = None
) -> List[Tuple[datetime, DropReminder, int]]:
    """Return future (fire_time, drop, minutes_before) tuples, soonest first."""
    current = now or datetime.now(timezone.utc)
    pending: List[Tuple[datetime, DropReminder, int]] = []
    for drop in drops:
        for fire_time, minutes in drop.reminder_times():
            if fire_time > current:
                pending.append((fire_time, drop, minutes))
    pending.sort(key=lambda entry: entry[0])
    return pending


def describe_upcoming(drops: Sequence[DropReminder], now: Optional[datetime] = None) -> str:
    pending = upcoming_reminders(drops, now)
    if not pending:
        return "no upcoming drop reminders configured"
    lines = []
    for fire_time, drop, minutes in pending:
        when = "at drop time" if minutes == 0 else f"{minutes} min before"
        lines.append(
            f"{fire_time.astimezone().strftime('%Y-%m-%d %H:%M %Z')} "
            f"({when}) - {drop.label}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------- calendar
def _escape_ics_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold_ics_line(line: str) -> str:
    """Fold a content line at 75 octets, per RFC 5545."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    chunks: List[str] = []
    current = ""
    current_len = 0
    limit = 75
    for char in line:
        char_len = len(char.encode("utf-8"))
        if current_len + char_len > limit:
            chunks.append(current)
            current = " " + char
            current_len = 1 + char_len
            limit = 75
        else:
            current += char
            current_len += char_len
    if current:
        chunks.append(current)
    return "\r\n".join(chunks)


def _utc_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "drop"


def build_ics(
    drops: Sequence[DropReminder], now: Optional[datetime] = None
) -> str:
    """Build an .ics calendar with one event per drop and reminder alarms."""
    created = now or datetime.now(timezone.utc)
    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//secret-lair-watch//drop reminders//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Secret Lair drops",
    ]

    for drop in drops:
        uid = f"{_slug(drop.label)}-{_utc_stamp(drop.starts_at)}@secret-lair-watch"
        description = (
            f"{drop.label} is scheduled to open at "
            f"{drop.starts_at.astimezone().strftime('%Y-%m-%d %H:%M %Z')}.\n"
            "Open the official store yourself and join the queue normally:\n"
            f"{drop.url}\n"
            "No automated queueing, carting, or checkout."
        )
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{_utc_stamp(created)}",
                f"DTSTART:{_utc_stamp(drop.starts_at)}",
                f"SUMMARY:{_escape_ics_text('Secret Lair: ' + drop.label)}",
                f"DESCRIPTION:{_escape_ics_text(description)}",
                f"URL:{drop.url}",
                "TRANSP:TRANSPARENT",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:Secret Lair drop reminder",
                "TRIGGER:-PT0M",
                "END:VALARM",
            ]
        )
        for minutes in sorted(set(drop.reminders_minutes), reverse=True):
            if minutes == 0:
                continue
            lines.extend(
                [
                    "BEGIN:VALARM",
                    "ACTION:DISPLAY",
                    f"DESCRIPTION:Secret Lair drops in {minutes} minutes",
                    f"TRIGGER:-PT{minutes}M",
                    "END:VALARM",
                ]
            )
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    folded = [_fold_ics_line(line) for line in lines]
    return "\r\n".join(folded) + "\r\n"


def write_calendar(path: str, drops: Sequence[DropReminder]) -> Optional[str]:
    """Write an .ics file; returns the absolute path or None if nothing to write."""
    if not drops:
        LOGGER.warning("no drops in drop_schedule; nothing to write to %s", path)
        return None
    import os

    content = build_ics(drops)
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    LOGGER.info("wrote %s drop(s) to calendar file %s", len(drops), target)
    return target


def open_official_page(url: str, enabled: bool) -> None:
    """Open the official page in the default browser, if the user opted in."""
    if not enabled:
        LOGGER.info("browser opening disabled; open this yourself: %s", url)
        return
    LOGGER.info("opening official store page: %s", url)
    webbrowser.open(url, new=2)


def play_alert(sound_file: Optional[str] = None) -> None:
    """Best-effort audible alarm so a reminder is hard to miss."""
    import os
    import platform
    import shutil
    import subprocess

    system = platform.system().lower()
    try:
        if system == "windows":
            import winsound

            if sound_file and os.path.exists(sound_file):
                winsound.PlaySound(sound_file, winsound.SND_FILENAME)
            else:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        elif system == "darwin" and shutil.which("afplay") and sound_file:
            subprocess.run(["afplay", sound_file], timeout=10, check=False)
        elif shutil.which("paplay") and sound_file:
            subprocess.run(["paplay", sound_file], timeout=10, check=False)
        else:
            LOGGER.info("no local sound available; relying on the desktop notification")
    except Exception as exc:  # pragma: no cover - platform-specific
        LOGGER.warning("could not play alert sound: %s", exc)
