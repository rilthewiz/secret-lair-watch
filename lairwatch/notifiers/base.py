"""Notifier interface and shared notification formatting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence


class NotifierError(Exception):
    """A notifier could not deliver; other notifiers still run."""


@dataclass(frozen=True)
class Notification:
    source: str
    kind: str  # "new" | "status_change"
    title: str
    url: str
    status: str
    previous_status: Optional[str] = None

    @property
    def label(self) -> str:
        if self.kind == "status_change":
            return "STATUS CHANGE"
        if self.kind == "reminder":
            return "REMINDER"
        return "NEW"

    def headline(self) -> str:
        if self.kind == "status_change":
            return f"{self.title}: {self.previous_status} -> {self.status}"
        return f"{self.title} ({self.status})"

    def body_lines(self) -> List[str]:
        lines = [
            f"[{self.label}] {self.title}",
            f"  status: {self.status}",
        ]
        if self.previous_status:
            lines.append(f"  previous status: {self.previous_status}")
        lines.append(f"  source: {self.source}")
        if self.url:
            lines.append(f"  url: {self.url}")
        return lines


@dataclass
class NotificationBatch:
    notifications: Sequence[Notification]
    checked_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.checked_at is None:
            self.checked_at = datetime.now(timezone.utc)

    def __len__(self) -> int:
        return len(self.notifications)

    @property
    def timestamp(self) -> str:
        assert self.checked_at is not None
        return self.checked_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    def subject(self) -> str:
        count = len(self.notifications)
        noun = "update" if count == 1 else "updates"
        return f"Secret Lair Watch: {count} {noun}"

    def as_text(self) -> str:
        lines = [
            f"Secret Lair Watch - {self.timestamp}",
            "",
        ]
        for notification in self.notifications:
            lines.extend(notification.body_lines())
            lines.append("")
        lines.extend(
            [
                "-- ",
                "These are official public sources only. Nothing was added to a cart",
                "and no order was placed. Open the official store and complete the",
                "purchase manually:",
                "https://secretlair.wizards.com/us",
            ]
        )
        return "\n".join(lines)


class Notifier:
    name = "base"

    def enabled(self) -> bool:
        return True

    def send(self, batch: NotificationBatch) -> None:
        raise NotImplementedError
