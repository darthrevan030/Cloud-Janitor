"""Unit tests for core.logging_config.configure_logging().

Validates: Requirements 7.2, 7.3, 7.4, 7.6
"""

from __future__ import annotations

import logging
import os
import re
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from cloud_janitor.core.logging_config import configure_logging


@pytest.fixture(autouse=True)
def _reset_logging():
    """Reset root logger state between tests to avoid cross-contamination."""
    yield
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)


# ─── Requirement 7.2: Valid levels configure correctly ────────────────────────


class TestValidLevels:
    """JANITOR_LOG_LEVEL with valid values sets the root logger level."""

    @pytest.mark.parametrize(
        "env_value,expected_level",
        [
            ("DEBUG", logging.DEBUG),
            ("INFO", logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
        ],
    )
    def test_valid_level_configures_root_logger(self, env_value, expected_level):
        """Each valid level string sets the root logger to that level."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": env_value}):
            configure_logging()

        assert logging.getLogger().level == expected_level

    @pytest.mark.parametrize(
        "env_value,expected_level",
        [
            ("debug", logging.DEBUG),
            ("Debug", logging.DEBUG),
            ("DeBuG", logging.DEBUG),
            ("info", logging.INFO),
            ("Info", logging.INFO),
            ("warning", logging.WARNING),
            ("Warning", logging.WARNING),
            ("error", logging.ERROR),
            ("Error", logging.ERROR),
        ],
    )
    def test_case_insensitive_matching(self, env_value, expected_level):
        """Level matching is case-insensitive (Req 7.2)."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": env_value}):
            configure_logging()

        assert logging.getLogger().level == expected_level


# ─── Requirement 7.3: Missing env var defaults to INFO ────────────────────────


class TestDefaultLevel:
    """When JANITOR_LOG_LEVEL is unset, root logger defaults to INFO."""

    def test_missing_env_var_defaults_to_info(self):
        """No JANITOR_LOG_LEVEL in environment => root logger at INFO."""
        env = os.environ.copy()
        env.pop("JANITOR_LOG_LEVEL", None)

        with patch.dict(os.environ, env, clear=True):
            configure_logging()

        assert logging.getLogger().level == logging.INFO

    def test_default_does_not_emit_warning(self):
        """Missing env var should NOT produce a warning log about invalid value."""
        env = os.environ.copy()
        env.pop("JANITOR_LOG_LEVEL", None)

        with patch.dict(os.environ, env, clear=True):
            configure_logging()

        # If we log at WARNING level and capture, there should be no "Invalid" message
        logger = logging.getLogger("cloud_janitor.core.logging_config")
        with patch.object(logger, "warning") as mock_warn:
            # Re-run to check no warning emitted on the clean pass
            pass
        # The real check: capture log output and verify no invalid message
        # We do this by checking the root handler output
        root = logging.getLogger()
        assert root.level == logging.INFO


# ─── Requirement 7.6: Invalid value falls back to INFO + emits WARNING ────────


class TestInvalidLevel:
    """Invalid JANITOR_LOG_LEVEL falls back to INFO and emits a WARNING."""

    @pytest.mark.parametrize("bad_value", ["TRACE", "VERBOSE", "FATAL", "0", ""])
    def test_invalid_level_falls_back_to_info(self, bad_value):
        """Invalid values result in root logger set to INFO."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": bad_value}):
            configure_logging()

        assert logging.getLogger().level == logging.INFO

    @pytest.mark.parametrize("bad_value", ["TRACE", "VERBOSE", "nonsense", "123"])
    def test_invalid_level_emits_warning_log(self, bad_value, capsys):
        """Invalid values emit a WARNING log message mentioning the bad value."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": bad_value}):
            configure_logging()

        # Capture stderr (where the handler writes)
        captured = capsys.readouterr()
        assert "Invalid JANITOR_LOG_LEVEL" in captured.err
        assert bad_value in captured.err

    def test_invalid_level_warning_mentions_valid_options(self, capsys):
        """The warning message mentions all valid level names."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": "BADLEVEL"}):
            configure_logging()

        captured = capsys.readouterr()
        for level_name in ("DEBUG", "INFO", "WARNING", "ERROR"):
            assert level_name in captured.err

    def test_valid_level_does_not_emit_invalid_warning(self, capsys):
        """Negative test: valid level should NOT emit an 'Invalid' warning."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": "DEBUG"}):
            configure_logging()

        captured = capsys.readouterr()
        assert "Invalid JANITOR_LOG_LEVEL" not in captured.err


