"""Centralized path configuration for Cloud Janitor runtime artifacts.

All artifact paths are constructed here. No module should build
artifact paths via string literals outside this module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("JANITOR_HOME", Path.cwd())).resolve()

# Base output directory
OUTPUT_DIR = PROJECT_ROOT / "output"

# Subdirectories
ROLLBACKS_DIR = OUTPUT_DIR / "rollbacks"
REMEDIATIONS_DIR = OUTPUT_DIR / "remediations"
LOGS_DIR = OUTPUT_DIR / "logs"
POLICIES_DIR = OUTPUT_DIR / "policies"

# Specific files
FINDINGS_STORE_PATH = OUTPUT_DIR / "findings_store.json"
AUDIT_LOG_PATH = LOGS_DIR / "audit.log"
REASONING_LOG_PATH = LOGS_DIR / "agent_reasoning.log"
APPROVAL_GATES_PATH = OUTPUT_DIR / "approval_gates.json"
SAVINGS_LEDGER_PATH = OUTPUT_DIR / "savings_ledger.json"
COST_EXPLORER_CACHE_PATH = OUTPUT_DIR / "cost_explorer_cache.json"
STATE_STORE_PATH = OUTPUT_DIR / "state.db"

# Findings store (run-scoped)
FINDINGS_STORE_DIR = OUTPUT_DIR / "findings_store"
FINDINGS_STORE_LATEST_POINTER = FINDINGS_STORE_DIR / "latest.json"

# Hooks directory — shipped as package data inside cloud_janitor.hooks
from cloud_janitor.hooks import HOOKS_DIR  # noqa: E402, F401

# Required directories (created at Orchestrator init)
REQUIRED_DIRS = [OUTPUT_DIR, ROLLBACKS_DIR, REMEDIATIONS_DIR, LOGS_DIR, POLICIES_DIR]


def ensure_output_dirs() -> None:
    """Create all required output directories.

    Raises:
        RuntimeError: If any directory cannot be created, with a message
            identifying the directory and the underlying OS error.
    """
    for directory in REQUIRED_DIRS:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as e:
            raise RuntimeError(
                f"Failed to create required directory '{directory}': {e}"
            ) from e


def resolve_latest_findings_store() -> Path | None:
    """Return the current run-scoped Findings Store path, legacy fallback, or None.

    Resolution order:
      1. FINDINGS_STORE_LATEST_POINTER exists and references a valid path → that path
      2. Legacy FINDINGS_STORE_PATH exists → that path (backward-compat fallback)
      3. Neither → None
    """
    if FINDINGS_STORE_LATEST_POINTER.exists():
        try:
            data = json.loads(FINDINGS_STORE_LATEST_POINTER.read_text(encoding="utf-8"))
            candidate = Path(data["path"])
            if candidate.exists():
                return candidate
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    if FINDINGS_STORE_PATH.exists():
        return FINDINGS_STORE_PATH
    return None


def update_findings_store_pointer(run_id: str, path: Path) -> None:
    """Atomically point 'latest.json' at this run's Findings Store file."""
    FINDINGS_STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FINDINGS_STORE_LATEST_POINTER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"run_id": run_id, "path": str(path)}), encoding="utf-8")
    tmp.replace(FINDINGS_STORE_LATEST_POINTER)
