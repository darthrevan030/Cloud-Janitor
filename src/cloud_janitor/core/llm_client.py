"""Shared LLM client module for Cloud Janitor.

All AI agents import from this module instead of using the OpenAI SDK directly.
Routes all LLM calls through OpenRouter's OpenAI-compatible API.

Includes:
- 30-second timeout to prevent indefinite hangs
- Manual retry loop with exponential backoff (max 3 retries, 4 total attempts)
- Respects Retry-After header for HTTP 429 (up to 60s max)
- Structured logging via logging module (no print statements)
"""

import logging
import os
import time

from dotenv import load_dotenv

load_dotenv()  # loads .env from project root if present, no-op otherwise

import openai  # noqa: E402

logger = logging.getLogger(__name__)

# Retry configuration
_MAX_RETRIES = 3  # 4 total attempts including original
_BASE_DELAY = 1.0  # seconds
_MULTIPLIER = 2
_TIMEOUT = 30  # seconds
_MAX_RETRY_AFTER = 60  # seconds — above this, don't wait
_RETRIABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class LLMRetryExhausted(Exception):
    """Raised when all retry attempts for an LLM call have been exhausted."""

    def __init__(self, status_or_error: str, attempts: int, elapsed: float):
        self.status_or_error = status_or_error
        self.attempts = attempts
        self.elapsed = elapsed
        super().__init__(
            f"LLM call failed after {attempts} attempts ({elapsed:.1f}s): {status_or_error}"
        )


class LLMRateLimitExceeded(Exception):
    """Raised when Retry-After header exceeds the maximum allowable delay."""

    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(
            f"Rate limit Retry-After ({retry_after:.0f}s) exceeds maximum ({_MAX_RETRY_AFTER}s)"
        )


def _extract_retry_after(exc: openai.RateLimitError) -> float | None:
    """Extract Retry-After header value from a RateLimitError response."""
    if hasattr(exc, "response") and exc.response is not None:
        header = exc.response.headers.get("Retry-After")
        if header:
            try:
                return float(header)
            except (ValueError, TypeError):
                pass
    return None


def call_llm(client: openai.OpenAI, **kwargs) -> openai.types.chat.ChatCompletion:
    """Call the LLM with automatic retry and exponential backoff.

    Retries on HTTP 429, 500, 502, 503, 504 and network timeouts.
    Max 3 retries (4 total attempts), exponential backoff: 1s, 2s, 4s.
    Respects Retry-After header for 429 (up to 60s max).

    Args:
        client: An OpenAI client instance (from get_client()).
        **kwargs: Arguments passed to client.chat.completions.create().

    Returns:
        The ChatCompletion response from the API.

    Raises:
        LLMRetryExhausted: If all retry attempts are exhausted.
        LLMRateLimitExceeded: If Retry-After header exceeds 60s.
        openai.APIStatusError: If a non-retriable HTTP error occurs.
    """
    start_time = time.monotonic()
    last_error: str = "unknown"

    for attempt in range(_MAX_RETRIES + 1):  # 0, 1, 2, 3
        try:
            return client.chat.completions.create(**kwargs)

        # ORDER MATTERS: RateLimitError is a subclass of APIStatusError.
        except openai.RateLimitError as exc:
            last_error = "HTTP 429"
            retry_after = _extract_retry_after(exc)

            if retry_after is not None and retry_after > _MAX_RETRY_AFTER:
                raise LLMRateLimitExceeded(retry_after) from exc

            if attempt >= _MAX_RETRIES:
                break

            delay = retry_after if retry_after else _BASE_DELAY * (_MULTIPLIER ** attempt)
            logger.warning(
                "LLM call rate-limited (retry %d of %d), waiting %.1fs",
                attempt + 1, _MAX_RETRIES, delay,
            )
            time.sleep(delay)

        except openai.APIStatusError as exc:
            if exc.status_code not in _RETRIABLE_STATUS_CODES:
                raise

            last_error = f"HTTP {exc.status_code}"
            if attempt >= _MAX_RETRIES:
                break

            delay = _BASE_DELAY * (_MULTIPLIER ** attempt)
            logger.warning(
                "LLM call failed HTTP %d (retry %d of %d), waiting %.1fs",
                exc.status_code, attempt + 1, _MAX_RETRIES, delay,
            )
            time.sleep(delay)

        except openai.APITimeoutError:
            last_error = "timeout"
            if attempt >= _MAX_RETRIES:
                break

            delay = _BASE_DELAY * (_MULTIPLIER ** attempt)
            logger.warning(
                "LLM call timed out (retry %d of %d), waiting %.1fs",
                attempt + 1, _MAX_RETRIES, delay,
            )
            time.sleep(delay)

    elapsed = time.monotonic() - start_time
    raise LLMRetryExhausted(last_error, _MAX_RETRIES + 1, elapsed)


def get_client() -> openai.OpenAI:
    """Return an OpenAI client configured for OpenRouter with timeout.

    The client has a 30-second timeout to prevent indefinite hangs when
    OpenRouter is slow or unreachable. Retry logic is handled externally
    by call_llm(), not by the SDK's built-in retry.

    Raises:
        EnvironmentError: If OPENROUTER_API_KEY is not set.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENROUTER_API_KEY is not set")
    return openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        timeout=_TIMEOUT,
    )


DEFAULT_MODEL: str = os.environ.get("JANITOR_LLM_MODEL", "anthropic/claude-haiku-4-5")
