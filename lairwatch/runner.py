"""The polling engine: fetch, parse, diff against state, notify, persist."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .config import Config, SourceConfig
from .http_client import AccessBlocked, FetchError, HttpClient, RateLimited
from .monitors import ParseError, create_monitor
from .notifiers import Notification, NotificationBatch, Notifier, dispatch
from .robots import RobotsCache
from .state import ItemRecord, SourceState, StateStore

LOGGER = logging.getLogger(__name__)

# Markers that mean "this is the real document", used so that a healthy page
# embedding queue scripts is not mistaken for a queue interstitial.
CONTENT_MARKERS = {
    "secret_lair_store": ("upcoming_event_slides", "notify-me-title"),
    "wotc_sitemap": ("<urlset", "<sitemapindex"),
}


@dataclass
class RunResult:
    notifications: List[Notification] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    blocked: List[str] = field(default_factory=list)
    checked: int = 0
    unchanged: int = 0
    baselined: int = 0
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors and not self.blocked


class Runner:
    def __init__(
        self,
        config: Config,
        state: StateStore,
        client: HttpClient,
        notifiers: List[Notifier],
        dry_run: bool = False,
        robots: Optional[RobotsCache] = None,
    ) -> None:
        self.config = config
        self.state = state
        self.client = client
        self.notifiers = notifiers
        self.dry_run = dry_run
        self.robots = robots or RobotsCache(
            client,
            config.user_agent,
            cache_seconds=config.robots_cache_seconds,
            enabled=config.respect_robots_txt,
        )

    # ------------------------------------------------------------------- helpers
    def _robots_ok(self, source_state: SourceState, url: str) -> bool:
        if not self.config.respect_robots_txt:
            return True
        try:
            allowed, rules = self.robots.allowed(url, source_state.robots)
            if rules.fetched_at:
                source_state.robots = rules.to_dict()
            return allowed
        except FetchError as exc:
            return self.robots.failure_allowed(url, exc)

    # ---------------------------------------------------------------------- main
    def run_once(self) -> RunResult:
        result = RunResult(dry_run=self.dry_run)
        started = time.time()

        for source_config in self.config.sources:
            if not source_config.enabled:
                LOGGER.info("source %s is disabled; skipping", source_config.name)
                continue
            self._process_source(source_config, result)

        if result.notifications and not self.dry_run:
            failures = dispatch(self.notifiers, NotificationBatch(result.notifications))
            result.errors.extend(f"notifier {failure}" for failure in failures)
        elif result.notifications and self.dry_run:
            LOGGER.info(
                "DRY RUN: would send %s notification(s) and skip state changes",
                len(result.notifications),
            )

        if not self.dry_run:
            self.state.save()

        LOGGER.info(
            "run finished in %.1fs: %s new/updated item(s), %s source(s) checked, "
            "%s unchanged (304), %s error(s)",
            time.time() - started,
            len(result.notifications),
            result.checked,
            result.unchanged,
            len(result.errors) + len(result.blocked),
        )
        return result

    # ------------------------------------------------------------------- source
    def _process_source(self, source_config: SourceConfig, result: RunResult) -> None:
        name = source_config.name
        source_state = self.state.source(name)
        now = time.time()
        source_state.last_checked = now

        monitor = create_monitor(source_config)
        LOGGER.info("checking %s", monitor.describe())

        if not self._robots_ok(source_state, source_config.url):
            message = (
                f"{name}: robots.txt disallows {source_config.url}; skipped by policy"
            )
            LOGGER.warning(message)
            result.errors.append(message)
            self.state.store_source(source_state)
            return

        try:
            fetched = self.client.get_text(
                source_config.url,
                etag=source_state.etag,
                last_modified=source_state.last_modified,
                accept=(
                    "application/xml,text/xml;q=0.9,*/*;q=0.8"
                    if source_config.type == "wotc_sitemap"
                    else "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
                ),
                content_markers=CONTENT_MARKERS.get(source_config.type, ()),
            )
        except AccessBlocked as exc:
            LOGGER.error("%s: %s", name, exc)
            source_state.last_error = str(exc)
            result.blocked.append(f"{name}: {exc}")
            self.state.store_source(source_state)
            return
        except RateLimited as exc:
            LOGGER.error("%s: %s", name, exc)
            source_state.last_error = str(exc)
            result.errors.append(f"{name}: rate limited; backing off")
            self.state.store_source(source_state)
            return
        except FetchError as exc:
            LOGGER.error("%s: fetch failed: %s", name, exc)
            source_state.last_error = str(exc)
            result.errors.append(f"{name}: {exc}")
            self.state.store_source(source_state)
            return

        result.checked += 1

        if fetched.not_modified:
            LOGGER.info("%s: unchanged since last check (HTTP 304)", name)
            source_state.last_success = now
            source_state.last_error = None
            result.unchanged += 1
            self.state.store_source(source_state)
            return

        try:
            items = monitor.fetch_items(self.client, fetched.body)
        except ParseError as exc:
            LOGGER.error("%s: parse error: %s", name, exc)
            source_state.last_error = str(exc)
            result.errors.append(f"{name}: {exc}")
            self.state.store_source(source_state)
            return

        was_baselined = source_state.baseline_done
        new_count = self._diff(
            name, source_config, source_state, items, result, was_baselined
        )

        source_state.baseline_done = True
        source_state.etag = fetched.etag or source_state.etag
        source_state.last_modified = fetched.last_modified or source_state.last_modified
        source_state.last_success = now
        source_state.last_error = None

        if not was_baselined:
            result.baselined += 1
            LOGGER.info(
                "%s: baseline recorded with %s item(s); no alerts for pre-existing "
                "items (set alert_on_first_run to change this)",
                name,
                len(items),
            )
        elif new_count == 0:
            LOGGER.info("%s: no new items", name)

        if not self.dry_run:
            self.state.prune(source_state, self.config.prune_after_days)
        self.state.store_source(source_state)

    def _diff(
        self,
        name: str,
        source_config: SourceConfig,
        source_state: SourceState,
        items: List,  # List[Item]
        result: RunResult,
        was_baselined: bool,
    ) -> int:
        now = time.time()
        new_count = 0

        for item in items:
            record = source_state.items.get(item.key)
            if record is None:
                should_alert = was_baselined or source_config.alert_on_first_run
                if should_alert:
                    result.notifications.append(
                        Notification(
                            source=name,
                            kind="new",
                            title=item.title,
                            url=item.url,
                            status=item.status,
                        )
                    )
                    new_count += 1
                    LOGGER.info("%s: NEW %s [%s]", name, item.title, item.status)
                else:
                    LOGGER.debug("%s: baselining %s [%s]", name, item.title, item.status)

                source_state.items[item.key] = ItemRecord(
                    key=item.key,
                    title=item.title,
                    url=item.url,
                    status=item.status,
                    first_seen=now,
                    last_seen=now,
                    last_alerted_status=item.status if should_alert else None,
                )
                continue

            if item.status != record.status:
                if source_config.notify_on_status_change:
                    result.notifications.append(
                        Notification(
                            source=name,
                            kind="status_change",
                            title=item.title,
                            url=item.url or record.url,
                            status=item.status,
                            previous_status=record.status,
                        )
                    )
                    new_count += 1
                    LOGGER.info(
                        "%s: STATUS CHANGE %s [%s -> %s]",
                        name, item.title, record.status, item.status,
                    )
                    record.last_alerted_status = item.status
                else:
                    LOGGER.debug(
                        "%s: status changed for %s ([%s -> %s]) but "
                        "notify_on_status_change is off",
                        name, item.title, record.status, item.status,
                    )
                record.status = item.status

            record.title = item.title
            record.url = item.url or record.url
            record.last_seen = now

        return new_count
