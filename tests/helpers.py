"""Shared test helpers (stdlib unittest only, no network)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from lairwatch.config import Config, SourceConfig
from lairwatch.http_client import FetchResult
from lairwatch.notifiers.base import NotificationBatch, Notifier

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    """Stands in for HttpClient. Optionally answers 304 when the ETag matches."""

    def __init__(self, responses: Dict[str, FetchResult]) -> None:
        self.responses = responses
        self.calls: List[str] = []

    def get_text(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        accept: str = "",
        detect_blocks: bool = True,
        content_markers=(),
    ) -> FetchResult:
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError(f"unexpected request in test: {url}")
        response = self.responses[url]
        if etag and response.etag and etag == response.etag:
            return FetchResult(url=url, status=304, body="", not_modified=True)
        return response


class RecordingNotifier(Notifier):
    name = "recording"

    def __init__(self) -> None:
        self.batches: List[NotificationBatch] = []

    def send(self, batch: NotificationBatch) -> None:
        self.batches.append(batch)


class FailingNotifier(Notifier):
    name = "failing"

    def send(self, batch: NotificationBatch) -> None:
        from lairwatch.notifiers.base import NotifierError

        raise NotifierError("simulated failure")


def make_config(
    sources: Iterable[SourceConfig],
    state_path: Path,
    respect_robots: bool = False,
    log_file: Optional[Path] = None,
    drop_schedule=None,
) -> Config:
    return Config(
        state_path=state_path,
        respect_robots_txt=respect_robots,
        log_file=log_file,
        sources=list(sources),
        drop_schedule=drop_schedule or [],
        interval_seconds=3600,
        min_interval_seconds=900,
    )


def temp_dir() -> str:
    return tempfile.mkdtemp(prefix="lairwatch-test-")


def clean_env(*names: str) -> None:
    for name in names:
        os.environ.pop(name, None)
