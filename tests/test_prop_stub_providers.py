"""Property tests for stub provider NotImplementedError content.

Feature: production-readiness, Property 5: Stub provider NotImplementedError content

**Validates: Requirements 11.3**

Property 5: Stub Provider NotImplementedError Content
For any stub provider (GCPProvider, AzureProvider) and any of its stub methods,
calling the method SHALL raise NotImplementedError with a message that contains
BOTH the provider class name AND the method name.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mcp_server.backends.gcp_provider import GCPProvider
from mcp_server.backends.azure_provider import AzureProvider


# ─── Provider registry ───────────────────────────────────────────────────────

STUB_PROVIDERS = [GCPProvider, AzureProvider]

STUB_METHODS = ["get_cost_data", "get_security_data", "check_dependencies"]


# ─── Strategies ──────────────────────────────────────────────────────────────

provider_strategy = st.sampled_from(STUB_PROVIDERS)
method_strategy = st.sampled_from(STUB_METHODS)

# For check_dependencies, we need a resource_id argument
resource_id_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=1,
    max_size=100,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _call_stub_method(provider: Any, method_name: str, resource_id: str) -> None:
    """Call a stub method with appropriate arguments."""
    method = getattr(provider, method_name)
    if method_name == "check_dependencies":
        method(resource_id)
    else:
        method()


# ─── Property test ───────────────────────────────────────────────────────────


class TestPropertyStubProviderErrors:
    """Property 5: Stub Provider NotImplementedError Content.

    **Validates: Requirements 11.3**
    """

    @given(
        provider_cls=provider_strategy,
        method_name=method_strategy,
        resource_id=resource_id_strategy,
    )
    @settings(max_examples=200)
    def test_not_implemented_error_contains_provider_and_method_name(
        self,
        provider_cls: type,
        method_name: str,
        resource_id: str,
    ) -> None:
        """For any (provider, method) pair, NotImplementedError message contains
        both the provider class name and the method name."""
        provider = provider_cls()

        with pytest.raises(NotImplementedError) as exc_info:
            _call_stub_method(provider, method_name, resource_id)

        message = str(exc_info.value)

        # Assert provider class name is in the error message
        assert provider_cls.__name__ in message, (
            f"Expected provider class name '{provider_cls.__name__}' in "
            f"NotImplementedError message, got: {message!r}"
        )

        # Assert method name is in the error message
        assert method_name in message, (
            f"Expected method name '{method_name}' in "
            f"NotImplementedError message, got: {message!r}"
        )
