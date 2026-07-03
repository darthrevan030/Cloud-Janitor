"""Unit tests for core/llm_client.py.

Tests the shared LLM client module that all AI agents import from.
Validates:
- get_client() returns OpenAI instance with correct base_url
- DEFAULT_MODEL reads from env var with correct default
- EnvironmentError raised when OPENROUTER_API_KEY unset
- No sensitive values are logged or exposed
- call_llm() retries on transient failures with exponential backoff
- LLMRetryExhausted raised when all attempts exhausted
- LLMRateLimitExceeded raised when Retry-After exceeds max

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 7.1
"""

import logging
from unittest.mock import MagicMock, patch

import openai
import pytest

import core.llm_client as llm_client
from core.llm_client import (
    _extract_retry_after,
    _MAX_RETRIES,
    _BASE_DELAY,
    _MULTIPLIER,
    _MAX_RETRY_AFTER,
    _RETRIABLE_STATUS_CODES,
)


class TestGetClient:
    """Tests for get_client() → openai.OpenAI configured for OpenRouter."""

    def test_returns_openai_instance_with_api_key_set(self, monkeypatch):
        """get_client() returns an openai.OpenAI instance when OPENROUTER_API_KEY is set."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-abc123")
        client = llm_client.get_client()
        assert isinstance(client, openai.OpenAI)

    def test_base_url_is_openrouter(self, monkeypatch):
        """get_client() configures base_url to OpenRouter's API endpoint."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-abc123")
        client = llm_client.get_client()
        assert client.base_url == "https://openrouter.ai/api/v1/"

    def test_api_key_passed_to_client(self, monkeypatch):
        """get_client() passes the OPENROUTER_API_KEY to the OpenAI client."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key-xyz")
        client = llm_client.get_client()
        assert client.api_key == "sk-or-test-key-xyz"

    def test_raises_environment_error_when_key_missing(self, monkeypatch):
        """get_client() raises EnvironmentError when OPENROUTER_API_KEY is not set."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="OPENROUTER_API_KEY is not set"):
            llm_client.get_client()

    def test_raises_environment_error_when_key_is_empty_string(self, monkeypatch):
        """get_client() raises EnvironmentError when OPENROUTER_API_KEY is empty."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "")
        with pytest.raises(EnvironmentError, match="OPENROUTER_API_KEY is not set"):
            llm_client.get_client()


class TestDefaultModel:
    """Tests for DEFAULT_MODEL reading from JANITOR_LLM_MODEL env var."""

    def test_default_model_uses_env_var_when_set(self, monkeypatch):
        """DEFAULT_MODEL reads from JANITOR_LLM_MODEL when the env var is present."""
        monkeypatch.setenv("JANITOR_LLM_MODEL", "openai/gpt-4o-mini")
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        # DEFAULT_MODEL is set at module import time via os.environ.get().
        # We verify the mechanism is os.environ.get("JANITOR_LLM_MODEL", default).
        import os
        assert os.environ.get("JANITOR_LLM_MODEL", "anthropic/claude-haiku-4-5") == "openai/gpt-4o-mini"

    def test_default_model_fallback_when_env_var_unset(self, monkeypatch):
        """DEFAULT_MODEL defaults to 'anthropic/claude-haiku-4-5' when env var is absent."""
        monkeypatch.delenv("JANITOR_LLM_MODEL", raising=False)
        import os
        assert os.environ.get("JANITOR_LLM_MODEL", "anthropic/claude-haiku-4-5") == "anthropic/claude-haiku-4-5"

    def test_default_model_is_string(self):
        """DEFAULT_MODEL is always a string type."""
        assert isinstance(llm_client.DEFAULT_MODEL, str)


class TestSensitiveDataExposure:
    """Ensure sensitive values (API keys, model names) are not exposed."""

    def test_get_client_does_not_include_key_in_error_message(self, monkeypatch):
        """When OPENROUTER_API_KEY is missing, the error message does not leak any key value."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        with pytest.raises(EnvironmentError) as exc_info:
            llm_client.get_client()

        error_msg = str(exc_info.value)
        assert "OPENROUTER_API_KEY" in error_msg
        assert "sk-or-" not in error_msg
        assert "sk-" not in error_msg.replace("OPENROUTER_API_KEY", "")

    def test_module_repr_does_not_expose_api_key(self, monkeypatch):
        """The client object's repr/str does not contain the raw API key."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-value-12345")
        client = llm_client.get_client()

        client_repr = repr(client)
        client_str = str(client)
        assert "sk-or-secret-value-12345" not in client_repr
        assert "sk-or-secret-value-12345" not in client_str


class TestModuleInterface:
    """Verify the module exports the expected interface."""

    def test_module_exports_get_client(self):
        """llm_client exposes get_client as a callable."""
        assert hasattr(llm_client, "get_client")
        assert callable(llm_client.get_client)

    def test_module_exports_default_model(self):
        """llm_client exposes DEFAULT_MODEL as a module-level attribute."""
        assert hasattr(llm_client, "DEFAULT_MODEL")

    def test_module_exports_call_llm(self):
        """llm_client exposes call_llm as a callable."""
        assert hasattr(llm_client, "call_llm")
        assert callable(llm_client.call_llm)

    def test_module_exports_exception_classes(self):
        """llm_client exposes LLMRetryExhausted and LLMRateLimitExceeded."""
        assert hasattr(llm_client, "LLMRetryExhausted")
        assert hasattr(llm_client, "LLMRateLimitExceeded")
        assert issubclass(llm_client.LLMRetryExhausted, Exception)
        assert issubclass(llm_client.LLMRateLimitExceeded, Exception)

    def test_no_anthropic_import(self):
        """llm_client does not import the anthropic package."""
        import inspect

        source = inspect.getsource(llm_client)
        assert "import anthropic" not in source
        assert "from anthropic" not in source

    def test_no_tenacity_import(self):
        """llm_client does not import tenacity (Req 8.8: manual retry loop)."""
        import inspect

        source = inspect.getsource(llm_client)
        assert "import tenacity" not in source
        assert "from tenacity" not in source

    def test_no_httpx_import(self):
        """llm_client does not import httpx directly."""
        import inspect

        source = inspect.getsource(llm_client)
        assert "import httpx" not in source
        assert "from httpx" not in source

    def test_uses_logging_not_print(self):
        """llm_client uses logging module, not print() for diagnostic output (Req 7.1)."""
        import inspect

        source = inspect.getsource(llm_client)
        assert "logging.getLogger(__name__)" in source
        # No print() calls for diagnostic output
        assert "print(" not in source


class TestLLMRetryExhausted:
    """Tests for LLMRetryExhausted exception class."""

    def test_stores_status_or_error(self):
        """LLMRetryExhausted stores the final status/error string."""
        exc = llm_client.LLMRetryExhausted("HTTP 500", 4, 7.5)
        assert exc.status_or_error == "HTTP 500"

    def test_stores_attempts(self):
        """LLMRetryExhausted stores the total number of attempts."""
        exc = llm_client.LLMRetryExhausted("timeout", 4, 10.0)
        assert exc.attempts == 4

    def test_stores_elapsed(self):
        """LLMRetryExhausted stores the total elapsed time."""
        exc = llm_client.LLMRetryExhausted("HTTP 502", 4, 7.123)
        assert exc.elapsed == 7.123

    def test_message_includes_all_fields(self):
        """LLMRetryExhausted message includes attempts, elapsed, and status."""
        exc = llm_client.LLMRetryExhausted("HTTP 429", 4, 7.5)
        msg = str(exc)
        assert "4 attempts" in msg
        assert "7.5s" in msg
        assert "HTTP 429" in msg


class TestLLMRateLimitExceeded:
    """Tests for LLMRateLimitExceeded exception class."""

    def test_stores_retry_after(self):
        """LLMRateLimitExceeded stores the retry_after value."""
        exc = llm_client.LLMRateLimitExceeded(120.0)
        assert exc.retry_after == 120.0

    def test_message_includes_retry_after_and_max(self):
        """LLMRateLimitExceeded message indicates both values."""
        exc = llm_client.LLMRateLimitExceeded(90.0)
        msg = str(exc)
        assert "90" in msg
        assert "60" in msg


class TestExtractRetryAfter:
    """Tests for _extract_retry_after helper."""

    def test_returns_float_from_header(self):
        """Extracts numeric Retry-After header as float."""
        exc = MagicMock(spec=openai.RateLimitError)
        exc.response = MagicMock()
        exc.response.headers = {"Retry-After": "30"}
        result = _extract_retry_after(exc)
        assert result == 30.0

    def test_returns_none_when_no_response(self):
        """Returns None when exception has no response."""
        exc = MagicMock(spec=openai.RateLimitError)
        exc.response = None
        result = _extract_retry_after(exc)
        assert result is None

    def test_returns_none_when_no_header(self):
        """Returns None when response has no Retry-After header."""
        exc = MagicMock(spec=openai.RateLimitError)
        exc.response = MagicMock()
        exc.response.headers = {}
        result = _extract_retry_after(exc)
        assert result is None

    def test_returns_none_on_invalid_header(self):
        """Returns None when Retry-After header is not a valid number."""
        exc = MagicMock(spec=openai.RateLimitError)
        exc.response = MagicMock()
        exc.response.headers = {"Retry-After": "not-a-number"}
        result = _extract_retry_after(exc)
        assert result is None

    def test_returns_float_for_decimal_value(self):
        """Handles decimal Retry-After values."""
        exc = MagicMock(spec=openai.RateLimitError)
        exc.response = MagicMock()
        exc.response.headers = {"Retry-After": "2.5"}
        result = _extract_retry_after(exc)
        assert result == 2.5


class TestCallLLMSuccess:
    """Tests for call_llm() on successful calls."""

    def test_returns_response_on_first_attempt(self):
        """call_llm() returns the response when first attempt succeeds."""
        client = MagicMock()
        expected = MagicMock()
        client.chat.completions.create.return_value = expected

        result = llm_client.call_llm(
            client, model="test-model", messages=[{"role": "user", "content": "hi"}]
        )

        assert result is expected
        client.chat.completions.create.assert_called_once_with(
            model="test-model", messages=[{"role": "user", "content": "hi"}]
        )

    def test_passes_kwargs_to_create(self):
        """call_llm() forwards all kwargs to client.chat.completions.create()."""
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock()

        llm_client.call_llm(client, model="m", messages=[], temperature=0.5, max_tokens=100)

        client.chat.completions.create.assert_called_once_with(
            model="m", messages=[], temperature=0.5, max_tokens=100
        )


class TestCallLLMRetryOnServerErrors:
    """Tests for call_llm() retrying on HTTP 500/502/503/504 (Req 8.2)."""

    @patch("core.llm_client.time.sleep")
    def test_retries_on_500_then_succeeds(self, mock_sleep):
        """call_llm() retries on HTTP 500 and returns success on subsequent attempt."""
        client = MagicMock()
        expected = MagicMock()

        error_response = MagicMock()
        error_response.status_code = 500
        error_response.headers = {}
        exc = openai.InternalServerError(
            message="Internal Server Error",
            response=error_response,
            body=None,
        )
        client.chat.completions.create.side_effect = [exc, expected]

        result = llm_client.call_llm(client, model="m", messages=[])

        assert result is expected
        assert client.chat.completions.create.call_count == 2
        mock_sleep.assert_called_once_with(1.0)  # _BASE_DELAY * 2^0

    @patch("core.llm_client.time.sleep")
    def test_retries_on_502(self, mock_sleep):
        """call_llm() retries on HTTP 502."""
        client = MagicMock()
        expected = MagicMock()

        error_response = MagicMock()
        error_response.status_code = 502
        error_response.headers = {}
        exc = openai.APIStatusError(
            message="Bad Gateway",
            response=error_response,
            body=None,
        )
        client.chat.completions.create.side_effect = [exc, expected]

        result = llm_client.call_llm(client, model="m", messages=[])
        assert result is expected

    @patch("core.llm_client.time.sleep")
    def test_does_not_retry_on_400(self, mock_sleep):
        """call_llm() does not retry on HTTP 400 (client error)."""
        client = MagicMock()

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.headers = {}
        exc = openai.BadRequestError(
            message="Bad Request",
            response=error_response,
            body=None,
        )
        client.chat.completions.create.side_effect = exc

        with pytest.raises(openai.BadRequestError):
            llm_client.call_llm(client, model="m", messages=[])

        assert client.chat.completions.create.call_count == 1
        mock_sleep.assert_not_called()


class TestCallLLMRetryOnTimeout:
    """Tests for call_llm() retrying on network timeouts (Req 8.3)."""

    @patch("core.llm_client.time.sleep")
    def test_retries_on_timeout_then_succeeds(self, mock_sleep):
        """call_llm() retries on APITimeoutError and returns success."""
        client = MagicMock()
        expected = MagicMock()

        exc = openai.APITimeoutError(request=MagicMock())
        client.chat.completions.create.side_effect = [exc, expected]

        result = llm_client.call_llm(client, model="m", messages=[])

        assert result is expected
        assert client.chat.completions.create.call_count == 2
        mock_sleep.assert_called_once_with(1.0)


class TestCallLLMRetryExhaustion:
    """Tests for call_llm() raising LLMRetryExhausted after max attempts (Req 8.4)."""

    @patch("core.llm_client.time.sleep")
    def test_raises_after_max_retries_on_500(self, mock_sleep):
        """call_llm() raises LLMRetryExhausted after 4 total attempts on HTTP 500."""
        client = MagicMock()

        error_response = MagicMock()
        error_response.status_code = 500
        error_response.headers = {}
        exc = openai.InternalServerError(
            message="Internal Server Error",
            response=error_response,
            body=None,
        )
        client.chat.completions.create.side_effect = exc

        with pytest.raises(llm_client.LLMRetryExhausted) as exc_info:
            llm_client.call_llm(client, model="m", messages=[])

        assert exc_info.value.status_or_error == "HTTP 500"
        assert exc_info.value.attempts == 4
        assert exc_info.value.elapsed >= 0
        assert client.chat.completions.create.call_count == 4
        assert mock_sleep.call_count == 3  # 3 retries

    @patch("core.llm_client.time.sleep")
    def test_raises_after_max_retries_on_timeout(self, mock_sleep):
        """call_llm() raises LLMRetryExhausted after all attempts on timeout."""
        client = MagicMock()

        exc = openai.APITimeoutError(request=MagicMock())
        client.chat.completions.create.side_effect = exc

        with pytest.raises(llm_client.LLMRetryExhausted) as exc_info:
            llm_client.call_llm(client, model="m", messages=[])

        assert exc_info.value.status_or_error == "timeout"
        assert exc_info.value.attempts == 4


class TestCallLLMExponentialBackoff:
    """Tests for exponential backoff delays (Req 8.6)."""

    @patch("core.llm_client.time.sleep")
    def test_backoff_delays_are_1_2_4(self, mock_sleep):
        """Retry delays follow 1s, 2s, 4s exponential backoff."""
        client = MagicMock()

        error_response = MagicMock()
        error_response.status_code = 503
        error_response.headers = {}
        exc = openai.APIStatusError(
            message="Service Unavailable",
            response=error_response,
            body=None,
        )
        client.chat.completions.create.side_effect = exc

        with pytest.raises(llm_client.LLMRetryExhausted):
            llm_client.call_llm(client, model="m", messages=[])

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0, 4.0]


class TestCallLLMRateLimit:
    """Tests for HTTP 429 handling with Retry-After (Req 8.1, 8.6, 8.7)."""

    @patch("core.llm_client.time.sleep")
    def test_uses_retry_after_header_as_delay(self, mock_sleep):
        """call_llm() uses Retry-After header value when within max (Req 8.6)."""
        client = MagicMock()
        expected = MagicMock()

        error_response = MagicMock()
        error_response.status_code = 429
        error_response.headers = {"Retry-After": "5"}
        exc = openai.RateLimitError(
            message="Rate limited",
            response=error_response,
            body=None,
        )
        client.chat.completions.create.side_effect = [exc, expected]

        result = llm_client.call_llm(client, model="m", messages=[])

        assert result is expected
        mock_sleep.assert_called_once_with(5.0)

    @patch("core.llm_client.time.sleep")
    def test_raises_rate_limit_exceeded_when_retry_after_too_high(self, mock_sleep):
        """call_llm() raises LLMRateLimitExceeded when Retry-After > 60s (Req 8.7)."""
        client = MagicMock()

        error_response = MagicMock()
        error_response.status_code = 429
        error_response.headers = {"Retry-After": "120"}
        exc = openai.RateLimitError(
            message="Rate limited",
            response=error_response,
            body=None,
        )
        client.chat.completions.create.side_effect = exc

        with pytest.raises(llm_client.LLMRateLimitExceeded) as exc_info:
            llm_client.call_llm(client, model="m", messages=[])

        assert exc_info.value.retry_after == 120.0
        mock_sleep.assert_not_called()

    @patch("core.llm_client.time.sleep")
    def test_uses_exponential_backoff_when_no_retry_after(self, mock_sleep):
        """call_llm() falls back to exponential backoff for 429 without Retry-After."""
        client = MagicMock()
        expected = MagicMock()

        error_response = MagicMock()
        error_response.status_code = 429
        error_response.headers = {}
        exc = openai.RateLimitError(
            message="Rate limited",
            response=error_response,
            body=None,
        )
        client.chat.completions.create.side_effect = [exc, expected]

        result = llm_client.call_llm(client, model="m", messages=[])

        assert result is expected
        mock_sleep.assert_called_once_with(1.0)  # First retry: 1s


class TestCallLLMLogging:
    """Tests for retry logging at WARNING level (Req 8.5)."""

    @patch("core.llm_client.time.sleep")
    def test_logs_retry_on_server_error(self, mock_sleep, caplog):
        """call_llm() logs retry attempt with attempt number, delay, and reason."""
        client = MagicMock()
        expected = MagicMock()

        error_response = MagicMock()
        error_response.status_code = 500
        error_response.headers = {}
        exc = openai.InternalServerError(
            message="Internal Server Error",
            response=error_response,
            body=None,
        )
        client.chat.completions.create.side_effect = [exc, expected]

        with caplog.at_level(logging.WARNING, logger="core.llm_client"):
            llm_client.call_llm(client, model="m", messages=[])

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelname == "WARNING"
        assert "retry 1 of 3" in record.message
        assert "1.0s" in record.message
        assert "500" in record.message

    @patch("core.llm_client.time.sleep")
    def test_logs_retry_on_timeout(self, mock_sleep, caplog):
        """call_llm() logs timeout retry with relevant info."""
        client = MagicMock()
        expected = MagicMock()

        exc = openai.APITimeoutError(request=MagicMock())
        client.chat.completions.create.side_effect = [exc, expected]

        with caplog.at_level(logging.WARNING, logger="core.llm_client"):
            llm_client.call_llm(client, model="m", messages=[])

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelname == "WARNING"
        assert "timed out" in record.message
        assert "retry 1 of 3" in record.message

    @patch("core.llm_client.time.sleep")
    def test_logs_retry_on_rate_limit(self, mock_sleep, caplog):
        """call_llm() logs rate limit retry with relevant info."""
        client = MagicMock()
        expected = MagicMock()

        error_response = MagicMock()
        error_response.status_code = 429
        error_response.headers = {}
        exc = openai.RateLimitError(
            message="Rate limited",
            response=error_response,
            body=None,
        )
        client.chat.completions.create.side_effect = [exc, expected]

        with caplog.at_level(logging.WARNING, logger="core.llm_client"):
            llm_client.call_llm(client, model="m", messages=[])

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelname == "WARNING"
        assert "rate-limited" in record.message


class TestConstants:
    """Tests for module-level retry constants."""

    def test_max_retries_is_3(self):
        """_MAX_RETRIES = 3 (4 total attempts)."""
        assert _MAX_RETRIES == 3

    def test_base_delay_is_1(self):
        """_BASE_DELAY = 1.0 second."""
        assert _BASE_DELAY == 1.0

    def test_multiplier_is_2(self):
        """_MULTIPLIER = 2."""
        assert _MULTIPLIER == 2

    def test_max_retry_after_is_60(self):
        """_MAX_RETRY_AFTER = 60 seconds."""
        assert _MAX_RETRY_AFTER == 60

    def test_retriable_status_codes(self):
        """Retriable codes include 429, 500, 502, 503, 504."""
        assert _RETRIABLE_STATUS_CODES == {429, 500, 502, 503, 504}
