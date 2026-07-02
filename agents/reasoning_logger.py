"""Structured JSON event logger for agent reasoning traces.

Provides a streaming JSONL logger that each agent uses to emit structured
decision events during audit execution. The log file is truncated at the
start of each new audit run and appended to sequentially during the run.

Filesystem errors are printed to stderr and never raised — agent execution
must not be interrupted by logging failures.

Usage:
    from agents.reasoning_logger import ReasoningLogger

    logger = ReasoningLogger()
    logger.truncate()
    logger.emit("finops_auditor", "check", "cache-prod-legacy-01", "Checking idle duration")
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import logging

logger = logging.getLogger(__name__)



class ReasoningLogger:
    """Structured JSON event logger for agent reasoning traces.

    Each call to emit() appends a single JSON line to the log file.
    The log is truncated at the start of each audit run via truncate().

    Args:
        log_path: Path to the reasoning log file.
            Defaults to ``output/logs/agent_reasoning.log`` relative to the project root.
    """

    VALID_EVENT_TYPES = {"check", "finding", "skip", "decision", "handoff"}

    def __init__(self, log_path: Path | None = None) -> None:
        if log_path is not None:
            self._log_path = log_path
        else:
            self._log_path = Path(__file__).resolve().parent.parent / "output" / "logs" / "agent_reasoning.log"

    @property
    def log_path(self) -> Path:
        """The path to the reasoning log file."""
        return self._log_path

    def truncate(self) -> None:
        """Truncate the log file (called at audit start).

        Opens the file in write mode to clear all existing content.
        On filesystem error: prints to stderr, does NOT raise.
        """
        try:
            with open(self._log_path, mode="w", encoding="utf-8") as f:
                f.truncate(0)
        except OSError as exc:
            logger.error(f"ReasoningLogger: failed to truncate {self._log_path}: {exc}")

    def emit(self, agent: str, event_type: str, resource_id: str, message: str) -> None:
        """Append a structured JSON line to the reasoning log.

        Args:
            agent: Agent name (max 64 chars, truncated if longer).
            event_type: One of VALID_EVENT_TYPES. If invalid, the event is
                emitted with event_type set to ``"unknown"``.
            resource_id: Resource ID or empty string.
            message: Plain-text explanation (max 500 chars, truncated if longer).

        On filesystem error: prints to stderr, does NOT raise.
        """
        # Truncate fields silently
        agent = agent[:64]
        message = message[:500]

        # Validate event_type — use "unknown" fallback for invalid values
        if event_type not in self.VALID_EVENT_TYPES:
            event_type = "unknown"

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "event_type": event_type,
            "resource_id": resource_id,
            "message": message,
        }

        try:
            line = json.dumps(entry, separators=(",", ":")) + "\n"
            with open(self._log_path, mode="a", encoding="utf-8") as f:
                f.write(line)
        except OSError as exc:
            logger.error(f"ReasoningLogger: failed to write to {self._log_path}: {exc}")
