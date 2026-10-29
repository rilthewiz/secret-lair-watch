"""Logging configuration: console + size-capped rotating log file."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    verbose: bool = False,
) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    resolved_level = logging.DEBUG if verbose else getattr(logging, level.upper(), logging.INFO)
    root.setLevel(resolved_level)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
    console.setLevel(resolved_level)
    root.addHandler(console)

    if log_file is not None:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
            file_handler.setLevel(resolved_level)
            root.addHandler(file_handler)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "could not open log file %s (%s); continuing with console logging only",
                log_file, exc,
            )

    # urllib is chatty at DEBUG and would dominate the log.
    logging.getLogger("urllib.request").setLevel(logging.WARNING)
