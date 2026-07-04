"""Centralized path configuration for Cloud Janitor runtime artifacts.

All artifact paths are constructed here. No module should build
artifact paths via string literals outside this module.
"""

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

# Hooks directory
HOOKS_DIR = PROJECT_ROOT / "hooks"

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
