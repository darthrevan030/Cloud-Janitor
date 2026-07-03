"""Unit tests for path configuration and directory creation.

Validates Requirements 4.1, 4.3, 4.5, 4.6:
- ensure_output_dirs() creates all REQUIRED_DIRS when they do not exist
- ensure_output_dirs() raises descriptive error when directory creation fails
- REQUIRED_DIRS contains exactly [OUTPUT_DIR, ROLLBACKS_DIR, LOGS_DIR, POLICIES_DIR]
- UI displays "no data available" message when artifact file is missing
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.paths import (
    LOGS_DIR,
    OUTPUT_DIR,
    POLICIES_DIR,
    REQUIRED_DIRS,
    ROLLBACKS_DIR,
    ensure_output_dirs,
)


class TestRequiredDirsConstant:
    """REQUIRED_DIRS must contain exactly the four expected directories."""

    def test_contains_exactly_four_entries(self) -> None:
        """REQUIRED_DIRS has exactly 4 elements."""
        assert len(REQUIRED_DIRS) == 4

    def test_contains_output_dir(self) -> None:
        """REQUIRED_DIRS includes OUTPUT_DIR."""
        assert OUTPUT_DIR in REQUIRED_DIRS

    def test_contains_rollbacks_dir(self) -> None:
        """REQUIRED_DIRS includes ROLLBACKS_DIR."""
        assert ROLLBACKS_DIR in REQUIRED_DIRS

    def test_contains_logs_dir(self) -> None:
        """REQUIRED_DIRS includes LOGS_DIR."""
        assert LOGS_DIR in REQUIRED_DIRS

    def test_contains_policies_dir(self) -> None:
        """REQUIRED_DIRS includes POLICIES_DIR."""
        assert POLICIES_DIR in REQUIRED_DIRS

    def test_exact_content(self) -> None:
        """REQUIRED_DIRS is exactly [OUTPUT_DIR, ROLLBACKS_DIR, LOGS_DIR, POLICIES_DIR]."""
        expected = [OUTPUT_DIR, ROLLBACKS_DIR, LOGS_DIR, POLICIES_DIR]
        assert REQUIRED_DIRS == expected


class TestEnsureOutputDirsCreation:
    """ensure_output_dirs() creates all REQUIRED_DIRS when they do not exist."""

    def test_creates_all_dirs_from_scratch(self, tmp_path: Path) -> None:
        """All four directories are created when none exist."""
        fake_output = tmp_path / "output"
        fake_rollbacks = fake_output / "rollbacks"
        fake_logs = fake_output / "logs"
        fake_policies = fake_output / "policies"
        fake_dirs = [fake_output, fake_rollbacks, fake_logs, fake_policies]

        # None of these should exist yet
        for d in fake_dirs:
            assert not d.exists()

        with patch("core.paths.REQUIRED_DIRS", fake_dirs):
            ensure_output_dirs()

        # All should now exist
        for d in fake_dirs:
            assert d.is_dir(), f"Expected {d} to be a directory after ensure_output_dirs()"

    def test_creates_nested_dirs_correctly(self, tmp_path: Path) -> None:
        """Subdirectories are created even when parent does not exist yet."""
        fake_output = tmp_path / "deep" / "nested" / "output"
        fake_dirs = [fake_output]

        with patch("core.paths.REQUIRED_DIRS", fake_dirs):
            ensure_output_dirs()

        assert fake_output.is_dir()

    def test_idempotent_on_existing_dirs(self, tmp_path: Path) -> None:
        """Does not fail if directories already exist."""
        existing = tmp_path / "output"
        existing.mkdir()
        assert existing.is_dir()

        with patch("core.paths.REQUIRED_DIRS", [existing]):
            ensure_output_dirs()  # Should not raise

        assert existing.is_dir()


class TestEnsureOutputDirsFailure:
    """ensure_output_dirs() raises descriptive RuntimeError on OS failure."""

    def test_raises_runtime_error_on_os_error(self, tmp_path: Path) -> None:
        """RuntimeError is raised when os.makedirs fails with OSError."""
        bad_dir = tmp_path / "impossible_dir"

        with patch("core.paths.REQUIRED_DIRS", [bad_dir]):
            with patch("os.makedirs", side_effect=OSError("Permission denied")):
                with pytest.raises(RuntimeError):
                    ensure_output_dirs()

    def test_error_message_contains_failed_directory_path(self, tmp_path: Path) -> None:
        """RuntimeError message identifies which directory could not be created."""
        bad_dir = tmp_path / "cannot_create_this"

        with patch("core.paths.REQUIRED_DIRS", [bad_dir]):
            with patch("os.makedirs", side_effect=OSError("No space left on device")):
                with pytest.raises(RuntimeError) as exc_info:
                    ensure_output_dirs()

                error_msg = str(exc_info.value)
                assert str(bad_dir) in error_msg

    def test_error_message_contains_underlying_os_error(self, tmp_path: Path) -> None:
        """RuntimeError message includes the underlying OS error description."""
        bad_dir = tmp_path / "fail"
        os_error_msg = "Read-only file system"

        with patch("core.paths.REQUIRED_DIRS", [bad_dir]):
            with patch("os.makedirs", side_effect=OSError(os_error_msg)):
                with pytest.raises(RuntimeError) as exc_info:
                    ensure_output_dirs()

                error_msg = str(exc_info.value)
                assert os_error_msg in error_msg

    def test_error_chains_original_os_error(self, tmp_path: Path) -> None:
        """RuntimeError.__cause__ is the original OSError for traceback chaining."""
        bad_dir = tmp_path / "fail"
        original = OSError("disk quota exceeded")

        with patch("core.paths.REQUIRED_DIRS", [bad_dir]):
            with patch("os.makedirs", side_effect=original):
                with pytest.raises(RuntimeError) as exc_info:
                    ensure_output_dirs()

                assert exc_info.value.__cause__ is original

    def test_fails_on_first_broken_directory(self, tmp_path: Path) -> None:
        """If the second directory fails, the first was still created."""
        good_dir = tmp_path / "output"
        bad_dir = tmp_path / "output" / "rollbacks"

        call_count = 0
        original_makedirs = __import__("os").makedirs

        def selective_fail(path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("failed on second dir")
            original_makedirs(path, **kwargs)

        with patch("core.paths.REQUIRED_DIRS", [good_dir, bad_dir]):
            with patch("os.makedirs", side_effect=selective_fail):
                with pytest.raises(RuntimeError, match="failed on second dir"):
                    ensure_output_dirs()

        # First directory was created before failure
        assert good_dir.is_dir()


class TestUIDisplaysNoDataMessage:
    """UI displays 'no data available' message when artifact file is missing.

    Validates Requirement 4.6: If the Streamlit_UI attempts to read an artifact
    file that does not yet exist, it SHALL display a message indicating that no
    data is available rather than raising an unhandled error.
    """

    def test_load_findings_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        """load_findings() returns [] when findings_store.json does not exist."""
        from app import load_findings

        nonexistent = tmp_path / "does_not_exist.json"
        with patch("app.FINDINGS_STORE_PATH", nonexistent):
            result = load_findings()

        assert result == []

    def test_load_audit_log_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        """load_audit_log() returns [] when audit.log does not exist."""
        from app import load_audit_log

        nonexistent = tmp_path / "nonexistent_audit.log"
        with patch("app.AUDIT_LOG_PATH", nonexistent):
            result = load_audit_log()

        assert result == []

    def test_load_rollback_hcl_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        """load_rollback_hcl() returns '' when rollback file does not exist."""
        from app import load_rollback_hcl

        fake_rollbacks_dir = tmp_path / "rollbacks"
        fake_rollbacks_dir.mkdir()
        with patch("app.ROLLBACKS_DIR", fake_rollbacks_dir):
            result = load_rollback_hcl("nonexistent-resource")

        assert result == ""

    def test_load_remediation_hcl_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        """load_remediation_hcl() returns '' when remediation.tf does not exist."""
        from app import load_remediation_hcl

        nonexistent = tmp_path / "remediation.tf"
        with patch("app.REMEDIATION_PATH", nonexistent):
            result = load_remediation_hcl()

        assert result == ""

    def test_render_findings_shows_no_data_message_for_empty_list(self) -> None:
        """render_findings_html([]) displays informational 'no data' message."""
        from app import render_findings_html

        result = render_findings_html([])

        # Must contain a user-facing message indicating no data
        assert "No findings" in result or "no data" in result.lower()
        # Must NOT be empty string - user needs visual feedback
        assert len(result) > 0

    def test_load_findings_no_unhandled_error_on_corrupt_json(self, tmp_path: Path) -> None:
        """load_findings() handles corrupt JSON gracefully (no crash)."""
        from app import load_findings

        corrupt_file = tmp_path / "findings_store.json"
        corrupt_file.write_text("{{not valid json", encoding="utf-8")

        with patch("app.FINDINGS_STORE_PATH", corrupt_file):
            result = load_findings()

        # Returns empty list rather than raising
        assert result == []

    def test_load_findings_no_unhandled_error_on_missing_key(self, tmp_path: Path) -> None:
        """load_findings() handles JSON without 'findings' key gracefully."""
        from app import load_findings

        incomplete_file = tmp_path / "findings_store.json"
        incomplete_file.write_text(json.dumps({"schema_version": "1.0.0"}), encoding="utf-8")

        with patch("app.FINDINGS_STORE_PATH", incomplete_file):
            result = load_findings()

        # Returns empty list for missing key, not crash
        assert result == []
