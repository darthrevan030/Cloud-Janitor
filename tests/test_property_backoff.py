"""Property tests for LLM backoff delay calculation.

Feature: production-readiness, Property 4: Backoff delay calculation

**Validates: Requirements 8.6, 8.7**

Property 4: Backoff Delay Calculation
For any retry attempt number `n` in {0, 1, 2} and for any optional Retry-After
header value `r`:

- If the error is HTTP 429 AND `r` is present AND 0 < r <= 60, the delay SHALL equal `r`
- If the error is HTTP 429 AND `r` is present AND r > 60, the client SHALL raise
  LLMRateLimitExceeded immediately (no retry)
- Otherwise, the delay SHALL equal 1 * 2^n seconds (i.e., 1s, 2s, 4s for attempts 0, 1, 2)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from core.llm_client import LLMRateLimitExceeded, LLMRetryExhausted, call_llm


# ─── Error construction helpers ──────────────────────────────────────────────


def _make_httpx_response(status_code: int, headers: dict | None = None) -> httpx.Response:
    """Build a real httpx.Response for OpenAI exception constructors."""
    return httpx.Response(
        status_code=status_code,
        headers=headers or {},
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )


def _rate_limit_error_with_retry_after(retry_after: float) -> openai.RateLimitError:
    """Create a RateLimitError WITH a Retry-After header."""
    response = _make_httpx_response(429, {"Retry-After": str(retry_after)})
    return openai.RateLimitError(
        message="Rate limited",
        response=response,
        body=None,
    )


def _rate_limit_error_no_header() -> openai.RateLimitError:
    """Create a RateLimitError WITHOUT a Retry-After header."""
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


# ─── Independent oracles ─────────────────────────────────────────────────────


def _oracle_exponential_delay(attempt: int) -> float:
    """Oracle: expected delay for a given attempt using exponential backoff.

    Formula: base_delay * multiplier^attempt = 1.0 * 2^attempt
    attempt 0 -> 1.0
    attempt 1 -> 2.0
    attempt 2 -> 4.0
    """
    return 1.0 * (2 ** attempt)


def _oracle_delay_for_429_with_retry_after(retry_after: float) -> float | None:
    """Oracle: expected delay for HTTP 429 with Retry-After header.

    - If 0 < retry_after <= 60: delay equals retry_after
    - If retry_after > 60: LLMRateLimitExceeded is raised (no delay)

    Returns the expected delay, or None if LLMRateLimitExceeded should be raised.
    """
    if retry_after > 60:
        return None  # signals immediate raise
    return retry_after


# ─── Strategies ──────────────────────────────────────────────────────────────

# Attempt numbers: 0, 1, 2 (the retry attempt index within the loop)
attempt_numbers = st.integers(min_value=0, max_value=2)

# Non-429 retriable status codes
non_429_retriable_codes = st.sampled_from([500, 502, 503, 504])

# Retry-After values that are within acceptable range (0.1 to 60.0)
valid_retry_after = st.floats(min_value=0.1, max_value=60.0, allow_nan=False, allow_infinity=False)

# Retry-After values that exceed the maximum (60.01 to 120.0)
excessive_retry_after = st.floats(min_value=60.01, max_value=120.0, allow_nan=False, allow_infinity=False)

# Any Retry-After value (mix of valid and excessive)
any_retry_after = st.floats(min_value=0.1, max_value=120.0, allow_nan=False, allow_infinity=False)


# ─── Property tests ─────────────────────────────────────────────────────────


class TestPropertyBackoffDelayCalculation:
    """Property 4: Backoff Delay Calculation.

    Validates that delay follows the correct formula based on error type,
    attempt number, and Retry-After header presence/value.

    **Validates: Requirements 8.6, 8.7**
    """

    @given(attempt=attempt_numbers, status_code=non_429_retriable_codes)
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_non_429_uses_exponential_backoff(
        self, mock_sleep, attempt: int, status_code: int
    ):
        """Non-429 retriable errors use delay = 1 * 2^attempt regardless of attempt number.

        We send `attempt + 1` failures followed by a success, then verify the
        sleep call for the specific attempt matches the oracle.
        """
        failure_count = attempt + 1  # need this many failures to reach that attempt's sleep

        side_effects: list = [_server_error(status_code) for _ in range(failure_count)]
        side_effects.append(MagicMock(spec=openai.types.chat.ChatCompletion))

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        call_llm(client, model="test", messages=[])

        # Verify the sleep call at the target attempt index
        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert len(sleep_args) == failure_count, (
            f"Expected {failure_count} sleep calls, got {len(sleep_args)}"
        )

        # Check the last sleep call (the one for the target attempt)
        actual_delay = sleep_args[attempt]
        expected_delay = _oracle_exponential_delay(attempt)
        assert actual_delay == pytest.approx(expected_delay), (
            f"For attempt={attempt}, status_code={status_code}: "
            f"expected delay={expected_delay}, got {actual_delay}"
        )

    @given(attempt=attempt_numbers)
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_timeout_uses_exponential_backoff(
        self, mock_sleep, attempt: int
    ):
        """Timeout errors use delay = 1 * 2^attempt regardless of attempt number."""
        failure_count = attempt + 1

        side_effects: list = [_timeout_error() for _ in range(failure_count)]
        side_effects.append(MagicMock(spec=openai.types.chat.ChatCompletion))

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        call_llm(client, model="test", messages=[])

        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert len(sleep_args) == failure_count

        actual_delay = sleep_args[attempt]
        expected_delay = _oracle_exponential_delay(attempt)
        assert actual_delay == pytest.approx(expected_delay), (
            f"For attempt={attempt} (timeout): "
            f"expected delay={expected_delay}, got {actual_delay}"
        )

    @given(attempt=attempt_numbers)
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_429_without_retry_after_uses_exponential_backoff(
        self, mock_sleep, attempt: int
    ):
        """HTTP 429 without Retry-After header falls back to exponential backoff."""
        failure_count = attempt + 1

        side_effects: list = [_rate_limit_error_no_header() for _ in range(failure_count)]
        side_effects.append(MagicMock(spec=openai.types.chat.ChatCompletion))

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        call_llm(client, model="test", messages=[])

        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert len(sleep_args) == failure_count

        actual_delay = sleep_args[attempt]
        expected_delay = _oracle_exponential_delay(attempt)
        assert actual_delay == pytest.approx(expected_delay), (
            f"For attempt={attempt} (429 no Retry-After): "
            f"expected delay={expected_delay}, got {actual_delay}"
        )

    @given(attempt=attempt_numbers, retry_after=valid_retry_after)
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_429_with_valid_retry_after_uses_header_value(
        self, mock_sleep, attempt: int, retry_after: float
    ):
        """HTTP 429 with Retry-After <= 60 uses the header value as delay.

        We construct `attempt + 1` failures all with the same Retry-After header,
        then verify the sleep at the target attempt equals the Retry-After value.
        """
        failure_count = attempt + 1

        side_effects: list = [
            _rate_limit_error_with_retry_after(retry_after)
            for _ in range(failure_count)
        ]
        side_effects.append(MagicMock(spec=openai.types.chat.ChatCompletion))

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        call_llm(client, model="test", messages=[])

        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert len(sleep_args) == failure_count

        actual_delay = sleep_args[attempt]
        expected_delay = _oracle_delay_for_429_with_retry_after(retry_after)
        assert expected_delay is not None  # sanity: valid range should not trigger raise
        assert actual_delay == pytest.approx(expected_delay), (
            f"For attempt={attempt}, Retry-After={retry_after}: "
            f"expected delay={expected_delay}, got {actual_delay}"
        )

    @given(retry_after=excessive_retry_after)
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_429_with_excessive_retry_after_raises_immediately(
        self, mock_sleep, retry_after: float
    ):
        """HTTP 429 with Retry-After > 60 raises LLMRateLimitExceeded immediately.

        No sleep should be called — the exception is raised on the first encounter.
        """
        side_effects = [_rate_limit_error_with_retry_after(retry_after)]

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        with pytest.raises(LLMRateLimitExceeded) as exc_info:
            call_llm(client, model="test", messages=[])

        # Verify exception contains the excessive retry_after value
        assert exc_info.value.retry_after == pytest.approx(retry_after), (
            f"Expected retry_after={retry_after}, got {exc_info.value.retry_after}"
        )

        # No sleep should have been called — immediate raise
        mock_sleep.assert_not_called()

    @given(attempt=attempt_numbers, retry_after=excessive_retry_after)
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_429_excessive_retry_after_raises_regardless_of_attempt(
        self, mock_sleep, attempt: int, retry_after: float
    ):
        """HTTP 429 with Retry-After > 60 raises immediately regardless of which attempt.

        Even if prior attempts succeeded with valid Retry-After, encountering an
        excessive Retry-After at any attempt raises immediately.
        """
        # Build: `attempt` successful retries (with valid Retry-After=1.0),
        # then one with excessive Retry-After
        side_effects: list = [
            _rate_limit_error_with_retry_after(1.0) for _ in range(attempt)
        ]
        side_effects.append(_rate_limit_error_with_retry_after(retry_after))

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        with pytest.raises(LLMRateLimitExceeded) as exc_info:
            call_llm(client, model="test", messages=[])

        assert exc_info.value.retry_after == pytest.approx(retry_after)

        # Only the prior valid retries should have caused sleeps
        assert mock_sleep.call_count == attempt, (
            f"Expected {attempt} sleep calls (from prior valid retries), "
            f"got {mock_sleep.call_count}"
        )

    @given(attempt=attempt_numbers)
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_all_delays_up_to_attempt_follow_formula(
        self, mock_sleep, attempt: int
    ):
        """All sleep calls up to and including the target attempt follow 1*2^n formula.

        This verifies the entire sequence, not just a single index.
        """
        failure_count = attempt + 1

        side_effects: list = [_server_error(500) for _ in range(failure_count)]
        side_effects.append(MagicMock(spec=openai.types.chat.ChatCompletion))

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        call_llm(client, model="test", messages=[])

        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        expected_sequence = [_oracle_exponential_delay(n) for n in range(failure_count)]

        assert sleep_args == expected_sequence, (
            f"For {failure_count} failures: expected delays={expected_sequence}, "
            f"got {sleep_args}"
        )

    @given(retry_after=any_retry_after)
    @settings(max_examples=200)
    @patch("core.llm_client.time.sleep")
    def test_retry_after_boundary_at_60(
        self, mock_sleep, retry_after: float
    ):
        """Verify the boundary: Retry-After <= 60 uses header, > 60 raises.

        This is the critical boundary test combining both cases.
        """
        side_effects = [_rate_limit_error_with_retry_after(retry_after)]
        # Add a success response in case the retry proceeds
        side_effects.append(MagicMock(spec=openai.types.chat.ChatCompletion))

        client = MagicMock()
        client.chat.completions.create.side_effect = side_effects

        if retry_after > 60:
            with pytest.raises(LLMRateLimitExceeded) as exc_info:
                call_llm(client, model="test", messages=[])
            assert exc_info.value.retry_after == pytest.approx(retry_after)
            mock_sleep.assert_not_called()
        else:
            call_llm(client, model="test", messages=[])
            sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
            assert len(sleep_args) == 1
            assert sleep_args[0] == pytest.approx(retry_after), (
                f"For Retry-After={retry_after} (<= 60): "
                f"expected sleep={retry_after}, got {sleep_args[0]}"
            )
