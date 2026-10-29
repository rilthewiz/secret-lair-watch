"""Command-line entry point.

Modes:
  python -m lairwatch                 watch forever (loop with jitter)
  python -m lairwatch --once          check every source once, then exit
  python -m lairwatch --calendar out.ics   write an .ics of configured drops
  python -m lairwatch --remind        wait for configured drop reminders
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from .config import Config, ConfigError, load_config
from .http_client import HttpClient
from .logging_setup import setup_logging
from .notifiers import Notification, NotificationBatch, build_notifiers, dispatch
from .runner import Runner, RunResult
from .schedule import (
    DropReminder,
    describe_upcoming,
    open_official_page,
    play_alert,
    upcoming_reminders,
    write_calendar,
)
from .state import StateStore

LOGGER = logging.getLogger("lairwatch.cli")

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_CONFIG = 2
EXIT_BLOCKED = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lairwatch",
        description=(
            "Notify about official Magic: The Gathering Secret Lair announcements. "
            "Public pages only; never purchases, queues, or logs in."
        ),
    )
    parser.add_argument(
        "--config", type=Path, help="path to config.json (default: ./config.json if present)"
    )
    parser.add_argument(
        "--once", action="store_true", help="check every enabled source once and exit"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and parse, print what would be sent, send/write nothing",
    )
    parser.add_argument(
        "--calendar",
        metavar="PATH",
        help="write an .ics calendar of drop_schedule entries and exit (no network)",
    )
    parser.add_argument(
        "--remind",
        action="store_true",
        help="wait for the next configured drop reminder, alert, then continue",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="enable DEBUG logging"
    )
    parser.add_argument(
        "--version", action="version", version=f"lairwatch {__version__}"
    )
    return parser


def _resolve_config_path(args: argparse.Namespace) -> Optional[Path]:
    if args.config:
        return args.config
    candidate = Path("config.json")
    return candidate if candidate.exists() else None


def _build_runner(config: Config, dry_run: bool) -> Runner:
    state = StateStore(config.state_path).load()
    client = HttpClient(
        user_agent=config.user_agent,
        timeout=config.request_timeout_seconds,
        retry_delay=config.retry_delay_seconds,
    )
    notifiers = build_notifiers(config.notifiers, dry_run=dry_run)
    return Runner(
        config=config,
        state=state,
        client=client,
        notifiers=notifiers,
        dry_run=dry_run,
    )


def _exit_code(result: RunResult) -> int:
    if result.errors:
        return EXIT_PARTIAL
    if result.blocked and result.checked == 0:
        return EXIT_BLOCKED
    return EXIT_OK


def run_watch(config: Config, dry_run: bool, once: bool) -> int:
    runner = _build_runner(config, dry_run)
    if once:
        return _exit_code(runner.run_once())

    interval = config.effective_interval
    LOGGER.info(
        "watching %s source(s) every ~%ss; press Ctrl+C to stop",
        sum(1 for source in config.sources if source.enabled),
        interval,
    )
    try:
        while True:
            _exit_code(runner.run_once())
            delay = interval * (1 + random.uniform(-config.jitter_ratio, config.jitter_ratio))
            next_run = datetime.now() + timedelta(seconds=delay)
            LOGGER.info("next check at %s", next_run.strftime("%Y-%m-%d %H:%M:%S"))
            time.sleep(delay)
    except KeyboardInterrupt:
        LOGGER.info("stopped by user")
        return EXIT_OK


def run_calendar(config: Config, path: str) -> int:
    target = write_calendar(path, config.drop_schedule)
    if target is None:
        return EXIT_PARTIAL
    print(target)
    return EXIT_OK


def run_reminders(config: Config, dry_run: bool) -> int:
    drops = config.drop_schedule
    if not drops:
        LOGGER.warning(
            "no drops configured; add entries to 'drop_schedule' in your config"
        )
        return EXIT_PARTIAL

    print(describe_upcoming(drops))

    if dry_run:
        LOGGER.info("dry run: not waiting, not notifying")
        return EXIT_OK

    pending = upcoming_reminders(drops)
    if not pending:
        LOGGER.warning("no upcoming reminders; all configured drops are in the past")
        return EXIT_PARTIAL

    notifiers = build_notifiers(config.notifiers)
    max_wait = config.remind_max_wait_hours * 3600

    try:
        while pending:
            fire_time, drop, minutes = pending[0]
            wait_seconds = (fire_time - datetime.now(timezone.utc)).total_seconds()
            if wait_seconds > max_wait:
                LOGGER.info(
                    "next reminder for %s is %.1f hours away (limit %s); exiting",
                    drop.label, wait_seconds / 3600, config.remind_max_wait_hours,
                )
                break
            LOGGER.info(
                "next: %s for %s (in %.0f min)",
                "drop time" if minutes == 0 else f"T-{minutes}m",
                drop.label,
                max(wait_seconds, 0) / 60,
            )
            while wait_seconds > 0:
                time.sleep(min(wait_seconds, 15))
                wait_seconds = (
                    fire_time - datetime.now(timezone.utc)
                ).total_seconds()
            _fire_reminder(config, drop, minutes, notifiers)
            pending = upcoming_reminders(drops)
    except KeyboardInterrupt:
        LOGGER.info("stopped by user")
    return EXIT_OK


def _fire_reminder(
    config: Config, drop: DropReminder, minutes: int, notifiers: List
) -> None:
    when = "now" if minutes == 0 else f"in {minutes} minutes"
    notification = Notification(
        source="drop_schedule",
        kind="reminder",
        title=f"{drop.label} opens {when}",
        url=drop.url,
        status=drop.starts_at.astimezone().strftime("%Y-%m-%d %H:%M %Z"),
    )
    failures = dispatch(notifiers, NotificationBatch([notification]))
    if config.remind_sound:
        play_alert(config.remind_sound_file)
    for failure in failures:
        LOGGER.error("reminder notifier failed: %s", failure)
    if not failures:
        LOGGER.info("reminder delivered: %s", notification.headline())
    open_official_page(drop.url, enabled=config.remind_open_browser)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(_resolve_config_path(args))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    setup_logging(
        level=config.log_level, log_file=config.log_file, verbose=args.verbose
    )

    if args.calendar:
        return run_calendar(config, args.calendar)
    if args.remind:
        return run_reminders(config, args.dry_run)

    return run_watch(config, args.dry_run, args.once)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
