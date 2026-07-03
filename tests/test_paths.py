"""Tests for core/paths.py centralized path configuration."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.paths import (
    APPROVAL_GATES_PATH,
    AUDIT_LOG_PATH,
    FINDINGS_STORE_PATH,
    HOOKS_DIR,
    LOGS_DIR,
    OUTPUT_DIR,
    POLICIES_DIR,
    PROJECT_ROOT,
    REASONING_LOG_PATH,
    REQUIRED_DIRS,
    ROLLBACKS_DIR,
    SAVINGS_LEDGER_PATH,
    ensure_output_dirs,
)


class TestPathConstants:
    """Tests for path constant definitions."""

    def test_project_root_is_absolute(self) -> None:
        """PROJECT_ROOT must be an absolute path."""
        assert PROJECT_ROOT.is_absolute()

    def test_project_root_points_to_actual_project(self) -> None:
        """PROJECT_ROOT should contain recognizable project files."""
        # The project root should contain orchestrator.py or pyproject.toml
        assert (PROJECT_ROOT / "pyproject.toml").exists()

    def test_output_dir_is_under_project_root(self) -> None:
        """OUTPUT_DIR must be a child of PROJECT_ROOT."""
        assert OUTPUT_DIR == PROJECT_ROOT / "output"

    def test_rollbacks_dir_is_under_output(self) -> None:
        """ROLLBACKS_DIR must be output/rollbacks."""
        assert ROLLBACKS_DIR == OUTPUT_DIR / "rollbacks"

    def test_logs_dir_is_under_output(self) -> None:
        """LOGS_DIR must be output/logs."""
        assert LOGS_DIR == OUTPUT_DIR / "logs"

    def test_policies_dir_is_under_output(self) -> None:
        """POLICIES_DIR must be output/policies."""
        assert POLICIES_DIR == OUTPUT_DIR / "policies"

    def test_findings_store_path(self) -> None:
        """FINDINGS_STORE_PATH must be output/findings_store.json."""
        assert FINDINGS_STORE_PATH == OUTPUT_DIR / "findings_store.json"

    def test_audit_log_path(self) -> None:
        """AUDIT_LOG_PATH must be output/logs/audit.log."""
        assert AUDIT_LOG_PATH == LOGS_DIR / "audit.log"

    def test_reasoning_log_path(self) -> None:
        """REASONING_LOG_PATH must be output/logs/agent_reasoning.log."""
        assert REASONING_LOG_PATH == LOGS_DIR / "agent_reasoning.log"

    def test_approval_gates_path(self) -> None:
        """APPROVAL_GATES_PATH must be output/approval_gates.json."""
        assert APPROVAL_GATES_PATH == OUTPUT_DIR / "approval_gates.json"

    def test_savings_ledger_path(self) -> None:
        """SAVINGS_LEDGER_PATH must be output/savings_ledger.json."""
        assert SAVINGS_LEDGER_PATH == OUTPUT_DIR / "savings_ledger.json"

    def test_hooks_dir_is_under_project_root(self) -> None:
        """HOOKS_DIR must be hooks/ under PROJECT_ROOT."""
        assert HOOKS_DIR == PROJECT_ROOT / "hooks"

    def test_required_dirs_contains_all_output_subdirs(self) -> None:
        """REQUIRED_DIRS must include OUTPUT_DIR and its three subdirectories."""
        assert OUTPUT_DIR in REQUIRED_DIRS
        assert ROLLBACKS_DIR in REQUIRED_DIRS
        assert LOGS_DIR in REQUIRED_DIRS
        assert POLICIES_DIR in REQUIRED_DIRS
        assert len(REQUIRED_DIRS) == 4

    def test_all_paths_are_path_objects(self) -> None:
        """All path constants must be pathlib.Path instances."""
        paths = [
            PROJECT_ROOT,
            OUTPUT_DIR,
            ROLLBACKS_DIR,
            LOGS_DIR,
            POLICIES_DIR,
            FINDINGS_STORE_PATH,
            AUDIT_LOG_PATH,
            REASONING_LOG_PATH,
            APPROVAL_GATES_PATH,
            SAVINGS_LEDGER_PATH,
            HOOKS_DIR,
        ]
        for p in paths:
            assert isinstance(p, Path), f"{p} is not a Path instance"


class TestEnsureOutputDirs:
    """Tests for the ensure_output_dirs() helper function."""

    def test_creates_all_required_directories(self, tmp_path: Path) -> None:
        """ensure_output_dirs() creates all directories in REQUIRED_DIRS."""
        fake_dirs = [
            tmp_path / "output",
            tmp_path / "output" / "rollbacks",
            tmp_path / "output" / "logs",
            tmp_path / "output" / "policies",
        ]
        with patch("core.paths.REQUIRED_DIRS", fake_dirs):
            ensure_output_dirs()

        for d in fake_dirs:
            assert d.is_dir()

    def test_idempotent_when_dirs_exist(self, tmp_path: Path) -> None:
        """ensure_output_dirs() succeeds even if directories already exist."""
        fake_dirs = [tmp_path / "output"]
        fake_dirs[0].mkdir()

        with patch("core.paths.REQUIRED_DIRS", fake_dirs):
            # Should not raise
            ensure_output_dirs()

        assert fake_dirs[0].is_dir()

    def test_raises_runtime_error_on_os_failure(self, tmp_path: Path) -> None:
        """ensure_output_dirs() raises RuntimeError identifying the failed directory."""
        bad_dir = tmp_path / "nonexistent" / "deep" / "path"

        with patch("core.paths.REQUIRED_DIRS", [bad_dir]):
            with patch("os.makedirs", side_effect=OSError("Permission denied")):
                with pytest.raises(RuntimeError, match="Permission denied"):
                    ensure_output_dirs()

    def test_runtime_error_identifies_failed_directory(self, tmp_path: Path) -> None:
        """RuntimeError message must contain the path that failed."""
        bad_dir = tmp_path / "cannot_create"

        with patch("core.paths.REQUIRED_DIRS", [bad_dir]):
            with patch("os.makedirs", side_effect=OSError("disk full")):
                with pytest.raises(RuntimeError) as exc_info:
                    ensure_output_dirs()

                assert str(bad_dir) in str(exc_info.value)

    def test_runtime_error_wraps_original_os_error(self, tmp_path: Path) -> None:
        """RuntimeError.__cause__ must be the original OSError."""
        bad_dir = tmp_path / "fail"
        original_error = OSError("read-only filesystem")

        with patch("core.paths.REQUIRED_DIRS", [bad_dir]):
            with patch("os.makedirs", side_effect=original_error):
                with pytest.raises(RuntimeError) as exc_info:
                    ensure_output_dirs()

                assert exc_info.value.__cause__ is original_error
