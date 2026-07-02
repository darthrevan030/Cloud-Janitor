"""Centralized logging configuration for Cloud Janitor.

All modules should use:
    import logging
    logger = logging.getLogger(__name__)

This module provides configure_logging() which should be called once at
application startup (app.py, scheduler.py, or CLI entry point) to set up
the root logger with a consistent format and level.
"""

import logging
import os
import sys

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_DEFAULT_LEVEL = "INFO"
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    """Configure the root logger for Cloud Janitor.

    Reads JANITOR_LOG_LEVEL env var (case-insensitive).
    Valid levels: DEBUG, INFO, WARNING, ERROR.
    Falls back to INFO if missing or invalid (emits a WARNING if invalid).
    """
    raw_level = os.environ.get("JANITOR_LOG_LEVEL", _DEFAULT_LEVEL).upper()

    if raw_level not in _VALID_LEVELS:
        level = logging.INFO
        warn_invalid = True
    else:
        level = getattr(logging, raw_level)
        warn_invalid = False

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S"))

    logging.basicConfig(
        level=level,
        handlers=[handler],
        force=True,
    )

    if warn_invalid:
        logging.getLogger(__name__).warning(
            "Invalid JANITOR_LOG_LEVEL=%r, falling back to INFO. "
            "Valid values: DEBUG, INFO, WARNING, ERROR",
            os.environ.get("JANITOR_LOG_LEVEL"),
        )

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
