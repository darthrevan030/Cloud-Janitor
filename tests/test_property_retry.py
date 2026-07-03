"""Property tests for LLM retry on retriable errors.

Feature: production-readiness, Property 2: Retry on retriable errors

**Validates: Requirements 8.1, 8.2, 8.3, 8.5**

Property 2: Retry on Retriable Errors
For any retriable error type (HTTP 429, 500, 502, 503, 504, or network timeout)
and for any number of consecutive failures `n` where `1 <= n <= 3`, the `call_llm`
function SHALL make exactly `n + 1` total attempts before either succeeding (if the
`n+1`th attempt succeeds) or raising `LLMRetryExhausted` (if `n == 3`). Each retry
attempt SHALL produce a WARNING-level log record containing the attempt number,
wait duration, and error reason.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from core.llm_client import LLMRetryExhausted, call_llm


# ─── Error construction helpers ──────────────────────────────────────────────


def _make_httpx_response(status_code: int, headers: dict | None = None) -> httpx.Response:
    """Build a real httpx.Response for OpenAI exception constructors."""
    resp = httpx.Response(
        status_code=status_code,
        headers=headers or {},
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )
    return resp


def _rate_limit_error() -> openai.RateLimitError:
    """Create a RateLimitError without Retry-After header."""
    response = _make_httpx_response(429, {})
    return openai.RateLimitError(
        message="Rate limited",
        response=response,
        body=None,
    )


def _server_error(status_code: int) -> openai.APIStatusError:
    """Create an APIStatusError for a given retriable status code."""
    response = _make_httpx_response(status_code)
    return openai.APIStatusError(
        message=f"Server error {status_code}",
        response=response,
        body=None,
    )


def _timeout_error() -> openai.APITimeoutError:
    """Create an APITimeoutError simulating a network timeout."""
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return openai.APITimeoutError(request=request)


def _make_error(error_type: str) -> Exception:
    """Construct an error instance based on the error type string."""
    if error_type == "HTTP 429":
        return _rate_limit_error()
    elif error_type == "timeout":
        return _timeout_error()
    else:
        # Extract status code from "HTTP 500", "HTTP 502", etc.
        code = int(error_type.split(" ")[1])
        return _server_error(code)


# ─── Independent oracle ──────────────────────────────────────────────────────


def _oracle_total_attempts(failure_count: int, final_succeeds: bool) -> int:
    """Independent oracle: expected total attempts for given scenario.

    If final succeeds: attempts = failure_count + 1
    If final fails (only when failure_count == 3): attempts = 4
    """
    if final_succeeds:
        return failure_count + 1
    else:
        # final_succeeds=False only valid when failure_count == 3
        return 4


def _oracle_warning_count(failure_count: int, final_succeeds: bool) -> int:
    """Independent oracle: expected number of WARNING log records.

    Each retry produces one WARNING before sleeping.
    If final succeeds after n failures: n warnings.
    If all 4 attempts fail (failure_count==3, final_succeeds==False): 3 warnings.
    """
    if final_succeeds:
        return failure_count
    else:
        # All 4 attempts fail: warnings emitted before retries 1, 2, 3
        return 3


def _oracle_expected_error_substring(error_type: str) -> str:
    """Independent oracle: substring expected in WARNING log messages for this error type."""
    if error_type == "HTTP 429":
        return "rate-limited"
    elif error_type == "timeout":
        return "timed out"
    else:
        # e.g., "HTTP 500" -> the warning contains the status code number
        code = error_type.split(" ")[1]
        return code


# ─── Strategies ──────────────────────────────────────────────────────────────

# All retriable error types
retriable_error_types = st.sampled_from([
    "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "timeout"
])

# Number of consecutive failures before (possible) success: 1, 2, or 3
failure_counts = st.integers(min_value=1, max_value=3)

# Whether the final attempt succeeds or fails
final_succeeds_strategy = st.booleans()


# ─── Log capture handler ─────────────────────────────────────────────────────


class _CaptureHandler(logging.Handler):
    """Handler that captures log records into a list."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


