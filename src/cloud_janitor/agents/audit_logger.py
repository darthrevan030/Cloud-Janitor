"""Append-only audit log writer.

Provides structured, file-based audit logging in JSON-lines format.
Each line is a complete JSON object with: timestamp, resource_id, actor, action, result.

The writer enforces append-only semantics — it never truncates or overwrites
existing log content. File I/O errors are handled gracefully; logging should
never crash the calling system.

Usage:
    from agents.audit_logger import AuditLogger
    from pathlib import Path

    logger = AuditLogger(Path("audit.log"))
    success = logger.append({
        "timestamp": "2025-01-15T10:30:00+00:00",
        "resource_id": "vol-abc123",
        "actor": "admin",
        "action": "approval",
        "result": "success",
    })
"""

from __future__ import annotations

import json
from pathlib import Path


class AuditLogger:
    """Append-only audit log writer using JSON-lines format.

    Each entry is written as a single JSON line. The file is opened in
    append mode ('a') for every write, ensuring no existing content is
    overwritten or truncated.

    Args:
        log_path: Path to the audit log file.
    """

    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path

    @property
    def log_path(self) -> Path:
        """The path to the audit log file."""
        return self._log_path

    def append(self, entry: dict) -> bool:
        """Append a single audit entry as a JSON line.

        Opens the file in append mode ('a') to enforce append-only semantics.
        Returns True on success, False if the write fails for any reason.

        Args:
            entry: A dictionary containing audit fields. Expected keys:
                timestamp, resource_id, actor, action, result.
                Additional keys (e.g. details) are preserved.

        Returns:
            True if the entry was written successfully, False otherwise.
        """
        try:
            line = json.dumps(entry, separators=(",", ":")) + "\n"
            with open(self._log_path, mode="a", encoding="utf-8") as f:
                f.write(line)
            return True
        except (OSError, IOError, TypeError, ValueError):
            return False

    def read_all(self) -> list[dict]:
        """Read all audit entries from the log file.

        Parses each line as a JSON object. Lines that cannot be parsed
        are silently skipped.

        Returns:
            A list of audit entry dictionaries, in chronological order.
            Returns an empty list if the file does not exist or cannot be read.
        """
        if not self._log_path.exists():
            return []

        entries: list[dict] = []
        try:
            with open(self._log_path, mode="r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        # Skip malformed lines — don't crash
                        continue
        except (OSError, IOError):
            return []

        return entries
