"""Property-based tests for core.error_telemetry module.

**Validates: Requirements 12.1, 12.2, 12.3**

Uses Hypothesis to verify universal properties of structured error records
and JSONL formatting across all valid inputs.
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from core.error_telemetry import (
    ERROR_CATEGORIES,
    build_error_record,
    write_error_record,
)

# --- Strategies ---

# Generate arbitrary Exception subclasses with arbitrary messages
_exception_types = [
    ValueError,
    TypeError,
    RuntimeError,
    KeyError,
    OSError,
    IOError,
    PermissionError,
    FileNotFoundError,
    AttributeError,
    IndexError,
    ZeroDivisionError,
    NotImplementedError,
    StopIteration,
    OverflowError,
    UnicodeDecodeError,
]


@st.composite
def exception_strategy(draw):
    """Generate an arbitrary exception instance.

    Some exceptions are raised-and-caught (so they have a traceback),
    some are bare (no traceback). This exercises both paths.
    """
    exc_cls = draw(st.sampled_from(_exception_types))
    msg = draw(st.text(min_size=0, max_size=5000))

    # UnicodeDecodeError requires special constructor args
    if exc_cls is UnicodeDecodeError:
        return UnicodeDecodeError("utf-8", b"\xff", 0, 1, msg)

    # Randomly decide whether to give the exception a real traceback
    use_traceback = draw(st.booleans())
    if use_traceback:
        try:
            raise exc_cls(msg)
        except exc_cls as e:
            return e
    else:
        return exc_cls(msg)


agent_name_strategy = st.text(min_size=0, max_size=200)
error_category_strategy = st.sampled_from(sorted(ERROR_CATEGORIES))


# --- Property 11: Structured Error Record Completeness ---


class TestProperty11StructuredErrorRecordCompleteness:
    """Property 11: Structured Error Record Completeness.

    For any exception and agent name, build_error_record() SHALL produce
    a dict containing all required fields with correct types and constraints.
    """

    REQUIRED_FIELDS = {
        "error_type",
        "message",
        "traceback",
        "timestamp",
        "agent_name",
        "error_category",
    }

    @given(
        exc=exception_strategy(),
        agent_name=agent_name_strategy,
        error_category=error_category_strategy,
    )
    @settings(max_examples=200)
    def test_all_required_fields_present(self, exc, agent_name, error_category):
        """All 6 required fields must be present in the returned dict."""
        record = build_error_record(exc, agent_name, error_category)

        missing = self.REQUIRED_FIELDS - set(record.keys())
        assert missing == set(), f"Missing required fields: {missing}"

    @given(
        exc=exception_strategy(),
        agent_name=agent_name_strategy,
        error_category=error_category_strategy,
    )
    @settings(max_examples=200)
    def test_all_fields_are_strings(self, exc, agent_name, error_category):
        """Every required field must have type str."""
        record = build_error_record(exc, agent_name, error_category)

        for field in self.REQUIRED_FIELDS:
            assert isinstance(record[field], str), (
                f"Field '{field}' is {type(record[field]).__name__}, expected str"
            )

    @given(
        exc=exception_strategy(),
        agent_name=agent_name_strategy,
        error_category=error_category_strategy,
    )
    @settings(max_examples=200)
    def test_error_category_in_valid_set(self, exc, agent_name, error_category):
        """error_category must be one of the 4 valid categories."""
        record = build_error_record(exc, agent_name, error_category)

        assert record["error_category"] in ERROR_CATEGORIES, (
            f"Got '{record['error_category']}', expected one of {ERROR_CATEGORIES}"
        )

    @given(
        exc=exception_strategy(),
        agent_name=agent_name_strategy,
        error_category=error_category_strategy,
    )
    @settings(max_examples=200)
    def test_traceback_length_at_most_4096(self, exc, agent_name, error_category):
        """Traceback field must be at most 4096 characters."""
        record = build_error_record(exc, agent_name, error_category)

        assert len(record["traceback"]) <= 4096, (
            f"Traceback length {len(record['traceback'])} exceeds 4096"
        )

    @given(
        exc=exception_strategy(),
        agent_name=agent_name_strategy,
        error_category=error_category_strategy,
    )
    @settings(max_examples=200)
    def test_timestamp_is_valid_iso8601_utc(self, exc, agent_name, error_category):
        """Timestamp must parse as valid ISO 8601 UTC datetime."""
        record = build_error_record(exc, agent_name, error_category)

        ts = record["timestamp"]
        # Must parse without error
        parsed = datetime.fromisoformat(ts)
        # Must have timezone info
        assert parsed.tzinfo is not None, "Timestamp missing timezone info"
        # Must be UTC (offset == 0)
        assert parsed.utcoffset().total_seconds() == 0, (
            f"Timestamp not UTC: offset is {parsed.utcoffset()}"
        )


# --- Property 12: JSONL Error Record Format ---


class TestProperty12JSONLErrorRecordFormat:
    """Property 12: JSONL Error Record Format.

    For any structured error record dict, write_error_record() SHALL append
    exactly one line that is independently parseable as valid JSON.
    Multiple sequential writes SHALL produce a file where each line is
    independently parseable.
    """

    @given(
        error_type=st.text(min_size=1, max_size=100),
        message=st.text(min_size=0, max_size=500),
        traceback_str=st.text(min_size=0, max_size=500),
        agent_name=st.text(min_size=0, max_size=100),
        error_category=st.sampled_from(sorted(ERROR_CATEGORIES)),
    )
    @settings(max_examples=200)
    def test_single_write_appends_exactly_one_line(
        self, error_type, message, traceback_str, agent_name, error_category
    ):
        """Each write_error_record call appends exactly one new line."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "errors.jsonl"
            record = {
                "error_type": error_type,
                "message": message,
                "traceback": traceback_str,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent_name": agent_name,
                "error_category": error_category,
            }

            write_error_record(record, log_path)

            content = log_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            assert len(lines) == 1, f"Expected 1 line, got {len(lines)}"

    @given(
        error_type=st.text(min_size=1, max_size=100),
        message=st.text(min_size=0, max_size=500),
        traceback_str=st.text(min_size=0, max_size=500),
        agent_name=st.text(min_size=0, max_size=100),
        error_category=st.sampled_from(sorted(ERROR_CATEGORIES)),
    )
    @settings(max_examples=200)
    def test_single_write_line_is_valid_json(
        self, error_type, message, traceback_str, agent_name, error_category
    ):
        """Each written line must be independently parseable as valid JSON."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "errors.jsonl"
            record = {
                "error_type": error_type,
                "message": message,
                "traceback": traceback_str,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent_name": agent_name,
                "error_category": error_category,
            }

            write_error_record(record, log_path)

            line = log_path.read_text(encoding="utf-8").splitlines()[0]
            parsed = json.loads(line)  # Must not raise
            assert isinstance(parsed, dict)

    @given(
        records=st.lists(
            st.fixed_dictionaries({
                "error_type": st.text(min_size=1, max_size=80),
                "message": st.text(min_size=0, max_size=300),
                "traceback": st.text(min_size=0, max_size=300),
                "timestamp": st.text(min_size=1, max_size=40),
                "agent_name": st.text(min_size=0, max_size=80),
                "error_category": st.sampled_from(sorted(ERROR_CATEGORIES)),
            }),
            min_size=2,
            max_size=10,
        )
    )
    @settings(max_examples=100)
    def test_multiple_writes_all_lines_valid_json(self, records):
        """Multiple sequential writes produce a file where EVERY line is valid JSON."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "multi_errors.jsonl"

            for record in records:
                write_error_record(record, log_path)

            content = log_path.read_text(encoding="utf-8")
            lines = content.splitlines()

            # Must have exactly as many lines as records written
            assert len(lines) == len(records), (
                f"Expected {len(records)} lines, got {len(lines)}"
            )

            # Each line must be independently parseable
            for i, line in enumerate(lines):
                try:
                    parsed = json.loads(line)
                    assert isinstance(parsed, dict), f"Line {i} is not a JSON object"
                except json.JSONDecodeError as e:
                    raise AssertionError(
                        f"Line {i} is not valid JSON: {e}\nContent: {line!r}"
                    )