# ─── Property tests ─────────────────────────────────────────────────────────


class TestPropertyRetryOnRetriableErrors:
    """Property 2: Retry on Retriable Errors.

    **Validates: Requirements 8.1, 8.2, 8.3, 8.5**
    """

    @given(
        error_type=retriable_error_types,
        failure_count=failure_counts,
        final_succeeds=final_succeeds_strategy,
    )
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_total_attempts_matches_oracle(
        self, mock_sleep, error_type: str, failure_count: int, final_succeeds: bool
    ):
        """Total call count to the API matches the independent oracle prediction."""
        # If failure_count < 3, final must succeed (can't exhaust retries with < 3 failures)
        if failure_count < 3 and not final_succeeds:
            final_succeeds = True

        expected_attempts = _oracle_total_attempts(failure_count, final_succeeds)

        # Build side effects: each failure is a fresh error instance
        if final_succeeds:
            side_effects = [_make_error(error_type) for _ in range(failure_count)]
            side_effects.append(MagicMock(spec=openai.types.chat.ChatCompletion))
        else:
            # All 4 attempts fail (failure_count must be 3)
            side_effects = [_make_error(error_type) for _ in range(4)]

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        if final_succeeds:
            call_llm(client, model="test", messages=[])
        else:
            with pytest.raises(LLMRetryExhausted):
                call_llm(client, model="test", messages=[])

        actual_attempts = client.chat.completions.create.call_count
        assert actual_attempts == expected_attempts, (
            f"For error_type={error_type!r}, failure_count={failure_count}, "
            f"final_succeeds={final_succeeds}: expected {expected_attempts} attempts, "
            f"got {actual_attempts}"
        )

    @given(
        error_type=retriable_error_types,
        failure_count=failure_counts,
        final_succeeds=final_succeeds_strategy,
    )
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_warning_count_matches_oracle(
        self, mock_sleep, error_type: str, failure_count: int, final_succeeds: bool
    ):
        """Number of WARNING log records matches the independent oracle prediction."""
        if failure_count < 3 and not final_succeeds:
            final_succeeds = True

        expected_warnings = _oracle_warning_count(failure_count, final_succeeds)

        # Build side effects
        if final_succeeds:
            side_effects = [_make_error(error_type) for _ in range(failure_count)]
            side_effects.append(MagicMock(spec=openai.types.chat.ChatCompletion))
        else:
            side_effects = [_make_error(error_type) for _ in range(4)]

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        # Capture logs from core.llm_client logger
        llm_logger = logging.getLogger("core.llm_client")
        capture = _CaptureHandler()
        capture.setLevel(logging.DEBUG)
        llm_logger.addHandler(capture)

        try:
            if final_succeeds:
                call_llm(client, model="test", messages=[])
            else:
                with pytest.raises(LLMRetryExhausted):
                    call_llm(client, model="test", messages=[])

            warning_records = [r for r in capture.records if r.levelno == logging.WARNING]
            actual_warnings = len(warning_records)

            assert actual_warnings == expected_warnings, (
                f"For error_type={error_type!r}, failure_count={failure_count}, "
                f"final_succeeds={final_succeeds}: expected {expected_warnings} WARNING "
                f"records, got {actual_warnings}"
            )
        finally:
            llm_logger.removeHandler(capture)

    @given(
        error_type=retriable_error_types,
        failure_count=failure_counts,
        final_succeeds=final_succeeds_strategy,
    )
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_warning_records_contain_attempt_number(
        self, mock_sleep, error_type: str, failure_count: int, final_succeeds: bool
    ):
        """Each WARNING log record contains the retry attempt number."""
        if failure_count < 3 and not final_succeeds:
            final_succeeds = True

        if final_succeeds:
            side_effects = [_make_error(error_type) for _ in range(failure_count)]
            side_effects.append(MagicMock(spec=openai.types.chat.ChatCompletion))
        else:
            side_effects = [_make_error(error_type) for _ in range(4)]

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        llm_logger = logging.getLogger("core.llm_client")
        capture = _CaptureHandler()
        capture.setLevel(logging.DEBUG)
        llm_logger.addHandler(capture)

        try:
            if final_succeeds:
                call_llm(client, model="test", messages=[])
            else:
                with pytest.raises(LLMRetryExhausted):
                    call_llm(client, model="test", messages=[])

            warning_records = [r for r in capture.records if r.levelno == logging.WARNING]

            for idx, record in enumerate(warning_records):
                expected_attempt_num = idx + 1  # retry 1, 2, 3
                msg = record.getMessage()
                # The message should contain "retry X of 3" pattern
                assert f"retry {expected_attempt_num} of 3" in msg, (
                    f"WARNING record {idx} should contain 'retry {expected_attempt_num} of 3', "
                    f"but message was: {msg!r}"
                )
        finally:
            llm_logger.removeHandler(capture)

    @given(
        error_type=retriable_error_types,
        failure_count=failure_counts,
        final_succeeds=final_succeeds_strategy,
    )
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_warning_records_contain_wait_duration(
        self, mock_sleep, error_type: str, failure_count: int, final_succeeds: bool
    ):
        """Each WARNING log record contains the wait duration value."""
        if failure_count < 3 and not final_succeeds:
            final_succeeds = True

        if final_succeeds:
            side_effects = [_make_error(error_type) for _ in range(failure_count)]
            side_effects.append(MagicMock(spec=openai.types.chat.ChatCompletion))
        else:
            side_effects = [_make_error(error_type) for _ in range(4)]

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        llm_logger = logging.getLogger("core.llm_client")
        capture = _CaptureHandler()
        capture.setLevel(logging.DEBUG)
        llm_logger.addHandler(capture)

        try:
            if final_succeeds:
                call_llm(client, model="test", messages=[])
            else:
                with pytest.raises(LLMRetryExhausted):
                    call_llm(client, model="test", messages=[])

            warning_records = [r for r in capture.records if r.levelno == logging.WARNING]

            # Expected delays: 1.0s (attempt 0 -> retry 1), 2.0s (attempt 1 -> retry 2),
            #                   4.0s (attempt 2 -> retry 3)
            expected_delays = [1.0, 2.0, 4.0]

            for idx, record in enumerate(warning_records):
                msg = record.getMessage()
                expected_delay = expected_delays[idx]
                delay_str = f"{expected_delay:.1f}"
                assert delay_str in msg, (
                    f"WARNING record {idx} should contain wait duration "
                    f"{delay_str}s, but message was: {msg!r}"
                )
        finally:
            llm_logger.removeHandler(capture)

    @given(
        error_type=retriable_error_types,
        failure_count=failure_counts,
        final_succeeds=final_succeeds_strategy,
    )
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_warning_records_contain_error_reason(
        self, mock_sleep, error_type: str, failure_count: int, final_succeeds: bool
    ):
        """Each WARNING log record contains the error reason."""
        if failure_count < 3 and not final_succeeds:
            final_succeeds = True

        if final_succeeds:
            side_effects = [_make_error(error_type) for _ in range(failure_count)]
            side_effects.append(MagicMock(spec=openai.types.chat.ChatCompletion))
        else:
            side_effects = [_make_error(error_type) for _ in range(4)]

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        llm_logger = logging.getLogger("core.llm_client")
        capture = _CaptureHandler()
        capture.setLevel(logging.DEBUG)
        llm_logger.addHandler(capture)

        try:
            if final_succeeds:
                call_llm(client, model="test", messages=[])
            else:
                with pytest.raises(LLMRetryExhausted):
                    call_llm(client, model="test", messages=[])

            warning_records = [r for r in capture.records if r.levelno == logging.WARNING]
            expected_substring = _oracle_expected_error_substring(error_type)

            for idx, record in enumerate(warning_records):
                msg = record.getMessage()
                assert expected_substring in msg, (
                    f"WARNING record {idx} for error_type={error_type!r} should contain "
                    f"{expected_substring!r}, but message was: {msg!r}"
                )
        finally:
            llm_logger.removeHandler(capture)
