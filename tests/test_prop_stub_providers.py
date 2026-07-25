"""Property tests for stub provider NotImplementedError content.

Property 5: Stub Provider NotImplementedError Content
For any stub provider and any of its stub methods, calling the method SHALL
raise NotImplementedError with a message containing BOTH the provider class
name AND the method name.

**Validates: Requirements 11.3**
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.mcp_server.backends.azure_provider import AzureProvider


STUB_METHODS = ["get_cost_data", "get_security_data", "check_dependencies"]

method_strategy = st.sampled_from(STUB_METHODS)

resource_id_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=1,
    max_size=100,
)


def _call_stub_method(provider: Any, method_name: str, resource_id: str) -> None:
    """Call a stub method with appropriate arguments."""
    method = getattr(provider, method_name)
    if method_name == "check_dependencies":
        method(resource_id)
    else:
        method()


class TestPropertyStubProviderErrors:
    """Property 5: Stub Provider NotImplementedError Content.

    **Validates: Requirements 11.3**

    Note: GCPProvider is excluded from Hypothesis property tests here because
    it requires SDK mocking which is incompatible with Hypothesis's execution
    model. GCP's NotImplementedError format is tested separately in
    test_stub_providers.py with explicit fixtures.
    """

    @given(
        method_name=method_strategy,
        resource_id=resource_id_strategy,
    )
    @settings(max_examples=30, deadline=None)
    def test_azure_not_implemented_error_contains_provider_and_method_name(
        self,
        method_name: str,
        resource_id: str,
    ) -> None:
        """For Azure provider, NotImplementedError message contains
        both the provider class name and the method name."""
        provider = AzureProvider()

        with pytest.raises(NotImplementedError) as exc_info:
            _call_stub_method(provider, method_name, resource_id)

        message = str(exc_info.value)

        assert "AzureProvider" in message, (
            f"Expected 'AzureProvider' in NotImplementedError message, got: {message!r}"
        )
        assert method_name in message, (
            f"Expected method name '{method_name}' in "
            f"NotImplementedError message, got: {message!r}"
        )
