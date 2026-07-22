"""Shared LLM client module for Cloud Janitor.

All AI agents import from this module instead of using the OpenAI SDK directly.
Routes all LLM calls through a configurable OpenAI-compatible endpoint.

Configuration (via environment variables):
- JANITOR_LLM_BASE_URL: LLM API endpoint (default: https://openrouter.ai/api/v1)
- JANITOR_LLM_API_KEY: API key for the configured endpoint (falls back to OPENROUTER_API_KEY)
- JANITOR_LLM_MODEL: Model identifier (default: anthropic/claude-haiku-4-5)
- JANITOR_AI_ENABLED: Set to "false" to disable all LLM calls (AI kill switch)
- JANITOR_PRIVACY_MODE: Set to "strict" to require no-training provider policies

Includes:
- Configurable timeout (JANITOR_LLM_TIMEOUT, default 30s) to prevent indefinite hangs
- Manual retry loop with exponential backoff (max 3 retries, 4 total attempts)
- Respects Retry-After header for HTTP 429 (up to 60s max)
- Structured logging via logging module (no print statements)
- BYO-endpoint support for enterprise deployments (Bedrock, Azure OpenAI, vLLM)
- AI kill switch for zero-egress operation
- Fail-closed strict mode for provider routing policy
"""

import logging
import os
import time

from dotenv import load_dotenv

load_dotenv()  # loads .env from project root if present, no-op otherwise

import openai  # noqa: E402

from cloud_janitor.core.timeouts import get_timeout  # noqa: E402

logger = logging.getLogger(__name__)

# Retry configuration
_MAX_RETRIES = 3  # 4 total attempts including original
_BASE_DELAY = 1.0  # seconds
_MULTIPLIER = 2
_TIMEOUT = 30  # seconds
_MAX_RETRY_AFTER = 60  # seconds — above this, don't wait
_RETRIABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# AI kill switch — set JANITOR_AI_ENABLED=false to disable all LLM calls
_AI_ENABLED = os.environ.get("JANITOR_AI_ENABLED", "true").lower() not in ("false", "0", "no")

# Privacy mode — "strict" requires no-training provider policies
_PRIVACY_MODE = os.environ.get("JANITOR_PRIVACY_MODE", "").lower()

# Retention policy attestation — must be "none" in strict mode
_RETENTION_POLICY = os.environ.get("JANITOR_LLM_RETENTION_POLICY", "unknown").lower()

# Free-tier router patterns that should be blocked in strict mode
_FREE_ROUTER_PATTERNS = (":free", "/free", "openrouter/auto")


class AIDisabledError(Exception):
    """Raised when an LLM call is attempted but AI is disabled via JANITOR_AI_ENABLED=false."""

    def __init__(self) -> None:
        super().__init__(
            "AI features are disabled (JANITOR_AI_ENABLED=false). "
            "No LLM calls will be made. Set JANITOR_AI_ENABLED=true to re-enable."
        )


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
        AIDisabledError: If JANITOR_AI_ENABLED=false.
        LLMRetryExhausted: If all retry attempts are exhausted.
        LLMRateLimitExceeded: If Retry-After header exceeds 60s.
        openai.APIStatusError: If a non-retriable HTTP error occurs.
    """
    if not _AI_ENABLED:
        raise AIDisabledError()

    # In strict privacy mode, inject provider routing preferences to require
    # no-training/zero-data-retention from the upstream provider.
    if _PRIVACY_MODE == "strict":
        extra_body = kwargs.pop("extra_body", {}) or {}
        extra_body.setdefault("provider", {
            "require_parameters": True,
            "data_collection": "deny",
            "allow_fallbacks": False,
        })
        kwargs["extra_body"] = extra_body

    start_time = time.monotonic()
    last_error: str = "unknown"

    for attempt in range(_MAX_RETRIES + 1):  # 0, 1, 2, 3
        try:
            return client.chat.completions.create(**kwargs)  # type: ignore[no-any-return]

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
    """Return an OpenAI client configured for the LLM endpoint with timeout.

    Supports BYO-endpoint via environment variables:
    - JANITOR_LLM_BASE_URL: Override the API base URL (default: OpenRouter)
    - JANITOR_LLM_API_KEY: Override the API key (falls back to OPENROUTER_API_KEY)

    The client has a 30-second timeout to prevent indefinite hangs when
    the endpoint is slow or unreachable. Retry logic is handled externally
    by call_llm(), not by the SDK's built-in retry.

    Raises:
        AIDisabledError: If JANITOR_AI_ENABLED=false.
        EnvironmentError: If no API key is configured.
        RuntimeError: If strict privacy mode blocks the configured model.
    """
    if not _AI_ENABLED:
        raise AIDisabledError()

    # BYO-endpoint: prefer JANITOR_LLM_* over legacy OPENROUTER_* keys
    base_url = os.environ.get(
        "JANITOR_LLM_BASE_URL",
        "https://openrouter.ai/api/v1",
    )
    api_key = os.environ.get("JANITOR_LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "No LLM API key configured. Set JANITOR_LLM_API_KEY (preferred) "
            "or OPENROUTER_API_KEY."
        )

    # Strict privacy mode: refuse to start if retention policy is not "none"
    if _PRIVACY_MODE == "strict":
        if _RETENTION_POLICY != "none":
            raise RuntimeError(
                "JANITOR_PRIVACY_MODE=strict requires JANITOR_LLM_RETENTION_POLICY=none "
                "(explicit attestation that the configured endpoint retains/trains on no "
                f"data). Currently: {_RETENTION_POLICY!r}."
            )
        model = DEFAULT_MODEL
        if any(pattern in model for pattern in _FREE_ROUTER_PATTERNS):
            raise RuntimeError(
                f"JANITOR_PRIVACY_MODE=strict blocks free-tier model '{model}'. "
                f"Free-tier routes to nondeterministic providers that may train on data. "
                f"Set JANITOR_LLM_MODEL to a paid model or disable strict mode."
            )

    return openai.OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=get_timeout("JANITOR_LLM_TIMEOUT"),
    )


DEFAULT_MODEL: str = os.environ.get("JANITOR_LLM_MODEL", "anthropic/claude-haiku-4-5")


def is_ai_enabled() -> bool:
    """Check if AI features are enabled. Useful for agents to short-circuit."""
    return _AI_ENABLED


def get_privacy_posture() -> dict:
    """Return current privacy configuration as a dict.

    Keys:
        mode: The privacy mode ("strict" or "disabled").
        retention_policy: The LLM retention policy attestation.
        model: The configured default model identifier.
        base_url: The configured LLM API base URL.
    """
    return {
        "mode": _PRIVACY_MODE or "disabled",
        "retention_policy": _RETENTION_POLICY,
        "model": DEFAULT_MODEL,
        "base_url": os.environ.get("JANITOR_LLM_BASE_URL", "https://openrouter.ai/api/v1"),
    }
