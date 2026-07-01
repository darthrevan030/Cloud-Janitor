"""Smoke tests for bin/tflocal wrapper script.

Validates Requirement 2.6: bin/tflocal dry-run and self-skip behavior.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = str(PROJECT_ROOT / "bin" / "tflocal").replace("\\", "/")

# On Windows, prefer Git Bash
GIT_BASH = Path(r"C:\Program Files\Git\usr\bin\bash.exe")
BASH = str(GIT_BASH) if GIT_BASH.exists() else shutil.which("bash") or "bash"


def run_wrapper(args: list[str], env: dict) -> subprocess.CompletedProcess:
    """Run the tflocal wrapper with given args and a fully explicit env dict."""
    return subprocess.run(
        [BASH, WRAPPER] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
        cwd=str(PROJECT_ROOT),
    )


class TestDryRunValidate:
    """JANITOR_DRY_RUN=1 bin/tflocal validate exits 0 with [DRY RUN] in stdout."""

    def test_dry_run_validate_exits_zero(self):
        env = {"JANITOR_DRY_RUN": "1", "PATH": "/usr/bin:/bin"}
        result = run_wrapper(["validate"], env=env)
        assert result.returncode == 0

    def test_dry_run_validate_stdout_contains_dry_run_marker(self):
        env = {"JANITOR_DRY_RUN": "1", "PATH": "/usr/bin:/bin"}
        result = run_wrapper(["validate"], env=env)
        assert "[DRY RUN]" in result.stdout

    def test_dry_run_validate_stdout_contains_command(self):
        env = {"JANITOR_DRY_RUN": "1", "PATH": "/usr/bin:/bin"}
        result = run_wrapper(["validate"], env=env)
        assert "tflocal validate" in result.stdout


class TestDryRunApply:
    """JANITOR_DRY_RUN=1 bin/tflocal apply -auto-approve exits 0 with full command string."""

    def test_dry_run_apply_exits_zero(self):
        env = {"JANITOR_DRY_RUN": "1", "PATH": "/usr/bin:/bin"}
        result = run_wrapper(["apply", "-auto-approve"], env=env)
        assert result.returncode == 0

    def test_dry_run_apply_stdout_contains_full_command(self):
        env = {"JANITOR_DRY_RUN": "1", "PATH": "/usr/bin:/bin"}
        result = run_wrapper(["apply", "-auto-approve"], env=env)
        assert "[DRY RUN] Would execute: tflocal apply -auto-approve" in result.stdout


class TestSelfSkipNoRecursion:
    """Self-skip logic: with only bin/ on PATH, wrapper must not infinitely recurse."""

    def test_no_recursion_exits_with_error(self):
        """When PATH contains only bin/ (plus basic utils), wrapper should exit 1 gracefully."""
        bin_dir = str(PROJECT_ROOT / "bin").replace("\\", "/")
        # Include /usr/bin and /bin so bash builtins like `which` work
        env = {"JANITOR_DRY_RUN": "0", "PATH": f"{bin_dir}:/usr/bin:/bin"}
        result = run_wrapper(["plan"], env=env)
        assert result.returncode == 1, (
            f"Expected exit 1 (graceful error), got {result.returncode}. "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )

    def test_no_recursion_prints_error_message(self):
        """Error message should indicate neither tflocal nor terraform was found."""
        bin_dir = str(PROJECT_ROOT / "bin").replace("\\", "/")
        env = {"JANITOR_DRY_RUN": "0", "PATH": f"{bin_dir}:/usr/bin:/bin"}
        result = run_wrapper(["plan"], env=env)
        assert "ERROR: Neither tflocal nor terraform found on PATH" in result.stderr

    def test_no_recursion_completes_within_timeout(self):
        """If the wrapper recurses, it will hang and hit the 10s timeout — this must not happen."""
        bin_dir = str(PROJECT_ROOT / "bin").replace("\\", "/")
        env = {"JANITOR_DRY_RUN": "0", "PATH": f"{bin_dir}:/usr/bin:/bin"}
        # subprocess.run with timeout=10 is set in run_wrapper.
        # If this test passes at all, the wrapper didn't loop.
        result = run_wrapper(["plan"], env=env)
        # Any completion (pass or fail) proves no infinite loop
        assert result.returncode in (0, 1)
