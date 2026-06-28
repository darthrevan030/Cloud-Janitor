"""Unit tests for llm_client.py.

Tests the shared LLM client module that all AI agents import from.
Validates:
- get_client() returns OpenAI instance with correct base_url
- DEFAULT_MODEL reads from env var with correct default
- EnvironmentError raised when OPENROUTER_API_KEY unset
- No sensitive values are logged or exposed

Requirements: 13.1, 13.2, 13.3, 13.4
"""

import importlib
import os
from unittest.mock import patch

import pytest


class TestGetClient:
    """Tests for get_client() → openai.OpenAI configured for OpenRouter."""

    def test_returns_openai_instance_with_api_key_set(self):
        """get_client() returns an openai.OpenAI instance when OPENROUTER_API_KEY is set."""
        import openai

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key-abc123"}):
            import llm_client

            importlib.reload(llm_client)
            client = llm_client.get_client()

        assert isinstance(client, openai.OpenAI)

    def test_base_url_is_openrouter(self):
        """get_client() configures base_url to OpenRouter's API endpoint."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key-abc123"}):
            import llm_client

            importlib.reload(llm_client)
            client = llm_client.get_client()

        assert client.base_url == "https://openrouter.ai/api/v1/"

    def test_api_key_passed_to_client(self):
        """get_client() passes the OPENROUTER_API_KEY to the OpenAI client."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-key-xyz"}):
            import llm_client

            importlib.reload(llm_client)
            client = llm_client.get_client()

        assert client.api_key == "sk-or-test-key-xyz"

    def test_raises_environment_error_when_key_missing(self):
        """get_client() raises EnvironmentError when OPENROUTER_API_KEY is not set."""
        env = os.environ.copy()
        env.pop("OPENROUTER_API_KEY", None)

        with patch.dict(os.environ, env, clear=True):
            import llm_client

            importlib.reload(llm_client)

            with pytest.raises(EnvironmentError, match="OPENROUTER_API_KEY is not set"):
                llm_client.get_client()

    def test_raises_environment_error_when_key_is_empty_string(self):
        """get_client() raises EnvironmentError when OPENROUTER_API_KEY is empty."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            import llm_client

            importlib.reload(llm_client)

            with pytest.raises(EnvironmentError, match="OPENROUTER_API_KEY is not set"):
                llm_client.get_client()


class TestDefaultModel:
    """Tests for DEFAULT_MODEL reading from JANITOR_LLM_MODEL env var."""

    def test_default_model_uses_env_var_when_set(self):
        """DEFAULT_MODEL reads from JANITOR_LLM_MODEL when the env var is present."""
        with patch.dict(
            os.environ,
            {"JANITOR_LLM_MODEL": "openai/gpt-4o-mini", "OPENROUTER_API_KEY": "k"},
        ):
            import llm_client

            importlib.reload(llm_client)

            assert llm_client.DEFAULT_MODEL == "openai/gpt-4o-mini"

    def test_default_model_fallback_when_env_var_unset(self):
        """DEFAULT_MODEL defaults to 'anthropic/claude-haiku-4-5' when env var is absent."""
        env = os.environ.copy()
        env.pop("JANITOR_LLM_MODEL", None)
        env["OPENROUTER_API_KEY"] = "k"

        with patch.dict(os.environ, env, clear=True):
            import llm_client

            importlib.reload(llm_client)

            assert llm_client.DEFAULT_MODEL == "anthropic/claude-haiku-4-5"

    def test_default_model_is_string(self):
        """DEFAULT_MODEL is always a string type."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}):
            import llm_client

            importlib.reload(llm_client)

            assert isinstance(llm_client.DEFAULT_MODEL, str)


class TestSensitiveDataExposure:
    """Ensure sensitive values (API keys, model names) are not exposed."""

    def test_get_client_does_not_include_key_in_error_message(self):
        """When OPENROUTER_API_KEY is missing, the error message does not leak any key value."""
        env = os.environ.copy()
        env.pop("OPENROUTER_API_KEY", None)

        with patch.dict(os.environ, env, clear=True):
            import llm_client

            importlib.reload(llm_client)

            with pytest.raises(EnvironmentError) as exc_info:
                llm_client.get_client()

            error_msg = str(exc_info.value)
            # Error message should indicate the variable name, not contain any key value
            assert "OPENROUTER_API_KEY" in error_msg
            # Should not contain patterns that look like actual API keys
            assert "sk-or-" not in error_msg
            assert "sk-" not in error_msg.replace("OPENROUTER_API_KEY", "")

    def test_module_repr_does_not_expose_api_key(self):
        """The client object's repr/str does not contain the raw API key."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-secret-value-12345"}):
            import llm_client

            importlib.reload(llm_client)
            client = llm_client.get_client()

        client_repr = repr(client)
        client_str = str(client)
        assert "sk-or-secret-value-12345" not in client_repr
        assert "sk-or-secret-value-12345" not in client_str

    def test_module_source_does_not_log_api_key(self):
        """The llm_client module source code does not contain logging of the API key value."""
        import inspect

        import llm_client

        source = inspect.getsource(llm_client)
        # Should not have print() or logging calls that could expose the key
        assert "print(api_key" not in source
        assert "logging" not in source or "api_key" not in source


class TestModuleInterface:
    """Verify the module exports the expected interface."""

    def test_module_exports_get_client(self):
        """llm_client exposes get_client as a callable."""
        import llm_client

        assert hasattr(llm_client, "get_client")
        assert callable(llm_client.get_client)

    def test_module_exports_default_model(self):
        """llm_client exposes DEFAULT_MODEL as a module-level attribute."""
        import llm_client

        assert hasattr(llm_client, "DEFAULT_MODEL")

    def test_no_anthropic_import(self):
        """llm_client does not import the anthropic package (Req 13.5)."""
        import inspect

        import llm_client

        source = inspect.getsource(llm_client)
        assert "import anthropic" not in source
        assert "from anthropic" not in source
