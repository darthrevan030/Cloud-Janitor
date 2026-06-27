"""Tests for the append-only audit log writer."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from agents.audit_logger import AuditLogger


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    """Provide a temporary audit log file path."""
    return tmp_path / "audit.log"


@pytest.fixture
def logger(log_path: Path) -> AuditLogger:
    """Provide an AuditLogger instance with a temporary path."""
    return AuditLogger(log_path)


def _sample_entry(resource_id: str = "vol-abc123") -> dict:
    """Create a sample audit entry."""
    return {
        "timestamp": "2025-01-15T10:30:00+00:00",
        "resource_id": resource_id,
        "actor": "admin",
        "action": "approval",
        "result": "success",
        "details": "Approved by admin",
    }


class TestAppend:
    """Tests for AuditLogger.append()."""

    def test_append_creates_file_and_writes_json_line(
        self, logger: AuditLogger, log_path: Path
    ) -> None:
        """A single append creates the file and writes valid JSON."""
        entry = _sample_entry()
        result = logger.append(entry)

        assert result is True
        assert log_path.exists()

        content = log_path.read_text(encoding="utf-8")
        parsed = json.loads(content.strip())
        assert parsed == entry

    def test_append_multiple_entries_creates_multiple_lines(
        self, logger: AuditLogger, log_path: Path
    ) -> None:
        """Multiple appends produce multiple JSON lines."""
        entries = [_sample_entry(f"res-{i}") for i in range(3)]
        for entry in entries:
            logger.append(entry)

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

        for i, line in enumerate(lines):
            parsed = json.loads(line)
            assert parsed["resource_id"] == f"res-{i}"

    def test_append_returns_true_on_success(self, logger: AuditLogger) -> None:
        """append() returns True when the write succeeds."""
        assert logger.append(_sample_entry()) is True

    def test_append_returns_false_on_unwritable_path(self, tmp_path: Path) -> None:
        """append() returns False gracefully when the path is unwritable."""
        # Use a directory as the "file" path — can't open a dir for writing
        bad_path = tmp_path / "no_such_dir" / "nested" / "deep" / "audit.log"
        # Don't create parent dirs — this should fail
        logger = AuditLogger(bad_path)
        result = logger.append(_sample_entry())
        assert result is False

    def test_append_handles_non_serializable_data(
        self, logger: AuditLogger
    ) -> None:
        """append() returns False for non-JSON-serializable data."""
        bad_entry = {"timestamp": object()}  # type: ignore[dict-item]
        result = logger.append(bad_entry)
        assert result is False


class TestAppendOnlySemantics:
    """Tests that the log file is append-only (never shrinks)."""

    def test_file_size_only_grows(
        self, logger: AuditLogger, log_path: Path
    ) -> None:
        """File size must monotonically increase with each append."""
        sizes: list[int] = []
        for i in range(5):
            logger.append(_sample_entry(f"res-{i}"))
            sizes.append(log_path.stat().st_size)

        # Each size must be strictly greater than the previous
        for i in range(1, len(sizes)):
            assert sizes[i] > sizes[i - 1]

    def test_existing_content_preserved_after_append(
        self, logger: AuditLogger, log_path: Path
    ) -> None:
        """Existing entries are never overwritten by subsequent appends."""
        logger.append(_sample_entry("first"))
        first_content = log_path.read_text(encoding="utf-8")

        logger.append(_sample_entry("second"))
        full_content = log_path.read_text(encoding="utf-8")

        # The original content must be a prefix of the full content
        assert full_content.startswith(first_content)


class TestReadAll:
    """Tests for AuditLogger.read_all()."""

    def test_read_all_returns_empty_list_when_no_file(
        self, logger: AuditLogger
    ) -> None:
        """read_all() returns [] if the log file doesn't exist."""
        assert logger.read_all() == []

    def test_read_all_returns_all_entries(
        self, logger: AuditLogger
    ) -> None:
        """read_all() returns all previously appended entries."""
        entries = [_sample_entry(f"res-{i}") for i in range(4)]
        for entry in entries:
            logger.append(entry)

        result = logger.read_all()
        assert len(result) == 4
        assert result[0]["resource_id"] == "res-0"
        assert result[3]["resource_id"] == "res-3"

    def test_read_all_skips_malformed_lines(
        self, logger: AuditLogger, log_path: Path
    ) -> None:
        """read_all() skips lines that aren't valid JSON."""
        logger.append(_sample_entry("good-1"))
        # Manually inject a bad line
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("not valid json\n")
        logger.append(_sample_entry("good-2"))

        result = logger.read_all()
        assert len(result) == 2
        assert result[0]["resource_id"] == "good-1"
        assert result[1]["resource_id"] == "good-2"

    def test_read_all_returns_empty_on_unreadable_file(
        self, tmp_path: Path
    ) -> None:
        """read_all() returns [] if the file cannot be read."""
        # Point to a directory — can't read as a file
        logger = AuditLogger(tmp_path)
        # tmp_path is a directory, exists() returns True but open for read fails
        result = logger.read_all()
        assert result == []


class TestLogPath:
    """Tests for the log_path property."""

    def test_log_path_property(self, log_path: Path) -> None:
        """log_path property returns the configured path."""
        logger = AuditLogger(log_path)
        assert logger.log_path == log_path
