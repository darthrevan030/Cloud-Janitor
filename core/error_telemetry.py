"""Structured error telemetry for Cloud Janitor.

Captures agent exceptions as structured JSONL records with
consistent categorization for operational diagnosis.
"""

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

ERROR_CATEGORIES = {
    "agent_failure",       # Exception within agent scan/plan logic
    "terraform_failure",   # Non-zero exit from TF_CMD
    "validation_failure",  # Schema, gate, or hook validation errors
    "io_failure",          # File system or network I/O errors
}


def build_error_record(
    exc: Exception,
    agent_name: str,
    error_category: str,
) -> dict:
    """Build a structured error record from an exception.

    Args:
        exc: The caught exception.
        agent_name: Identifying string for the failing agent.
        error_category: One of ERROR_CATEGORIES.

    Returns:
        Dict with fields: error_type, message, traceback,
        timestamp, agent_name, error_category.
    """
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    tb_str = "".join(tb)[:4096]  # Truncate to 4096 chars

    return {
        "error_type": type(exc).__name__,
        "message": str(exc),
        "traceback": tb_str,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_name": agent_name,
        "error_category": error_category,
    }


def write_error_record(record: dict, log_path: Path) -> None:
    """Append an error record as a single JSONL line."""
    line = json.dumps(record, separators=(",", ":")) + "\n"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)
