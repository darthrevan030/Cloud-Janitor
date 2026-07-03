"""Unit tests for LLM retry logic in core/llm_client.py.

Focused on the retry loop behavior of call_llm():
- Successful first attempt (no retry)
- Retry on retriable status codes (429, 500, 502, 503, 504)
- Retry on network timeout
- LLMRetryExhausted after 4 total attempts
- LLMRateLimitExceeded when Retry-After > 60s
- Retry-After header respected as delay value
- Non-retriable errors (400, 401, 403) raise immediately
- Exponential backoff delays: 1s, 2s, 4s

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7
"""

from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest

from cloud_janitor.core.llm_client import (
    LLMRateLimitExceeded,
    LLMRetryExhausted,
    call_llm,
)


# ---------------------------------------------------------------------------
# Helpers to construct realistic OpenAI exceptions
# ---------------------------------------------------------------------------


def _make_httpx_response(status_code: int, headers: dict | None = None) -> httpx.Response:
    """Build a real httpx.Response for OpenAI exception constructors."""
    resp = httpx.Response(
        status_code=status_code,
        headers=headers or {},
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )
    return resp


def _rate_limit_error(retry_after: str | None = None) -> openai.RateLimitError:
    """Create a RateLimitError with optional Retry-After header."""
    headers = {"Retry-After": retry_after} if retry_after else {}
    response = _make_httpx_response(429, headers)
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


def _client_error(status_code: int) -> openai.APIStatusError:
    """Create a non-retriable client error (400, 401, 403)."""
    response = _make_httpx_response(status_code)
    return openai.APIStatusError(
        message=f"Client error {status_code}",
        response=response,
        body=None,
    )


# ---------------------------------------------------------------------------
# Test: Successful call on first attempt (no retry)
# ---------------------------------------------------------------------------


class TestSuccessOnFirstAttempt:
    """Validates: Requirements 8.1, 8.2, 8.3"""

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_returns_response_without_sleeping(self, mock_sleep):
        """When the first call succeeds, no retry and no sleep occur."""
        client = MagicMock()
        expected_response = MagicMock(spec=openai.types.chat.ChatCompletion)
        client.chat.completions.create.return_value = expected_response

        result = call_llm(client, model="test-model", messages=[{"role": "user", "content": "hi"}])

        assert result is expected_response
        client.chat.completions.create.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_forwards_all_kwargs(self, mock_sleep):
        """call_llm passes all keyword arguments to the client."""
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock()

        call_llm(client, model="gpt-4", messages=[], temperature=0.7, max_tokens=200)

        client.chat.completions.create.assert_called_once_with(
            model="gpt-4", messages=[], temperature=0.7, max_tokens=200
        )
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Test: Retry on retriable status codes — verify correct attempt count
# ---------------------------------------------------------------------------


class TestRetryOnRetriableStatusCodes:
    """Validates: Requirements 8.1, 8.2"""

    @pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_retries_once_then_succeeds(self, mock_sleep, status_code):
        """call_llm retries on each retriable status code, succeeding on attempt 2."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)

        if status_code == 429:
            error = _rate_limit_error()
        else:
            error = _server_error(status_code)

        client.chat.completions.create.side_effect = [error, expected]

        result = call_llm(client, model="m", messages=[])

        assert result is expected
        assert client.chat.completions.create.call_count == 2

    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_retries_twice_then_succeeds(self, mock_sleep, status_code):
        """call_llm retries through 2 failures, succeeds on attempt 3."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)

        error = _server_error(status_code)
        client.chat.completions.create.side_effect = [error, error, expected]

        result = call_llm(client, model="m", messages=[])

        assert result is expected
        assert client.chat.completions.create.call_count == 3

    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_retries_three_times_then_succeeds(self, mock_sleep, status_code):
        """call_llm retries through 3 failures, succeeds on attempt 4 (last chance)."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)

        error = _server_error(status_code)
        client.chat.completions.create.side_effect = [error, error, error, expected]

        result = call_llm(client, model="m", messages=[])

        assert result is expected
        assert client.chat.completions.create.call_count == 4


# ---------------------------------------------------------------------------
# Test: Retry on network timeout
# ---------------------------------------------------------------------------


class TestRetryOnNetworkTimeout:
    """Validates: Requirements 8.3"""

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_retries_on_timeout_then_succeeds(self, mock_sleep):
        """call_llm retries on APITimeoutError and returns success on next attempt."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)

        client.chat.completions.create.side_effect = [_timeout_error(), expected]

        result = call_llm(client, model="m", messages=[])

        assert result is expected
        assert client.chat.completions.create.call_count == 2
        mock_sleep.assert_called_once_with(1.0)

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_retries_multiple_timeouts_then_succeeds(self, mock_sleep):
        """call_llm retries through multiple timeouts until success."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)

        client.chat.completions.create.side_effect = [
            _timeout_error(),
            _timeout_error(),
            expected,
        ]

        result = call_llm(client, model="m", messages=[])

        assert result is expected
        assert client.chat.completions.create.call_count == 3

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_timeout_exhaustion_reports_timeout_status(self, mock_sleep):
        """When all attempts timeout, LLMRetryExhausted has status_or_error='timeout'."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _timeout_error()

        with pytest.raises(LLMRetryExhausted) as exc_info:
            call_llm(client, model="m", messages=[])

        assert exc_info.value.status_or_error == "timeout"
        assert exc_info.value.attempts == 4


