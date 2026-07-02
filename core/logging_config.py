"""Centralized logging configuration for Cloud Janitor.

All modules should use:
    import logging
    logger = logging.getLogger(__name__)

This module provides configure_logging() which should be called once at
application startup (app.py, scheduler.py, or CLI entry point) to set up
the root logger with structured JSON output for production or human-readable
output for development.
"""

import logging
import os
import sys


def configure_logging(level: str | None = None) -> None:
    """Configure the root logger for Cloud Janitor.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
            Defaults to JANITOR_LOG_LEVEL env var, then INFO.
    """
    if level is None:
        level = os.environ.get("JANITOR_LOG_LEVEL", "INFO").upper()

    numeric_level = getattr(logging, level, logging.INFO)

    # Format: timestamp - module - level - message
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Stream handler to stderr (same destination as previous print() calls)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    # Configure root logger
    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Avoid duplicate handlers on re-import
    if not root.handlers:
        root.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
