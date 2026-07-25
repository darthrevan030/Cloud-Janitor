"""Property-based tests for strict-mode privacy attestation gate.

Property 4: Strict Mode Attestation Gate
Validates Requirement 3.2 — In strict privacy mode, get_client() MUST raise
RuntimeError for ANY retention policy value that is not exactly "none".
"""

from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.core import llm_client
from cloud_janitor.core.llm_client import get_client


# Strategy: generate arbitrary text strings that are NEVER "none" (case-insensitive).
# This covers empty strings, whitespace, "None", "NONE", "n0ne", random garbage, etc.
_not_none_text = st.text(min_size=0, max_size=200).filter(
    lambda x: x.lower() != "none"
)


class TestStrictModeAttestationGateProperty:
    """For any retention policy != 'none', strict mode must reject client construction."""

    @given(retention_policy=_not_none_text)
    @settings(max_examples=200, deadline=None)
    def test_strict_mode_rejects_non_none_retention_policy(self, retention_policy: str) -> None:
        """get_client() raises RuntimeError when _PRIVACY_MODE='strict' and
        _RETENTION_POLICY is anything other than 'none'.

        We patch module-level variables directly (they are read at call time by
        get_client), plus ensure AI is enabled and an API key is present so we
        reach the attestation gate rather than short-circuiting earlier.
        """
        with patch.object(llm_client, "_PRIVACY_MODE", "strict"), \
             patch.object(llm_client, "_RETENTION_POLICY", retention_policy.lower()), \
             patch.object(llm_client, "_AI_ENABLED", True), \
             patch.dict("os.environ", {
                 "JANITOR_LLM_API_KEY": "sk-test-key-for-property-test",
                 "JANITOR_LLM_MODEL": "anthropic/claude-sonnet-4-20250514",
             }), \
             patch.object(llm_client, "DEFAULT_MODEL", "anthropic/claude-sonnet-4-20250514"):
            with pytest.raises(RuntimeError) as exc_info:
                get_client()

            # Verify the error message is specific to retention policy, not some
            # other RuntimeError that happens to fire.
            assert "JANITOR_PRIVACY_MODE=strict" in str(exc_info.value)
            assert "JANITOR_LLM_RETENTION_POLICY=none" in str(exc_info.value)

    @given(retention_policy=_not_none_text)
    @settings(max_examples=200, deadline=None)
    def test_error_message_contains_offending_value(self, retention_policy: str) -> None:
        """The RuntimeError message must surface the actual bad value so operators
        can diagnose the misconfiguration without reading source code."""
        with patch.object(llm_client, "_PRIVACY_MODE", "strict"), \
             patch.object(llm_client, "_RETENTION_POLICY", retention_policy.lower()), \
             patch.object(llm_client, "_AI_ENABLED", True), \
             patch.dict("os.environ", {
                 "JANITOR_LLM_API_KEY": "sk-test-key-for-property-test",
                 "JANITOR_LLM_MODEL": "anthropic/claude-sonnet-4-20250514",
             }), \
             patch.object(llm_client, "DEFAULT_MODEL", "anthropic/claude-sonnet-4-20250514"):
            with pytest.raises(RuntimeError) as exc_info:
                get_client()

            # The lowercased policy value must appear in the error message.
            # The implementation uses repr() formatting, so check for repr form.
            error_msg = str(exc_info.value)
            lowered = retention_policy.lower()
            assert repr(lowered) in error_msg or lowered in error_msg, (
                f"Neither {lowered!r} nor its repr found in error: {error_msg}"
            )


class TestStrictModePositiveCase:
    """Verify that retention_policy='none' in strict mode does NOT raise RuntimeError."""

    def test_strict_mode_allows_none_retention_policy(self) -> None:
        """get_client() succeeds (returns OpenAI client) when strict mode is on
        and retention policy is exactly 'none', with a non-free model."""
        with patch.object(llm_client, "_PRIVACY_MODE", "strict"), \
             patch.object(llm_client, "_RETENTION_POLICY", "none"), \
             patch.object(llm_client, "_AI_ENABLED", True), \
             patch.dict("os.environ", {
                 "JANITOR_LLM_API_KEY": "sk-test-key-for-property-test",
                 "JANITOR_LLM_MODEL": "anthropic/claude-sonnet-4-20250514",
             }), \
             patch.object(llm_client, "DEFAULT_MODEL", "anthropic/claude-sonnet-4-20250514"):
            # Should NOT raise RuntimeError — client construction succeeds
            client = get_client()
            assert client is not None
            # Verify it's actually an OpenAI client instance
            import openai
            assert isinstance(client, openai.OpenAI)


class TestStrictModeOnlyBlocksInStrictMode:
    """Retention policy checks must NOT fire when privacy mode is not 'strict'."""

    @given(retention_policy=_not_none_text)
    @settings(max_examples=100, deadline=None)
    def test_non_strict_mode_ignores_retention_policy(self, retention_policy: str) -> None:
        """When _PRIVACY_MODE is not 'strict', any retention policy is accepted."""
        with patch.object(llm_client, "_PRIVACY_MODE", ""), \
             patch.object(llm_client, "_RETENTION_POLICY", retention_policy.lower()), \
             patch.object(llm_client, "_AI_ENABLED", True), \
             patch.dict("os.environ", {
                 "JANITOR_LLM_API_KEY": "sk-test-key-for-property-test",
                 "JANITOR_LLM_MODEL": "anthropic/claude-sonnet-4-20250514",
             }), \
             patch.object(llm_client, "DEFAULT_MODEL", "anthropic/claude-sonnet-4-20250514"):
            # Should NOT raise RuntimeError — non-strict mode doesn't gate on policy
            client = get_client()
            assert client is not None
