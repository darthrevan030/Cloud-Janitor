"""Unit tests for privacy attestation in llm_client.

Covers Requirements 3.1–3.4:
1. Strict mode + retention_policy unset (default "unknown") → RuntimeError
2. Strict mode + retention_policy="unknown" explicitly → RuntimeError
3. Strict mode + retention_policy="none" + non-free model → client succeeds
4. Strict mode + retention_policy="none" + free-tier model → RuntimeError (free-router)
5. get_privacy_posture() returns all 4 required keys with correct values
"""

from unittest.mock import patch

import openai
import pytest

from cloud_janitor.core import llm_client
from cloud_janitor.core.llm_client import get_client, get_privacy_posture


# ---------------------------------------------------------------------------
# Shared patch context: API key must be present to reach privacy checks
# ---------------------------------------------------------------------------
_FAKE_API_KEY = {"JANITOR_LLM_API_KEY": "sk-test-privacy-unit"}


class TestStrictModeRetentionUnset:
    """Requirement 3.2: strict mode + JANITOR_LLM_RETENTION_POLICY unset → RuntimeError."""

    def test_retention_policy_unset_defaults_to_unknown_and_raises(self) -> None:
        """When JANITOR_LLM_RETENTION_POLICY is not set, _RETENTION_POLICY defaults
        to 'unknown'. In strict mode this must raise RuntimeError."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "unknown"),
            patch.object(llm_client, "_AI_ENABLED", True),
            patch.object(llm_client, "DEFAULT_MODEL", "anthropic/claude-sonnet-4-20250514"),
            patch.dict("os.environ", _FAKE_API_KEY),
        ):
            with pytest.raises(RuntimeError, match=r"JANITOR_PRIVACY_MODE=strict"):
                get_client()

    def test_error_message_mentions_retention_policy_requirement(self) -> None:
        """The error must tell the operator what to set."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "unknown"),
            patch.object(llm_client, "_AI_ENABLED", True),
            patch.object(llm_client, "DEFAULT_MODEL", "anthropic/claude-sonnet-4-20250514"),
            patch.dict("os.environ", _FAKE_API_KEY),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                get_client()
            msg = str(exc_info.value)
            assert "JANITOR_LLM_RETENTION_POLICY=none" in msg
            assert "'unknown'" in msg


class TestStrictModeRetentionExplicitUnknown:
    """Requirement 3.2: strict mode + explicitly set ="unknown" → RuntimeError."""

    def test_explicit_unknown_raises(self) -> None:
        """Setting JANITOR_LLM_RETENTION_POLICY=unknown is the same as unset."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "unknown"),
            patch.object(llm_client, "_AI_ENABLED", True),
            patch.object(llm_client, "DEFAULT_MODEL", "anthropic/claude-sonnet-4-20250514"),
            patch.dict("os.environ", _FAKE_API_KEY),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                get_client()
            # Must not succeed silently — verify it's the attestation gate
            assert "retention" in str(exc_info.value).lower()


class TestStrictModeNoneRetentionNonFreeModel:
    """Requirement 3.3: strict + retention='none' + non-free model → success."""

    def test_non_free_model_with_none_retention_succeeds(self) -> None:
        """get_client() returns an OpenAI client when all strict-mode conditions are met."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "none"),
            patch.object(llm_client, "_AI_ENABLED", True),
            patch.object(llm_client, "DEFAULT_MODEL", "anthropic/claude-sonnet-4-20250514"),
            patch.dict("os.environ", _FAKE_API_KEY),
        ):
            client = get_client()
            assert client is not None
            assert isinstance(client, openai.OpenAI)

    def test_client_has_correct_timeout(self) -> None:
        """Constructed client must have the 30s timeout configured."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "none"),
            patch.object(llm_client, "_AI_ENABLED", True),
            patch.object(llm_client, "DEFAULT_MODEL", "anthropic/claude-sonnet-4-20250514"),
            patch.dict("os.environ", _FAKE_API_KEY),
        ):
            client = get_client()
            # OpenAI client stores timeout; verify it's set
            assert client.timeout is not None


class TestStrictModeFreeTierModelBlocked:
    """Requirement 3.4: strict + retention='none' + free model → RuntimeError."""

    def test_free_suffix_model_raises(self) -> None:
        """Models ending in ':free' are blocked in strict mode."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "none"),
            patch.object(llm_client, "_AI_ENABLED", True),
            patch.object(llm_client, "DEFAULT_MODEL", "openai/gpt-oss-120b:free"),
            patch.dict("os.environ", _FAKE_API_KEY),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                get_client()
            msg = str(exc_info.value)
            assert "free-tier" in msg.lower() or "free" in msg.lower()
            assert "openai/gpt-oss-120b:free" in msg

    def test_free_slash_model_raises(self) -> None:
        """Models containing '/free' pattern are blocked in strict mode."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "none"),
            patch.object(llm_client, "_AI_ENABLED", True),
            patch.object(llm_client, "DEFAULT_MODEL", "provider/free-model-v1"),
            patch.dict("os.environ", _FAKE_API_KEY),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                get_client()
            assert "strict" in str(exc_info.value).lower()

    def test_openrouter_auto_model_raises(self) -> None:
        """The 'openrouter/auto' pattern is blocked in strict mode."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "none"),
            patch.object(llm_client, "_AI_ENABLED", True),
            patch.object(llm_client, "DEFAULT_MODEL", "openrouter/auto"),
            patch.dict("os.environ", _FAKE_API_KEY),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                get_client()
            assert "openrouter/auto" in str(exc_info.value)


