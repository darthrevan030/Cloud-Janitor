"""Shared LLM client module for Cloud Janitor.

All AI agents import from this module instead of using the OpenAI SDK directly.
Routes all LLM calls through OpenRouter's OpenAI-compatible API.
"""

import os

import openai


def get_client() -> openai.OpenAI:
    """Return an OpenAI client configured for OpenRouter.

    Raises:
        EnvironmentError: If OPENROUTER_API_KEY is not set.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENROUTER_API_KEY is not set")
    return openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )


DEFAULT_MODEL: str = os.environ.get("JANITOR_LLM_MODEL", "anthropic/claude-haiku-4-5")
