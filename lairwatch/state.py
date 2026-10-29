"""Local, crash-safe state: seen items, statuses, ETags, and robots cache.

State is a single JSON document written atomically (temp file + os.replace) so a
crash can never leave a half-written file. A corrupt or unreadable state file is
backed up and replaced with an empty state rather than crashing the app.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

LOGGER = logging.getLogger(__name__)

STATE_VERSION = 1


@dataclass
class ItemRecord:
    key: str
    title: str
    url: str
    status: str
    first_seen: float
    last_seen: float
    last_alerted_status: Optional[str] = None
    # Alternative identifiers seen for this item (e.g. the title when the anchor
    # changed). Lets a rename match the existing record instead of re-alerting.
    aliases: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "status": self.status,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "last_alerted_status": self.last_alerted_status,
            "aliases": self.aliases,
        }

    @classmethod
    def from_dict(cls, key: str, data: Dict[str, Any]) -> "ItemRecord":
        aliases = data.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        return cls(
            key=key,
            title=str(data.get("title") or key),
            url=str(data.get("url") or ""),
            status=str(data.get("status") or ""),
            first_seen=float(data.get("first_seen") or 0),
            last_seen=float(data.get("last_seen") or 0),
            last_alerted_status=data.get("last_alerted_status"),
            aliases=[str(alias) for alias in aliases if str(alias)],
        )


@dataclass
class SourceState:
    name: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    baseline_done: bool = False
    last_checked: Optional[float] = None
    last_success: Optional[float] = None
    last_error: Optional[str] = None
    robots: Optional[Dict[str, Any]] = None
    items: Dict[str, ItemRecord] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "etag": self.etag,
            "last_modified": self.last_modified,
            "baseline_done": self.baseline_done,
            "last_checked": self.last_checked,
            "last_success": self.last_success,
            "last_error": self.last_error,
            "robots": self.robots,
            "items": {key: record.to_dict() for key, record in self.items.items()},
        }

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "SourceState":
        items_raw = data.get("items") or {}
        items: Dict[str, ItemRecord] = {}
        if isinstance(items_raw, dict):
            for key, value in items_raw.items():
                if isinstance(value, dict):
                    items[str(key)] = ItemRecord.from_dict(str(key), value)
        return cls(
            name=name,
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
            baseline_done=bool(data.get("baseline_done")),
            last_checked=data.get("last_checked"),
            last_success=data.get("last_success"),
            last_error=data.get("last_error"),
            robots=data.get("robots") if isinstance(data.get("robots"), dict) else None,
            items=items,
        )


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: Dict[str, Any] = {"version": STATE_VERSION, "sources": {}}

    # ------------------------------------------------------------------ loading
    def load(self) -> "StateStore":
        if not self.path.exists():
            LOGGER.info("no state file yet at %s; starting fresh", self.path)
            return self
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("state root is not an object")
        except (OSError, ValueError) as exc:
            backup = self.path.with_suffix(self.path.suffix + ".corrupt")
            try:
                if self.path.exists():
                    os.replace(self.path, backup)
                LOGGER.error(
                    "state file %s was unreadable (%s); moved to %s and starting fresh",
                    self.path, exc, backup,
                )
            except OSError:
                LOGGER.error("state file %s was unreadable (%s)", self.path, exc)
            self._data = {"version": STATE_VERSION, "sources": {}}
            return self

        if not isinstance(data.get("sources"), dict):
            data["sources"] = {}
        data["version"] = STATE_VERSION
        self._data = data
        return self

    # ------------------------------------------------------------------- saving
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + f".tmp{os.getpid()}")
        payload = json.dumps(self._data, indent=2, sort_keys=True)
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, self.path)

    # ----------------------------------------------------------------- accessors
    def source(self, name: str) -> SourceState:
        sources = self._data.setdefault("sources", {})
        raw = sources.get(name)
        if not isinstance(raw, dict):
            raw = {}
            sources[name] = raw
        return SourceState.from_dict(name, raw)

    def store_source(self, source: SourceState) -> None:
        self._data.setdefault("sources", {})[source.name] = source.to_dict()

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    # ------------------------------------------------------------------ pruning
    def prune(self, source: SourceState, older_than_days: int) -> int:
        cutoff = time.time() - older_than_days * 86400
        stale: List[str] = [
            key for key, record in source.items.items() if record.last_seen < cutoff
        ]
        for key in stale:
            source.items.pop(key, None)
        if stale:
            LOGGER.info("pruned %s stale item(s) from %s", len(stale), source.name)
        return len(stale)
