"""Property tests for LLM retry exhaustion exception content.

Feature: production-readiness, Property 3: Retry exhaustion exception content

**Validates: Requirements 8.4**

Property 3: Retry Exhaustion Exception Content
For any retriable error type that persists for all 4 attempts, the raised
LLMRetryExhausted exception SHALL contain: (a) the final HTTP status code or
error type string, (b) the total number of attempts made (always 4), and (c)
the total elapsed time as a positive float. Additionally, mocked sleep calls
SHALL sum to ~7 seconds (1+2+4) matching the exponential backoff formula.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.core.llm_client import LLMRetryExhausted, call_llm


# ─── Error construction helpers ──────────────────────────────────────────────


def _make_httpx_response(status_code: int, headers: dict | None = None) -> httpx.Response:
    """Build a real httpx.Response for OpenAI exception constructors."""
    return httpx.Response(
        status_code=status_code,
        headers=headers or {},
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )


def _rate_limit_error() -> openai.RateLimitError:
    """Create a RateLimitError without Retry-After header (retriable)."""
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
    """Construct a fresh error instance based on the error type string."""
    if error_type == "HTTP 429":
        return _rate_limit_error()
    elif error_type == "timeout":
        return _timeout_error()
    else:
        code = int(error_type.split(" ")[1])
        return _server_error(code)


# ─── Independent oracles ─────────────────────────────────────────────────────


def _oracle_status_or_error(error_type: str) -> str:
    """Oracle: expected status_or_error field for a given error type.

    - "HTTP 429" -> "HTTP 429"
    - "timeout" -> "timeout"
    - "HTTP 500" -> "HTTP 500"
    etc.
    """
    return error_type


def _oracle_sleep_sum() -> float:
    """Oracle: expected sum of sleep durations for 3 retries with exponential backoff.

    delay = 1 * 2^attempt for attempt in {0, 1, 2}
    = 1 + 2 + 4 = 7.0 seconds
    """
    return 7.0


def _oracle_sleep_sequence() -> list[float]:
    """Oracle: expected individual sleep values for 3 retries.

    Retry 1 (attempt 0): 1.0 * 2^0 = 1.0
    Retry 2 (attempt 1): 1.0 * 2^1 = 2.0
    Retry 3 (attempt 2): 1.0 * 2^2 = 4.0
    """
    return [1.0, 2.0, 4.0]


# ─── Strategies ──────────────────────────────────────────────────────────────

# All retriable error types that can persist for all 4 attempts
retriable_error_types = st.sampled_from([
    "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "timeout"
])


# ─── Property tests ─────────────────────────────────────────────────────────


class TestPropertyRetryExhaustionExceptionContent:
    """Property 3: Retry Exhaustion Exception Content.

    For any retriable error type that persists for all 4 attempts, the raised
    LLMRetryExhausted exception has correct status_or_error, attempts == 4,
    and sleep calls sum to 7 seconds (1+2+4).

    **Validates: Requirements 8.4**
    """

    @given(error_type=retriable_error_types)
    @settings(max_examples=200)
    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_status_or_error_matches_error_type(
        self, mock_sleep, error_type: str
    ):
        """LLMRetryExhausted.status_or_error matches the error type string."""
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            _make_error(error_type) for _ in range(4)
        ]

        with pytest.raises(LLMRetryExhausted) as exc_info:
            call_llm(client, model="test", messages=[])

        expected_status = _oracle_status_or_error(error_type)
        assert exc_info.value.status_or_error == expected_status, (
            f"For error_type={error_type!r}: expected status_or_error="
            f"{expected_status!r}, got {exc_info.value.status_or_error!r}"
        )

    @given(error_type=retriable_error_types)
    @settings(max_examples=200)
    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_attempts_always_equals_4(
        self, mock_sleep, error_type: str
    ):
        """LLMRetryExhausted.attempts is always 4 (1 original + 3 retries)."""
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            _make_error(error_type) for _ in range(4)
        ]

        with pytest.raises(LLMRetryExhausted) as exc_info:
            call_llm(client, model="test", messages=[])

        assert exc_info.value.attempts == 4, (
            f"For error_type={error_type!r}: expected attempts=4, "
            f"got {exc_info.value.attempts}"
        )

    @given(error_type=retriable_error_types)
    @settings(max_examples=200)
    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_elapsed_is_non_negative_float(
        self, mock_sleep, error_type: str
    ):
        """LLMRetryExhausted.elapsed is a non-negative float (real wall-clock time).

        With mocked sleep, elapsed may be near-zero since no real time passes.
        The key invariant is that it's a float >= 0 computed from time.monotonic().
        """
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            _make_error(error_type) for _ in range(4)
        ]

        with pytest.raises(LLMRetryExhausted) as exc_info:
            call_llm(client, model="test", messages=[])

        assert isinstance(exc_info.value.elapsed, float), (
            f"elapsed should be float, got {type(exc_info.value.elapsed)}"
        )
        assert exc_info.value.elapsed >= 0, (
            f"elapsed should be non-negative, got {exc_info.value.elapsed}"
        )

    @given(error_type=retriable_error_types)
    @settings(max_examples=200)
    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_sleep_calls_sum_to_7_seconds(
        self, mock_sleep, error_type: str
    ):
        """Mocked sleep calls sum to exactly 7 seconds (1+2+4) for exponential backoff."""
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            _make_error(error_type) for _ in range(4)
        ]

        with pytest.raises(LLMRetryExhausted):
            call_llm(client, model="test", messages=[])

        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        actual_sum = sum(sleep_args)
        expected_sum = _oracle_sleep_sum()

        assert actual_sum == pytest.approx(expected_sum), (
            f"For error_type={error_type!r}: expected sleep sum={expected_sum}, "
            f"got {actual_sum} from individual sleeps={sleep_args}"
        )

    @given(error_type=retriable_error_types)
    @settings(max_examples=200)
    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_sleep_sequence_matches_backoff_formula(
        self, mock_sleep, error_type: str
    ):
        """Individual sleep calls match the exponential backoff formula: 1s, 2s, 4s."""
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            _make_error(error_type) for _ in range(4)
        ]

        with pytest.raises(LLMRetryExhausted):
            call_llm(client, model="test", messages=[])

        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        expected_sequence = _oracle_sleep_sequence()

        assert sleep_args == expected_sequence, (
            f"For error_type={error_type!r}: expected sleep sequence="
            f"{expected_sequence}, got {sleep_args}"
        )

    @given(error_type=retriable_error_types)
    @settings(max_examples=200)
    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_exactly_3_sleep_calls_before_exhaustion(
        self, mock_sleep, error_type: str
    ):
        """Exactly 3 sleep calls occur (between attempts 1-2, 2-3, 3-4)."""
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            _make_error(error_type) for _ in range(4)
        ]

        with pytest.raises(LLMRetryExhausted):
            call_llm(client, model="test", messages=[])

        assert mock_sleep.call_count == 3, (
            f"For error_type={error_type!r}: expected 3 sleep calls, "
            f"got {mock_sleep.call_count}"
        )

    @given(error_type=retriable_error_types)
    @settings(max_examples=200)
    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_exception_message_contains_diagnostic_info(
        self, mock_sleep, error_type: str
    ):
        """LLMRetryExhausted string representation contains attempts and status."""
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            _make_error(error_type) for _ in range(4)
        ]

        with pytest.raises(LLMRetryExhausted) as exc_info:
            call_llm(client, model="test", messages=[])

        msg = str(exc_info.value)
        assert "4 attempts" in msg, (
            f"Exception message should contain '4 attempts', got: {msg!r}"
        )
        expected_status = _oracle_status_or_error(error_type)
        assert expected_status in msg, (
            f"Exception message should contain {expected_status!r}, got: {msg!r}"
        )