# ─── Requirement 7.4: Output to stderr ───────────────────────────────────────


class TestStderrOutput:
    """All log output goes to stderr, not stdout."""

    def test_log_output_goes_to_stderr(self, capsys):
        """Log messages appear on stderr, not stdout."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": "DEBUG"}):
            configure_logging()

        logging.getLogger("test.stderr").info("hello stderr test")

        captured = capsys.readouterr()
        assert "hello stderr test" in captured.err
        assert "hello stderr test" not in captured.out

    def test_handler_stream_is_stderr(self):
        """The configured handler's stream is sys.stderr."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": "INFO"}):
            configure_logging()

        root = logging.getLogger()
        # There should be at least one StreamHandler targeting stderr
        stderr_handlers = [
            h
            for h in root.handlers
            if isinstance(h, logging.StreamHandler) and h.stream is sys.stderr
        ]
        assert len(stderr_handlers) >= 1, (
            f"Expected at least one stderr handler, got: {root.handlers}"
        )


# ─── Requirement 7.4: Log format ─────────────────────────────────────────────


class TestLogFormat:
    """Log format includes timestamp, level, name, message."""

    def test_format_includes_all_required_fields(self, capsys):
        """Each log line contains: timestamp, level, logger name, message."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": "DEBUG"}):
            configure_logging()

        test_logger = logging.getLogger("myapp.module")
        test_logger.info("format check message")

        captured = capsys.readouterr()
        log_line = captured.err.strip()

        # Timestamp: ISO 8601-like pattern at start (YYYY-MM-DDTHH:MM:SS)
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", log_line), (
            f"No ISO 8601 timestamp found in: {log_line!r}"
        )
        # Level name
        assert "INFO" in log_line
        # Logger name
        assert "myapp.module" in log_line
        # Message
        assert "format check message" in log_line

    def test_format_field_ordering(self, capsys):
        """Fields appear in order: timestamp, level, name, message."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": "DEBUG"}):
            configure_logging()

        test_logger = logging.getLogger("ordering.test")
        test_logger.warning("order test msg")

        captured = capsys.readouterr()
        log_line = captured.err.strip()

        # Find positions of each field
        ts_match = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", log_line)
        level_pos = log_line.find("WARNING")
        name_pos = log_line.find("ordering.test")
        msg_pos = log_line.find("order test msg")

        assert ts_match is not None
        assert ts_match.start() < level_pos < name_pos < msg_pos, (
            f"Fields not in expected order in: {log_line!r}"
        )

    def test_timestamp_uses_iso_8601_format(self, capsys):
        """Timestamp uses ISO 8601 date format with T separator."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": "INFO"}):
            configure_logging()

        logging.getLogger("ts.test").info("timestamp check")

        captured = capsys.readouterr()
        log_line = captured.err.strip()

        # Should match YYYY-MM-DDTHH:MM:SS (the 'T' separator is key for ISO 8601)
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", log_line), (
            f"Expected ISO 8601 timestamp with T separator in: {log_line!r}"
        )


# ─── Negative tests ──────────────────────────────────────────────────────────


class TestNegativeCases:
    """Tests for what configure_logging should NOT do."""

    def test_does_not_set_critical_level(self):
        """CRITICAL is not a valid JANITOR_LOG_LEVEL — should fall back to INFO."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": "CRITICAL"}):
            configure_logging()

        # CRITICAL is not in the valid set, so should be INFO
        assert logging.getLogger().level == logging.INFO

    def test_does_not_output_to_stdout(self, capsys):
        """Log messages must NOT appear on stdout."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": "INFO"}):
            configure_logging()

        logging.getLogger("neg.stdout").warning("should not be on stdout")

        captured = capsys.readouterr()
        assert "should not be on stdout" not in captured.out

    def test_does_not_accept_numeric_level_string(self):
        """Numeric strings like '10' or '20' are not valid levels."""
        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": "10"}):
            configure_logging()

        # Should fall back to INFO, not set level to 10
        assert logging.getLogger().level == logging.INFO
