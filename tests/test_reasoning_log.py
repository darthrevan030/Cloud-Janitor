"""Unit tests for reasoning log rotation and append preservation.

Validates Requirements 11.1, 11.2, 11.3:
- Log rotation triggers at 10MB threshold
- Rotation renames current file with timestamp suffix
- Maximum 5 rotated files retained (oldest deleted)
- New run appends separator without destroying existing content
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agents.reasoning_logger import ReasoningLogger


def _create_file_with_size(path: Path, size: int) -> None:
    """Create a sparse file that reports the given size via stat()."""
    path.write_bytes(b"")
    os.truncate(path, size)


class TestLogRotationThreshold:
    """Test log rotation triggers at the 10MB threshold (Req 11.3)."""

    def test_rotation_triggers_when_file_exceeds_10mb(self, tmp_path: Path):
        """File > 10MB should be rotated (renamed) on truncate()."""
        log_file = tmp_path / "agent_reasoning.log"
        _create_file_with_size(log_file, ReasoningLogger.LOG_ROTATION_THRESHOLD + 1)

        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        assert log_file.read_text() == ""
        rotated = list(tmp_path.glob("agent_reasoning.*.log"))
        assert len(rotated) == 1

    def test_no_rotation_when_file_below_10mb(self, tmp_path: Path):
        """File < 10MB should NOT be rotated, just truncated in place."""
        log_file = tmp_path / "agent_reasoning.log"
        log_file.write_text("small content\n")

        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        assert log_file.read_text() == ""
        rotated = list(tmp_path.glob("agent_reasoning.*.log"))
        assert len(rotated) == 0

    def test_no_rotation_when_file_exactly_at_threshold(self, tmp_path: Path):
        """File exactly at threshold should NOT be rotated (> not >=)."""
        log_file = tmp_path / "agent_reasoning.log"
        _create_file_with_size(log_file, ReasoningLogger.LOG_ROTATION_THRESHOLD)

        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        assert log_file.read_text() == ""
        rotated = list(tmp_path.glob("agent_reasoning.*.log"))
        assert len(rotated) == 0

    def test_threshold_constant_is_10mb(self):
        """LOG_ROTATION_THRESHOLD is exactly 10 * 1024 * 1024 bytes."""
        assert ReasoningLogger.LOG_ROTATION_THRESHOLD == 10 * 1024 * 1024
        assert ReasoningLogger.LOG_ROTATION_THRESHOLD == 10_485_760

    def test_rotation_does_not_occur_for_nonexistent_file(self, tmp_path: Path):
        """If the log file doesn't exist, truncate just creates empty file."""
        log_file = tmp_path / "agent_reasoning.log"
        assert not log_file.exists()

        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        assert log_file.exists()
        assert log_file.read_text() == ""
        rotated = list(tmp_path.glob("agent_reasoning.*.log"))
        assert len(rotated) == 0


class TestLogRotationRenaming:
    """Test that rotation renames current file with timestamp suffix (Req 11.3)."""

    def test_rotated_file_has_timestamp_suffix(self, tmp_path: Path):
        """Rotated file should be named agent_reasoning.<timestamp>.log."""
        log_file = tmp_path / "agent_reasoning.log"
        _create_file_with_size(log_file, ReasoningLogger.LOG_ROTATION_THRESHOLD + 1)

        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        rotated = list(tmp_path.glob("agent_reasoning.*.log"))
        assert len(rotated) == 1
        name = rotated[0].name
        assert name.startswith("agent_reasoning.")
        assert name.endswith(".log")
        # Extract the timestamp portion
        ts_part = name.removeprefix("agent_reasoning.").removesuffix(".log")
        # Format: YYYYMMDDTHHMMSSz (16 chars)
        assert len(ts_part) == 16
        assert ts_part[8] == "T"
        assert ts_part[15] == "Z"

    def test_rotated_file_preserves_original_content(self, tmp_path: Path):
        """The rotated file should contain the original log content."""
        log_file = tmp_path / "agent_reasoning.log"
        original = '{"event_type":"check","agent":"finops"}\n'
        log_file.write_text(original)
        # Pad file to exceed threshold using seek
        with open(log_file, "r+b") as f:
            f.seek(ReasoningLogger.LOG_ROTATION_THRESHOLD)
            f.write(b"\x00")

        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        rotated = list(tmp_path.glob("agent_reasoning.*.log"))
        assert len(rotated) == 1
        content = rotated[0].read_text(errors="replace")
        assert content.startswith(original)

    def test_active_log_is_empty_after_rotation(self, tmp_path: Path):
        """After rotation, the active log file is freshly created and empty."""
        log_file = tmp_path / "agent_reasoning.log"
        _create_file_with_size(log_file, ReasoningLogger.LOG_ROTATION_THRESHOLD + 100)

        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        assert log_file.exists()
        assert log_file.read_text() == ""


