"""Tests for bin/tflocal wrapper script.

Validates Requirement 2.6: bin/tflocal dry-run and delegation behavior.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = PROJECT_ROOT / "bin" / "tflocal"

# On Windows, prefer Git Bash over WSL bash
GIT_BASH = Path(r"C:\Program Files\Git\usr\bin\bash.exe")
BASH = str(GIT_BASH) if GIT_BASH.exists() else shutil.which("bash") or "bash"


def run_wrapper(args: list[str], env_override: dict | None = None) -> subprocess.CompletedProcess:
    """Run the tflocal wrapper with given args and env overrides."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [BASH, str(WRAPPER)] + args,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PROJECT_ROOT),
    )


class TestDryRunMode:
    """Tests for JANITOR_DRY_RUN=1 behavior."""

    def test_dry_run_prints_message_and_exits_zero(self):
        result = run_wrapper(["plan"], env_override={"JANITOR_DRY_RUN": "1"})
        assert result.returncode == 0
        assert "[DRY RUN] Would execute: tflocal plan" in result.stdout

    def test_dry_run_includes_all_arguments(self):
        result = run_wrapper(
            ["apply", "-auto-approve", "-var=region=us-east-1"],
            env_override={"JANITOR_DRY_RUN": "1"},
        )
        assert result.returncode == 0
        assert "[DRY RUN] Would execute: tflocal apply -auto-approve -var=region=us-east-1" in result.stdout

    def test_dry_run_with_no_arguments(self):
        result = run_wrapper([], env_override={"JANITOR_DRY_RUN": "1"})
        assert result.returncode == 0
        assert "[DRY RUN] Would execute: tflocal" in result.stdout

    def test_dry_run_zero_value_does_not_trigger(self):
        """JANITOR_DRY_RUN=0 should NOT trigger dry-run mode."""
        # With a restricted PATH, this should fail with the error message
        # (proving it didn't short-circuit into dry-run mode)
        env = {"JANITOR_DRY_RUN": "0", "PATH": "/nonexistent:/usr/bin:/bin"}
        result = run_wrapper(["plan"], env_override=env)
        assert result.returncode == 1
        assert "ERROR: Neither tflocal nor terraform found on PATH" in result.stderr

    def test_dry_run_unset_does_not_trigger(self):
        """Unset JANITOR_DRY_RUN should NOT trigger dry-run mode."""
        env = os.environ.copy()
        env.pop("JANITOR_DRY_RUN", None)
        env["PATH"] = "/nonexistent:/usr/bin:/bin"
        result = subprocess.run(
            [BASH, str(WRAPPER), "plan"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 1
        assert "ERROR: Neither tflocal nor terraform found on PATH" in result.stderr


class TestErrorHandling:
    """Tests for error conditions when no binary is found."""

    def test_error_when_no_binary_found(self):
        """Should print error to stderr and exit 1 when neither tflocal nor terraform is on PATH."""
        env = {"JANITOR_DRY_RUN": "0", "PATH": "/nonexistent:/usr/bin:/bin"}
        result = run_wrapper(["plan"], env_override=env)
        assert result.returncode == 1
        assert "ERROR: Neither tflocal nor terraform found on PATH" in result.stderr


class TestScriptProperties:
    """Tests for script file properties."""

    def test_script_exists(self):
        assert WRAPPER.exists(), f"Expected wrapper at {WRAPPER}"

    def test_script_has_shebang(self):
        content = WRAPPER.read_text()
        assert content.startswith("#!/usr/bin/env bash")

    def test_script_uses_strict_mode(self):
        content = WRAPPER.read_text()
        assert "set -euo pipefail" in content
