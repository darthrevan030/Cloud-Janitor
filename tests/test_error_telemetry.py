"""Tests for core.error_telemetry module.

Validates Requirements 12.1, 12.2, 12.3.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.error_telemetry import (
    ERROR_CATEGORIES,
    build_error_record,
    write_error_record,
)


# --- ERROR_CATEGORIES tests ---


class TestErrorCategories:
    def test_contains_all_required_categories(self):
        expected = {"agent_failure", "terraform_failure", "validation_failure", "io_failure"}
        assert ERROR_CATEGORIES == expected

    def test_is_a_set(self):
        assert isinstance(ERROR_CATEGORIES, set)


# --- build_error_record tests ---


class TestBuildErrorRecord:
    def test_returns_all_required_fields(self):
        """Schema validation: all required fields exist with correct types."""
        try:
            raise ValueError("test error")
        except ValueError as exc:
            record = build_error_record(exc, "finops_auditor", "agent_failure")

        required_fields = {
            "error_type": str,
            "message": str,
            "traceback": str,
            "timestamp": str,
            "agent_name": str,
            "error_category": str,
        }
        for field, expected_type in required_fields.items():
            assert field in record, f"Missing required field: {field}"
            assert isinstance(record[field], expected_type), (
                f"Field '{field}' should be {expected_type.__name__}, got {type(record[field]).__name__}"
            )

    def test_concrete_expected_values(self):
        """Test with known concrete expected values (not derived from the function)."""
        try:
            raise RuntimeError("connection refused")
        except RuntimeError as exc:
            record = build_error_record(exc, "secops_guard", "io_failure")

        assert record["error_type"] == "RuntimeError"
        assert record["message"] == "connection refused"
        assert record["agent_name"] == "secops_guard"
        assert record["error_category"] == "io_failure"
        assert "RuntimeError: connection refused" in record["traceback"]

    def test_timestamp_is_iso8601_utc(self):
        """Timestamp must be ISO 8601 UTC format."""
        try:
            raise Exception("x")
        except Exception as exc:
            record = build_error_record(exc, "agent", "agent_failure")

        ts = record["timestamp"]
        # Must parse as a valid datetime
        parsed = datetime.fromisoformat(ts)
        # Must be UTC (tzinfo present and offset is zero)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0

    def test_traceback_truncated_to_4096_chars(self):
        """Traceback field must be at most 4096 characters."""
        # Create an exception with a very long message to produce a large traceback
        long_msg = "x" * 5000
        try:
            raise ValueError(long_msg)
        except ValueError as exc:
            record = build_error_record(exc, "agent", "agent_failure")

        assert len(record["traceback"]) <= 4096

    def test_no_traceback_when_exc_has_no_tb(self):
        """When exception has no __traceback__, traceback field is still a string."""
        exc = KeyError("missing_key")
        # exc.__traceback__ is None when not raised
        record = build_error_record(exc, "agent", "validation_failure")

        assert isinstance(record["traceback"], str)
        assert record["error_type"] == "KeyError"


# --- write_error_record tests ---


class TestWriteErrorRecord:
    def test_writes_single_jsonl_line(self, tmp_path: Path):
        """Must write exactly one JSONL line per call (Req 12.2)."""
        log_path = tmp_path / "errors.jsonl"
        record = {
            "error_type": "ValueError",
            "message": "bad input",
            "traceback": "",
            "timestamp": "2026-07-01T12:00:00+00:00",
            "agent_name": "test_agent",
            "error_category": "validation_failure",
        }

        write_error_record(record, log_path)

        content = log_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["error_type"] == "ValueError"
        assert parsed["message"] == "bad input"

    def test_appends_multiple_records(self, tmp_path: Path):
        """Multiple calls append separate lines (not overwrite)."""
        log_path = tmp_path / "errors.jsonl"
        record1 = {"error_type": "A", "message": "first"}
        record2 = {"error_type": "B", "message": "second"}

        write_error_record(record1, log_path)
        write_error_record(record2, log_path)

        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["error_type"] == "A"
        assert json.loads(lines[1])["error_type"] == "B"

    def test_creates_parent_directories(self, tmp_path: Path):
        """Parent directories are created if they don't exist."""
        log_path = tmp_path / "deep" / "nested" / "dir" / "errors.jsonl"
        record = {"error_type": "OSError", "message": "disk full"}

        write_error_record(record, log_path)

        assert log_path.exists()
        parsed = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert parsed["error_type"] == "OSError"

    def test_each_line_is_valid_json(self, tmp_path: Path):
        """Every line written must be independently parseable as JSON."""
        log_path = tmp_path / "errors.jsonl"
        record = {
            "error_type": "TypeError",
            "message": 'special "chars" & <html>',
            "traceback": "line1\nline2\nline3",
            "timestamp": "2026-07-01T00:00:00+00:00",
            "agent_name": "agent",
            "error_category": "agent_failure",
        }

        write_error_record(record, log_path)

        line = log_path.read_text(encoding="utf-8").splitlines()[0]
        parsed = json.loads(line)
        assert parsed["message"] == 'special "chars" & <html>'
        assert "\n" not in line  # No literal newlines in the JSON line


# --- Negative tests ---


class TestNegativeCases:
    def test_build_error_record_invalid_category_still_stored(self):
        """build_error_record does not validate error_category — it stores whatever is passed.
        Callers are responsible for using valid categories."""
        try:
            raise Exception("x")
        except Exception as exc:
            record = build_error_record(exc, "agent", "bogus_category")

        # The function stores the category as-is (no validation)
        assert record["error_category"] == "bogus_category"

    def test_write_error_record_does_not_modify_existing_content(self, tmp_path: Path):
        """Writing a new record must not alter previously written records."""
        log_path = tmp_path / "errors.jsonl"
        # Pre-existing content
        log_path.write_text('{"existing":"data"}\n', encoding="utf-8")
        mtime_before = log_path.stat().st_mtime

        import time
        time.sleep(0.01)  # ensure mtime changes

        write_error_record({"new": "record"}, log_path)

        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == '{"existing":"data"}'
        assert json.loads(lines[1])["new"] == "record"