class TestLogRotationMaxFiles:
    """Test that maximum 5 rotated files are retained (Req 11.3)."""

    def test_max_history_files_constant_is_5(self):
        """MAX_HISTORY_FILES is exactly 5."""
        assert ReasoningLogger.MAX_HISTORY_FILES == 5

    def test_oldest_rotated_files_deleted_when_exceeding_max(self, tmp_path: Path):
        """When more than 5 rotated files exist, oldest are deleted."""
        log_file = tmp_path / "agent_reasoning.log"

        timestamps = [
            "20250101T000000Z",
            "20250102T000000Z",
            "20250103T000000Z",
            "20250104T000000Z",
            "20250105T000000Z",
            "20250106T000000Z",
        ]
        for ts in timestamps:
            (tmp_path / f"agent_reasoning.{ts}.log").write_text(f"data {ts}\n")

        _create_file_with_size(log_file, ReasoningLogger.LOG_ROTATION_THRESHOLD + 1)

        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        rotated = sorted(tmp_path.glob("agent_reasoning.*.log"))
        assert len(rotated) <= ReasoningLogger.MAX_HISTORY_FILES

    def test_newest_rotated_files_are_retained(self, tmp_path: Path):
        """Pruning should delete the OLDEST files, keeping newest ones."""
        log_file = tmp_path / "agent_reasoning.log"

        timestamps = [
            "20250101T000000Z",
            "20250102T000000Z",
            "20250103T000000Z",
            "20250104T000000Z",
            "20250105T000000Z",
            "20250106T000000Z",
        ]
        for ts in timestamps:
            (tmp_path / f"agent_reasoning.{ts}.log").write_text(f"data {ts}\n")

        _create_file_with_size(log_file, ReasoningLogger.LOG_ROTATION_THRESHOLD + 1)
        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        rotated = sorted(tmp_path.glob("agent_reasoning.*.log"))
        rotated_names = [f.name for f in rotated]

        # Oldest should be gone
        assert "agent_reasoning.20250101T000000Z.log" not in rotated_names
        # Newest should remain
        assert "agent_reasoning.20250106T000000Z.log" in rotated_names

    def test_exactly_5_rotated_files_no_deletion(self, tmp_path: Path):
        """When exactly MAX_HISTORY_FILES exist after rotation, none deleted."""
        log_file = tmp_path / "agent_reasoning.log"

        # 4 pre-existing + 1 from rotation = 5 total
        timestamps = [
            "20250101T000000Z",
            "20250102T000000Z",
            "20250103T000000Z",
            "20250104T000000Z",
        ]
        for ts in timestamps:
            (tmp_path / f"agent_reasoning.{ts}.log").write_text(f"data {ts}\n")

        _create_file_with_size(log_file, ReasoningLogger.LOG_ROTATION_THRESHOLD + 1)
        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        rotated = sorted(tmp_path.glob("agent_reasoning.*.log"))
        assert len(rotated) == 5
        for ts in timestamps:
            assert (tmp_path / f"agent_reasoning.{ts}.log").exists()

    def test_zero_rotated_files_works(self, tmp_path: Path):
        """Rotation works fine when no prior rotated files exist."""
        log_file = tmp_path / "agent_reasoning.log"
        _create_file_with_size(log_file, ReasoningLogger.LOG_ROTATION_THRESHOLD + 1)

        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        rotated = list(tmp_path.glob("agent_reasoning.*.log"))
        assert len(rotated) == 1


