"""Unit tests for agents.reasoning_logger.ReasoningLogger."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agents.reasoning_logger import ReasoningLogger


class TestReasoningLoggerInit:
    """Tests for ReasoningLogger initialization."""

    def test_default_log_path(self):
        logger = ReasoningLogger()
        expected = Path(__file__).resolve().parent.parent / "output" / "logs" / "agent_reasoning.log"
        assert logger.log_path == expected

    def test_custom_log_path(self, tmp_path: Path):
        custom = tmp_path / "custom.log"
        logger = ReasoningLogger(log_path=custom)
        assert logger.log_path == custom


class TestReasoningLoggerStartRun:
    """Tests for start_run() method (Req 11.1, 11.2, 11.4)."""

    def test_start_run_writes_run_separator_entry(self, tmp_path: Path):
        log_file = tmp_path / "reasoning.log"
        logger = ReasoningLogger(log_path=log_file)
        logger.start_run()

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event_type"] == "run_separator"
        assert entry["message"] == "New audit run started"
        assert "timestamp" in entry
        assert "+00:00" in entry["timestamp"]

    def test_start_run_creates_file_if_missing(self, tmp_path: Path):
        """Req 11.4: file is created if it does not exist."""
        log_file = tmp_path / "new_run.log"
        assert not log_file.exists()
        logger = ReasoningLogger(log_path=log_file)
        logger.start_run()
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["event_type"] == "run_separator"

    def test_start_run_preserves_existing_content(self, tmp_path: Path):
        """Req 11.1: append mode preserves previously written entries."""
        log_file = tmp_path / "reasoning.log"
        log_file.write_text('{"event_type":"check","agent":"x","resource_id":"","message":"old"}\n')
        logger = ReasoningLogger(log_path=log_file)
        logger.start_run()

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2
        # First line is the original content
        assert json.loads(lines[0])["event_type"] == "check"
        # Second line is the separator
        assert json.loads(lines[1])["event_type"] == "run_separator"

    def test_start_run_entry_has_exactly_three_fields(self, tmp_path: Path):
        """Req 11.2: separator entry has event_type, timestamp, message."""
        log_file = tmp_path / "reasoning.log"
        logger = ReasoningLogger(log_path=log_file)
        logger.start_run()

        entry = json.loads(log_file.read_text().strip())
        assert set(entry.keys()) == {"event_type", "timestamp", "message"}

    def test_start_run_on_filesystem_error_prints_stderr(self, tmp_path: Path, capsys):
        bad_path = tmp_path / "nonexistent_dir" / "sub" / "reasoning.log"
        logger = ReasoningLogger(log_path=bad_path)
        logger.start_run()
        captured = capsys.readouterr()
        assert "ReasoningLogger: failed to write separator" in captured.err

    def test_start_run_does_not_raise_on_filesystem_error(self, tmp_path: Path):
        bad_path = tmp_path / "nonexistent_dir" / "sub" / "reasoning.log"
        logger = ReasoningLogger(log_path=bad_path)
        # Should not raise
        logger.start_run()

    def test_start_run_multiple_calls_append_multiple_separators(self, tmp_path: Path):
        """Multiple start_run() calls produce multiple separator lines."""
        log_file = tmp_path / "reasoning.log"
        logger = ReasoningLogger(log_path=log_file)
        logger.start_run()
        logger.emit("agent", "check", "res", "doing work")
        logger.start_run()

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["event_type"] == "run_separator"
        assert json.loads(lines[1])["event_type"] == "check"
        assert json.loads(lines[2])["event_type"] == "run_separator"


class TestReasoningLoggerTruncate:
    """Tests for truncate() method."""

    def test_truncate_clears_file(self, tmp_path: Path):
        log_file = tmp_path / "reasoning.log"
        log_file.write_text("existing content\n")
        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()
        assert log_file.read_text() == ""

    def test_truncate_creates_file_if_missing(self, tmp_path: Path):
        log_file = tmp_path / "new.log"
        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()
        assert log_file.exists()
        assert log_file.read_text() == ""

    def test_truncate_on_permission_error_prints_stderr(self, tmp_path: Path, capsys):
        # Use an invalid path to trigger an OSError
        bad_path = tmp_path / "nonexistent_dir" / "sub" / "reasoning.log"
        logger = ReasoningLogger(log_path=bad_path)
        logger.truncate()
        captured = capsys.readouterr()
        assert "ReasoningLogger: failed to truncate" in captured.err


class TestReasoningLoggerEmit:
    """Tests for emit() method."""

    def test_emit_appends_valid_json_line(self, tmp_path: Path):
        log_file = tmp_path / "reasoning.log"
        logger = ReasoningLogger(log_path=log_file)
        logger.emit("finops_auditor", "check", "cache-01", "Checking idle duration")

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["agent"] == "finops_auditor"
        assert entry["event_type"] == "check"
        assert entry["resource_id"] == "cache-01"
        assert entry["message"] == "Checking idle duration"
        assert "timestamp" in entry

    def test_emit_timestamp_is_utc_iso8601(self, tmp_path: Path):
        log_file = tmp_path / "reasoning.log"
        logger = ReasoningLogger(log_path=log_file)
        logger.emit("agent", "finding", "res-1", "msg")

        entry = json.loads(log_file.read_text().strip())
        ts = entry["timestamp"]
        # Should end with +00:00 (UTC)
        assert "+00:00" in ts

    def test_emit_truncates_agent_to_64_chars(self, tmp_path: Path):
        log_file = tmp_path / "reasoning.log"
        logger = ReasoningLogger(log_path=log_file)
        long_agent = "a" * 100
        logger.emit(long_agent, "check", "", "msg")

        entry = json.loads(log_file.read_text().strip())
        assert len(entry["agent"]) == 64

    def test_emit_truncates_message_to_500_chars(self, tmp_path: Path):
        log_file = tmp_path / "reasoning.log"
        logger = ReasoningLogger(log_path=log_file)
        long_msg = "x" * 1000
        logger.emit("agent", "decision", "", long_msg)

        entry = json.loads(log_file.read_text().strip())
        assert len(entry["message"]) == 500

    def test_emit_invalid_event_type_uses_unknown(self, tmp_path: Path):
        log_file = tmp_path / "reasoning.log"
        logger = ReasoningLogger(log_path=log_file)
        logger.emit("agent", "invalid_type", "res", "msg")

        entry = json.loads(log_file.read_text().strip())
        assert entry["event_type"] == "unknown"

    def test_emit_all_valid_event_types(self, tmp_path: Path):
        log_file = tmp_path / "reasoning.log"
        logger = ReasoningLogger(log_path=log_file)
        for et in ReasoningLogger.VALID_EVENT_TYPES:
            logger.emit("agent", et, "", "msg")

        lines = log_file.read_text().strip().splitlines()
        event_types = {json.loads(line)["event_type"] for line in lines}
        assert event_types == ReasoningLogger.VALID_EVENT_TYPES

    def test_emit_sequential_append(self, tmp_path: Path):
        log_file = tmp_path / "reasoning.log"
        logger = ReasoningLogger(log_path=log_file)
        logger.emit("agent1", "check", "r1", "first")
        logger.emit("agent2", "finding", "r2", "second")
        logger.emit("agent3", "handoff", "r3", "third")

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["message"] == "first"
        assert json.loads(lines[1])["message"] == "second"
        assert json.loads(lines[2])["message"] == "third"

    def test_emit_on_filesystem_error_prints_stderr(self, tmp_path: Path, capsys):
        bad_path = tmp_path / "no_dir" / "sub" / "reasoning.log"
        logger = ReasoningLogger(log_path=bad_path)
        logger.emit("agent", "check", "", "msg")
        captured = capsys.readouterr()
        assert "ReasoningLogger: failed to write" in captured.err

    def test_emit_does_not_raise_on_filesystem_error(self, tmp_path: Path):
        bad_path = tmp_path / "no_dir" / "sub" / "reasoning.log"
        logger = ReasoningLogger(log_path=bad_path)
        # Should not raise
        logger.emit("agent", "check", "", "msg")

    def test_emit_empty_resource_id(self, tmp_path: Path):
        log_file = tmp_path / "reasoning.log"
        logger = ReasoningLogger(log_path=log_file)
        logger.emit("agent", "skip", "", "No resource")

        entry = json.loads(log_file.read_text().strip())
        assert entry["resource_id"] == ""
