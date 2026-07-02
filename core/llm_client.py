"""Shared LLM client module for Cloud Janitor.

All AI agents import from this module instead of using the OpenAI SDK directly.
Routes all LLM calls through OpenRouter's OpenAI-compatible API.

Includes:
- 30-second timeout (connect: 5s, read: 25s) to prevent indefinite hangs
- Automatic retry with exponential backoff (3 attempts) for transient failures
"""

import os

from dotenv import load_dotenv

load_dotenv()  # loads .env from project root if present, no-op otherwise

import httpx
import openai
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception


# Timeout: 5s connect, 25s read, 30s total
_LLM_TIMEOUT = httpx.Timeout(30.0, connect=5.0)

# Retry config: 3 attempts, 1s → 2s → 4s backoff
_MAX_RETRIES = 3


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient errors worth retrying."""
    # Retry on timeout
    if isinstance(exc, (httpx.TimeoutException, openai.APITimeoutError)):
        return True
    # Retry on 429 (rate limit) and 5xx (server errors)
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code in (429, 500, 502, 503, 504)
    # Retry on network-level errors
    if isinstance(exc, (httpx.ConnectError, httpx.ReadError)):
        return True
    return False


def get_client() -> openai.OpenAI:
    """Return an OpenAI client configured for OpenRouter with timeout.

    The client has a 30-second timeout (5s connect, 25s read) to prevent
    indefinite hangs when OpenRouter is slow or unreachable.

    Raises:
        EnvironmentError: If OPENROUTER_API_KEY is not set.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENROUTER_API_KEY is not set")
    return openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        timeout=_LLM_TIMEOUT,
        max_retries=_MAX_RETRIES,
    )


DEFAULT_MODEL: str = os.environ.get("JANITOR_LLM_MODEL", "anthropic/claude-haiku-4-5")