# ---------------------------------------------------------------------------
# Test: LLMRetryExhausted raised after 4 total attempts with correct fields
# ---------------------------------------------------------------------------


class TestRetryExhausted:
    """Validates: Requirements 8.4"""

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_raised_after_4_attempts_on_500(self, mock_sleep):
        """LLMRetryExhausted raised with correct fields after 4 attempts on HTTP 500."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _server_error(500)

        with pytest.raises(LLMRetryExhausted) as exc_info:
            call_llm(client, model="m", messages=[])

        exc = exc_info.value
        assert exc.status_or_error == "HTTP 500"
        assert exc.attempts == 4
        assert isinstance(exc.elapsed, float)
        assert exc.elapsed >= 0
        assert client.chat.completions.create.call_count == 4

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_raised_after_4_attempts_on_429(self, mock_sleep):
        """LLMRetryExhausted raised after 4 attempts on HTTP 429 without large Retry-After."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _rate_limit_error()

        with pytest.raises(LLMRetryExhausted) as exc_info:
            call_llm(client, model="m", messages=[])

        exc = exc_info.value
        assert exc.status_or_error == "HTTP 429"
        assert exc.attempts == 4
        assert isinstance(exc.elapsed, float)
        assert exc.elapsed >= 0

    @patch("cloud_janitor.core.llm_client.time.sleep")
    @patch("cloud_janitor.core.llm_client.time.monotonic")
    def test_elapsed_time_reflects_monotonic_clock(self, mock_monotonic, mock_sleep):
        """LLMRetryExhausted.elapsed reflects difference in time.monotonic() calls."""
        # Simulate real time passing: start at 100.0, end at 107.5
        mock_monotonic.side_effect = [100.0, 107.5]
        client = MagicMock()
        client.chat.completions.create.side_effect = _server_error(502)

        with pytest.raises(LLMRetryExhausted) as exc_info:
            call_llm(client, model="m", messages=[])

        assert exc_info.value.elapsed == pytest.approx(7.5)
        assert isinstance(exc_info.value.elapsed, float)

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_sleep_called_3_times_before_exhaustion(self, mock_sleep):
        """3 sleeps occur (between attempts 1-2, 2-3, 3-4) before exhaustion."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _server_error(504)

        with pytest.raises(LLMRetryExhausted):
            call_llm(client, model="m", messages=[])

        assert mock_sleep.call_count == 3


# ---------------------------------------------------------------------------
# Test: LLMRateLimitExceeded raised when Retry-After > 60s
# ---------------------------------------------------------------------------


class TestRateLimitExceeded:
    """Validates: Requirements 8.7"""

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_raised_immediately_when_retry_after_exceeds_60(self, mock_sleep):
        """LLMRateLimitExceeded raised on first 429 with Retry-After > 60."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _rate_limit_error(retry_after="120")

        with pytest.raises(LLMRateLimitExceeded) as exc_info:
            call_llm(client, model="m", messages=[])

        assert exc_info.value.retry_after == 120.0
        mock_sleep.assert_not_called()
        # Only 1 attempt made — raises immediately on first failure
        assert client.chat.completions.create.call_count == 1

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_raised_with_boundary_value_61(self, mock_sleep):
        """LLMRateLimitExceeded raised when Retry-After is exactly 61 (just over max)."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _rate_limit_error(retry_after="61")

        with pytest.raises(LLMRateLimitExceeded) as exc_info:
            call_llm(client, model="m", messages=[])

        assert exc_info.value.retry_after == 61.0

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_not_raised_when_retry_after_equals_60(self, mock_sleep):
        """Retry-After == 60 does NOT raise LLMRateLimitExceeded — it's within limit."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)
        client.chat.completions.create.side_effect = [
            _rate_limit_error(retry_after="60"),
            expected,
        ]

        result = call_llm(client, model="m", messages=[])

        assert result is expected
        mock_sleep.assert_called_once_with(60.0)


# ---------------------------------------------------------------------------
# Test: Retry-After header respected (delay equals header value)
# ---------------------------------------------------------------------------


