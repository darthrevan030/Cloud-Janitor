"""Shared per-run identifier and retention helper."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_RETENTION = 20


def generate_run_id() -> str:
    """Return a lexicographically sortable, unique run identifier."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{uuid.uuid4().hex[:8]}"


def get_retention(env_var: str) -> int:
    """Read a retention count from `env_var`, defaulting to 20 on unset/invalid."""
    raw = os.environ.get(env_var)
    if raw is None:
        return DEFAULT_RETENTION
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        logger.warning("Invalid %s=%r — falling back to default retention %d", env_var, raw, DEFAULT_RETENTION)
        return DEFAULT_RETENTION


def prune_run_scoped_files(directory: Path, suffix: str, keep: int, protect: set[str] | None = None) -> None:
    """Delete the oldest files matching '*<suffix>' in directory beyond `keep`."""
    if not directory.exists():
        return
    protect = protect or set()
    matches = sorted(
        p for p in directory.glob(f"*{suffix}") if p.name not in protect
    )
    excess = len(matches) - keep
    for path in matches[:max(excess, 0)]:
        try:
            path.unlink()
        except OSError:
            pass
