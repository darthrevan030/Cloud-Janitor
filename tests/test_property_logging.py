"""Property tests for log level configuration mapping.

Feature: production-readiness, Property 1: Log level configuration mapping

**Validates: Requirements 7.2, 7.6**

Property 1: Log Level Configuration Mapping
For any string value of JANITOR_LOG_LEVEL, calling configure_logging() SHALL
configure the root logger level to that level if the value (case-insensitive)
is in {"DEBUG", "INFO", "WARNING", "ERROR"}, or to INFO otherwise. Additionally,
if the value is not in the valid set, a WARNING-level log record SHALL be emitted
indicating the invalid value.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from cloud_janitor.core.logging_config import configure_logging


# ─── Independent oracle ──────────────────────────────────────────────────────
# Re-derive the expected mapping independently (not referencing production code).

_ORACLE_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}

_ORACLE_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _oracle_expected_level(raw_value: str) -> int:
    """Independent oracle: returns expected root logger level for any env value."""
    normalized = raw_value.upper()
    if normalized in _ORACLE_VALID_LEVELS:
        return _ORACLE_LEVEL_MAP[normalized]
    return logging.INFO


def _oracle_should_warn(raw_value: str) -> bool:
    """Independent oracle: returns True if a WARNING should be emitted."""
    return raw_value.upper() not in _ORACLE_VALID_LEVELS


# ─── Strategies ──────────────────────────────────────────────────────────────

# Strategy: valid level strings in random casing
def _random_case(level: str) -> st.SearchStrategy[str]:
    """Generate a valid level name with randomized casing."""
    return st.tuples(
        *[st.sampled_from([c.lower(), c.upper()]) for c in level]
    ).map(lambda chars: "".join(chars))


# Combined strategy for any valid level in any casing
valid_level_random_case = st.one_of(
    *[_random_case(level) for level in sorted(_ORACLE_VALID_LEVELS)]
)

# Strategy: arbitrary text strings excluding null bytes (OS env var constraint on Windows)
arbitrary_text = st.text(
    alphabet=st.characters(blacklist_characters="\x00"),
    min_size=0,
    max_size=50,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _reset_root_logger() -> None:
    """Reset root logger state to avoid cross-contamination between tests."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)


class _CaptureHandler(logging.Handler):
    """Handler that captures log records into a list."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


# ─── Property tests ─────────────────────────────────────────────────────────


class TestPropertyLogLevelMapping:
    """Property 1: Log Level Configuration Mapping.

    **Validates: Requirements 7.2, 7.6**
    """

    @given(level_str=valid_level_random_case)
    @settings(max_examples=200)
    def test_valid_levels_configure_correct_root_level(self, level_str: str):
        """For any valid level (any casing), root logger level equals the mapped constant."""
        _reset_root_logger()

        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": level_str}):
            configure_logging()

        expected = _oracle_expected_level(level_str)
        actual = logging.getLogger().level

        assert actual == expected, (
            f"For JANITOR_LOG_LEVEL={level_str!r}, expected root level "
            f"{expected} ({logging.getLevelName(expected)}), "
            f"got {actual} ({logging.getLevelName(actual)})"
        )

    @given(level_str=valid_level_random_case)
    @settings(max_examples=200)
    def test_valid_levels_do_not_emit_invalid_warning(self, level_str: str):
        """For any valid level (any casing), no WARNING about invalid value is emitted."""
        _reset_root_logger()

        # Install a capture handler at the root BEFORE configure_logging runs,
        # so we can intercept the warning that configure_logging would emit.
        # But configure_logging uses force=True which clears handlers.
        # So we capture via the specific module logger instead.
        config_logger = logging.getLogger("cloud_janitor.core.logging_config")
        capture = _CaptureHandler()
        capture.setLevel(logging.DEBUG)
        config_logger.addHandler(capture)

        try:
            with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": level_str}):
                configure_logging()

            invalid_warnings = [
                r for r in capture.records
                if r.levelno >= logging.WARNING
                and "Invalid JANITOR_LOG_LEVEL" in r.getMessage()
            ]
            assert invalid_warnings == [], (
                f"Valid level {level_str!r} should NOT emit invalid warning, "
                f"but got: {[r.getMessage() for r in invalid_warnings]}"
            )
        finally:
            config_logger.removeHandler(capture)

    @given(raw_value=arbitrary_text)
    @settings(max_examples=300)
    def test_invalid_levels_fall_back_to_info(self, raw_value: str):
        """For any string NOT in the valid set (case-insensitive), root level is INFO."""
        assume(raw_value.upper() not in _ORACLE_VALID_LEVELS)
        _reset_root_logger()

        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": raw_value}):
            configure_logging()

        expected = logging.INFO
        actual = logging.getLogger().level

        assert actual == expected, (
            f"For invalid JANITOR_LOG_LEVEL={raw_value!r}, expected root level "
            f"{expected} (INFO), got {actual} ({logging.getLevelName(actual)})"
        )

    @given(raw_value=arbitrary_text)
    @settings(max_examples=300)
    def test_invalid_levels_emit_warning_with_value(self, raw_value: str):
        """For any invalid value, a WARNING log record is emitted mentioning the value."""
        assume(raw_value.upper() not in _ORACLE_VALID_LEVELS)
        _reset_root_logger()

        config_logger = logging.getLogger("cloud_janitor.core.logging_config")
        capture = _CaptureHandler()
        capture.setLevel(logging.DEBUG)
        config_logger.addHandler(capture)

        try:
            with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": raw_value}):
                configure_logging()

            warning_records = [
                r for r in capture.records
                if r.levelno == logging.WARNING
            ]
            assert len(warning_records) >= 1, (
                f"For invalid JANITOR_LOG_LEVEL={raw_value!r}, expected a WARNING "
                f"log record but got none"
            )
            # The warning message should mention it's an invalid level
            warning_text = " ".join(r.getMessage() for r in warning_records)
            assert "Invalid JANITOR_LOG_LEVEL" in warning_text, (
                f"Warning message should mention 'Invalid JANITOR_LOG_LEVEL', "
                f"got: {warning_text!r}"
            )
        finally:
            config_logger.removeHandler(capture)

    @given(raw_value=arbitrary_text)
    @settings(max_examples=500)
    def test_oracle_agreement_on_any_input(self, raw_value: str):
        """For ANY string, root logger level matches the independent oracle prediction."""
        _reset_root_logger()

        with patch.dict(os.environ, {"JANITOR_LOG_LEVEL": raw_value}):
            configure_logging()

        expected = _oracle_expected_level(raw_value)
        actual = logging.getLogger().level

        assert actual == expected, (
            f"Oracle disagrees for JANITOR_LOG_LEVEL={raw_value!r}: "
            f"expected {expected} ({logging.getLevelName(expected)}), "
            f"got {actual} ({logging.getLevelName(actual)})"
        )