class TestRetryAfterHeaderRespected:
    """Validates: Requirements 8.6"""

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_uses_retry_after_value_as_delay(self, mock_sleep):
        """When Retry-After is 5, sleep is called with 5.0 instead of backoff."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)
        client.chat.completions.create.side_effect = [
            _rate_limit_error(retry_after="5"),
            expected,
        ]

        call_llm(client, model="m", messages=[])

        mock_sleep.assert_called_once_with(5.0)

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_uses_retry_after_30_as_delay(self, mock_sleep):
        """When Retry-After is 30, sleep is called with 30.0."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)
        client.chat.completions.create.side_effect = [
            _rate_limit_error(retry_after="30"),
            expected,
        ]

        call_llm(client, model="m", messages=[])

        mock_sleep.assert_called_once_with(30.0)

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_retry_after_overrides_exponential_backoff(self, mock_sleep):
        """Retry-After header takes precedence over calculated exponential backoff."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)
        # Fail twice with Retry-After, succeed on 3rd
        client.chat.completions.create.side_effect = [
            _rate_limit_error(retry_after="10"),
            _rate_limit_error(retry_after="15"),
            expected,
        ]

        call_llm(client, model="m", messages=[])

        # Both sleeps should use Retry-After values, not 1s/2s backoff
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [10.0, 15.0]

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_falls_back_to_backoff_when_no_retry_after(self, mock_sleep):
        """Without Retry-After header, 429 uses exponential backoff like other errors."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)
        client.chat.completions.create.side_effect = [
            _rate_limit_error(),  # No Retry-After
            expected,
        ]

        call_llm(client, model="m", messages=[])

        # Should use backoff: 1.0 * 2^0 = 1.0
        mock_sleep.assert_called_once_with(1.0)


# ---------------------------------------------------------------------------
# Test: Non-retriable errors (400, 401, 403) raise immediately
# ---------------------------------------------------------------------------


class TestNonRetriableErrors:
    """Validates: Requirements 8.1, 8.2 (by exclusion — only listed codes are retried)"""

    @pytest.mark.parametrize("status_code", [400, 401, 403])
    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_raises_immediately_without_retry(self, mock_sleep, status_code):
        """Non-retriable errors propagate immediately without any retry."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _client_error(status_code)

        with pytest.raises(openai.APIStatusError) as exc_info:
            call_llm(client, model="m", messages=[])

        assert exc_info.value.status_code == status_code
        assert client.chat.completions.create.call_count == 1
        mock_sleep.assert_not_called()

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_400_not_wrapped_in_retry_exhausted(self, mock_sleep):
        """HTTP 400 raises the original APIStatusError, not LLMRetryExhausted."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _client_error(400)

        with pytest.raises(openai.APIStatusError) as exc_info:
            call_llm(client, model="m", messages=[])

        assert not isinstance(exc_info.value, LLMRetryExhausted)

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_401_raises_immediately(self, mock_sleep):
        """HTTP 401 (Unauthorized) raises immediately — no retries."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _client_error(401)

        with pytest.raises(openai.APIStatusError) as exc_info:
            call_llm(client, model="m", messages=[])

        assert exc_info.value.status_code == 401
        mock_sleep.assert_not_called()

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_403_raises_immediately(self, mock_sleep):
        """HTTP 403 (Forbidden) raises immediately — no retries."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _client_error(403)

        with pytest.raises(openai.APIStatusError) as exc_info:
            call_llm(client, model="m", messages=[])

        assert exc_info.value.status_code == 403
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Test: Exponential backoff delays: 1s, 2s, 4s
# ---------------------------------------------------------------------------


class TestExponentialBackoff:
    """Validates: Requirements 8.6"""

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_backoff_sequence_is_1_2_4(self, mock_sleep):
        """Sleep calls follow 1s, 2s, 4s for retries 1, 2, 3 respectively."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _server_error(503)

        with pytest.raises(LLMRetryExhausted):
            call_llm(client, model="m", messages=[])

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0, 4.0]

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_first_retry_delay_is_1_second(self, mock_sleep):
        """First retry sleeps exactly 1 second (BASE_DELAY * 2^0)."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)
        client.chat.completions.create.side_effect = [_server_error(500), expected]

        call_llm(client, model="m", messages=[])

        mock_sleep.assert_called_once_with(1.0)

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_second_retry_delay_is_2_seconds(self, mock_sleep):
        """Second retry sleeps exactly 2 seconds (BASE_DELAY * 2^1)."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)
        client.chat.completions.create.side_effect = [
            _server_error(500),
            _server_error(500),
            expected,
        ]

        call_llm(client, model="m", messages=[])

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0]

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_third_retry_delay_is_4_seconds(self, mock_sleep):
        """Third retry sleeps exactly 4 seconds (BASE_DELAY * 2^2)."""
        client = MagicMock()
        expected = MagicMock(spec=openai.types.chat.ChatCompletion)
        client.chat.completions.create.side_effect = [
            _server_error(500),
            _server_error(500),
            _server_error(500),
            expected,
        ]

        call_llm(client, model="m", messages=[])

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0, 4.0]

    @patch("cloud_janitor.core.llm_client.time.sleep")
    def test_timeout_errors_use_same_backoff_formula(self, mock_sleep):
        """Timeout errors also use 1s, 2s, 4s backoff (same formula)."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _timeout_error()

        with pytest.raises(LLMRetryExhausted):
            call_llm(client, model="m", messages=[])

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0, 4.0]
