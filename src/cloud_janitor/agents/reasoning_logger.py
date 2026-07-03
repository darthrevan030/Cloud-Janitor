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

    # Maximum number of rotated history files to keep
    MAX_HISTORY_FILES = 5

    # Log rotation size threshold in bytes (10 MB)
    LOG_ROTATION_THRESHOLD = 10_485_760

    def truncate(self) -> None:
        """Rotate the log file if it exceeds the size threshold, then start fresh.

        Called at audit start. Rotates (renames with timestamp suffix) only when
        the current log exceeds LOG_ROTATION_THRESHOLD bytes. Keeps at most
        MAX_HISTORY_FILES rotated files; deletes oldest beyond that.

        On filesystem error: logs to stderr, does NOT raise.
        """
        try:
            if self._log_path.exists():
                file_size = self._log_path.stat().st_size
                if file_size > self.LOG_ROTATION_THRESHOLD:
                    # Rotate: rename current log with timestamp suffix
                    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    rotated = self._log_path.with_suffix(f".{ts}.log")
                    try:
                        self._log_path.rename(rotated)
                    except OSError:
                        pass  # If rename fails, just truncate below

                    # Prune old rotated files beyond MAX_HISTORY_FILES
                    self._prune_rotated_logs()

            # Create/truncate the active log file
            with open(self._log_path, mode="w", encoding="utf-8") as f:
                f.truncate(0)
        except OSError as exc:
            logger.error(f"ReasoningLogger: failed to rotate {self._log_path}: {exc}")

    def _prune_rotated_logs(self) -> None:
        """Delete oldest rotated log files beyond MAX_HISTORY_FILES."""
        try:
            pattern = self._log_path.stem + ".*.log"
            rotated = sorted(self._log_path.parent.glob(pattern))
            while len(rotated) > self.MAX_HISTORY_FILES:
                oldest = rotated.pop(0)
                oldest.unlink(missing_ok=True)
        except OSError:
            pass

    def start_run(self) -> None:
        """Write a run separator entry in append mode.

        Called at the start of each audit run. Creates file if missing.
        Preserves all previously written entries (Req 11.1).

        On filesystem error: prints to stderr, does NOT raise.
        """
        entry = {
            "event_type": "run_separator",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": "New audit run started",
        }
        try:
            line = json.dumps(entry, separators=(",", ":")) + "\n"
            with open(self._log_path, mode="a", encoding="utf-8") as f:
                f.write(line)
        except OSError as exc:
            print(
                f"ReasoningLogger: failed to write separator: {exc}",
                file=sys.stderr,
            )

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