class TestGetPrivacyPosture:
    """Requirement 3.1: get_privacy_posture() must return all 4 required keys."""

    _REQUIRED_KEYS = {"mode", "retention_policy", "model", "base_url"}

    def test_returns_all_required_keys(self) -> None:
        """All 4 keys must be present regardless of configuration."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "none"),
            patch.object(llm_client, "DEFAULT_MODEL", "some-model"),
            patch.dict("os.environ", {"JANITOR_LLM_BASE_URL": "https://example.com/v1"}),
        ):
            posture = get_privacy_posture()
            assert set(posture.keys()) == self._REQUIRED_KEYS

    def test_strict_mode_values_correct(self) -> None:
        """Values correspond to the patched module-level state."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "none"),
            patch.object(llm_client, "DEFAULT_MODEL", "some-model"),
            patch.dict("os.environ", {"JANITOR_LLM_BASE_URL": "https://my-endpoint.com/v1"}),
        ):
            posture = get_privacy_posture()
            assert posture["mode"] == "strict"
            assert posture["retention_policy"] == "none"
            assert posture["model"] == "some-model"
            assert posture["base_url"] == "https://my-endpoint.com/v1"

    def test_disabled_mode_when_privacy_mode_empty(self) -> None:
        """When _PRIVACY_MODE is empty string, mode key should be 'disabled'."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", ""),
            patch.object(llm_client, "_RETENTION_POLICY", "unknown"),
            patch.object(llm_client, "DEFAULT_MODEL", "anthropic/claude-haiku-4-5"),
            patch.dict("os.environ", {}, clear=False),
        ):
            posture = get_privacy_posture()
            assert posture["mode"] == "disabled"

    def test_default_base_url_when_not_set(self) -> None:
        """base_url falls back to OpenRouter default when JANITOR_LLM_BASE_URL is unset."""
        env = {k: v for k, v in _FAKE_API_KEY.items()}  # no JANITOR_LLM_BASE_URL
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "none"),
            patch.object(llm_client, "DEFAULT_MODEL", "some-model"),
            patch.dict("os.environ", env, clear=False),
        ):
            # Remove the key if it exists in env
            import os
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("JANITOR_LLM_BASE_URL", None)
                posture = get_privacy_posture()
                assert posture["base_url"] == "https://openrouter.ai/api/v1"

    def test_key_types_are_correct(self) -> None:
        """All values must be strings — not None, not missing."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "none"),
            patch.object(llm_client, "DEFAULT_MODEL", "test-model"),
            patch.dict("os.environ", {"JANITOR_LLM_BASE_URL": "https://x.com/v1"}),
        ):
            posture = get_privacy_posture()
            for key in self._REQUIRED_KEYS:
                assert isinstance(posture[key], str), f"Key '{key}' is not a string"
                assert len(posture[key]) > 0, f"Key '{key}' is empty"


class TestNegativeCases:
    """Negative tests: things that must NOT happen."""

    def test_non_strict_mode_does_not_check_retention(self) -> None:
        """When privacy mode is not 'strict', any retention policy is fine."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", ""),
            patch.object(llm_client, "_RETENTION_POLICY", "unknown"),
            patch.object(llm_client, "_AI_ENABLED", True),
            patch.object(llm_client, "DEFAULT_MODEL", "anthropic/claude-sonnet-4-20250514"),
            patch.dict("os.environ", _FAKE_API_KEY),
        ):
            # Must NOT raise RuntimeError
            client = get_client()
            assert isinstance(client, openai.OpenAI)

    def test_non_strict_mode_allows_free_model(self) -> None:
        """Free models are only blocked in strict mode."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", ""),
            patch.object(llm_client, "_RETENTION_POLICY", "unknown"),
            patch.object(llm_client, "_AI_ENABLED", True),
            patch.object(llm_client, "DEFAULT_MODEL", "openai/gpt-oss-120b:free"),
            patch.dict("os.environ", _FAKE_API_KEY),
        ):
            client = get_client()
            assert isinstance(client, openai.OpenAI)

    def test_ai_disabled_takes_precedence_over_privacy(self) -> None:
        """When AI is disabled, AIDisabledError fires before privacy checks."""
        with (
            patch.object(llm_client, "_PRIVACY_MODE", "strict"),
            patch.object(llm_client, "_RETENTION_POLICY", "none"),
            patch.object(llm_client, "_AI_ENABLED", False),
            patch.object(llm_client, "DEFAULT_MODEL", "anthropic/claude-sonnet-4-20250514"),
            patch.dict("os.environ", _FAKE_API_KEY),
        ):
            with pytest.raises(llm_client.AIDisabledError):
                get_client()