class TestAppendPreservation:
    """Test new run appends separator without destroying content (Req 11.1, 11.2)."""

    def test_start_run_appends_without_overwriting(self, tmp_path: Path):
        """start_run() must not destroy previously written entries."""
        log_file = tmp_path / "agent_reasoning.log"
        existing = '{"event_type":"check","agent":"finops","resource_id":"r1","message":"old"}\n'
        log_file.write_text(existing)

        logger = ReasoningLogger(log_path=log_file)
        logger.start_run()

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event_type"] == "check"
        assert json.loads(lines[0])["message"] == "old"
        assert json.loads(lines[1])["event_type"] == "run_separator"

    def test_start_run_separator_has_required_fields(self, tmp_path: Path):
        """Separator entry must have event_type, timestamp, message (Req 11.2)."""
        log_file = tmp_path / "agent_reasoning.log"
        logger = ReasoningLogger(log_path=log_file)
        logger.start_run()

        entry = json.loads(log_file.read_text().strip())
        assert entry["event_type"] == "run_separator"
        assert "timestamp" in entry
        assert "+00:00" in entry["timestamp"]
        assert entry["message"] == "New audit run started"
        assert set(entry.keys()) == {"event_type", "timestamp", "message"}

    def test_multiple_runs_accumulate_separators(self, tmp_path: Path):
        """Multiple start_run() calls create multiple separators, all preserved."""
        log_file = tmp_path / "agent_reasoning.log"
        logger = ReasoningLogger(log_path=log_file)

        logger.start_run()
        logger.emit("agent1", "check", "r1", "first run work")
        logger.start_run()
        logger.emit("agent2", "finding", "r2", "second run work")

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 4
        assert json.loads(lines[0])["event_type"] == "run_separator"
        assert json.loads(lines[1])["event_type"] == "check"
        assert json.loads(lines[2])["event_type"] == "run_separator"
        assert json.loads(lines[3])["event_type"] == "finding"

    def test_start_run_creates_file_if_missing(self, tmp_path: Path):
        """If file doesn't exist, start_run creates it (no error)."""
        log_file = tmp_path / "agent_reasoning.log"
        assert not log_file.exists()

        logger = ReasoningLogger(log_path=log_file)
        logger.start_run()

        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["event_type"] == "run_separator"

    def test_emit_after_start_run_appends_correctly(self, tmp_path: Path):
        """Entries emitted after start_run are appended, not overwritten."""
        log_file = tmp_path / "agent_reasoning.log"
        log_file.write_text(
            '{"event_type":"finding","agent":"sec","resource_id":"sg-1","message":"open port"}\n'
        )

        logger = ReasoningLogger(log_path=log_file)
        logger.start_run()
        logger.emit("finops", "check", "ebs-1", "checking idle")

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["event_type"] == "finding"
        assert json.loads(lines[1])["event_type"] == "run_separator"
        assert json.loads(lines[2])["event_type"] == "check"
        assert json.loads(lines[2])["resource_id"] == "ebs-1"

    def test_truncate_below_threshold_does_not_rotate(self, tmp_path: Path):
        """truncate() below threshold doesn't move content to rotated file."""
        log_file = tmp_path / "agent_reasoning.log"
        log_file.write_text("small content\n")

        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        assert log_file.read_text() == ""
        rotated = list(tmp_path.glob("agent_reasoning.*.log"))
        assert len(rotated) == 0
