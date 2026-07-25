"""Property-based tests for provider selection logic.

Uses Hypothesis to validate that _load_provider() correctly resolves
valid backend names to CloudProvider instances and rejects invalid ones
with informative error messages.
"""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from cloud_janitor.mcp_server.backends import CloudProvider
from cloud_janitor.mcp_server.aws_janitor_mcp import PROVIDER_REGISTRY, _load_provider


# --- Property 5: Provider registry completeness ---


@settings(max_examples=100, deadline=None)
@given(backend_name=st.sampled_from(list(PROVIDER_REGISTRY.keys())))
def test_valid_backend_returns_cloud_provider_instance(backend_name):
    """
    Property 5: Provider registry completeness

    For any valid backend name in the PROVIDER_REGISTRY, calling
    _load_provider() with that name set as JANITOR_BACKEND must return
    an instance of CloudProvider (not raise an exception).

    **Validates: Requirements 5.3, 5.4**
    """
    with patch.dict(os.environ, {"JANITOR_BACKEND": backend_name}):
        # AWSProvider lazily imports boto3 on __init__. Mock it so the test
        # doesn't require boto3 to be installed.
        # GCPProvider lazily imports google-auth. Mock it similarly.
        mock_google = MagicMock()
        mock_google.auth.default.return_value = (MagicMock(), "test-project")
        mock_google.auth.exceptions.DefaultCredentialsError = Exception
        with patch("cloud_janitor.mcp_server.backends.aws_provider.boto3", create=True), \
             patch.dict(sys.modules, {
                 "google": mock_google,
                 "google.auth": mock_google.auth,
                 "google.auth.exceptions": mock_google.auth.exceptions,
             }):
            # Patch the import inside AWSProvider.__init__
            with patch("builtins.__import__", side_effect=_mock_import):
                provider = _load_provider()

    # The returned object must be a CloudProvider instance
    assert isinstance(provider, CloudProvider), (
        f"_load_provider() with JANITOR_BACKEND={backend_name!r} returned "
        f"{type(provider).__name__}, which is not a CloudProvider instance"
    )


def _mock_import(name, *args, **kwargs):
    """Mock import that intercepts boto3 and google-auth, delegates everything else."""
    if name == "boto3":
        return MagicMock()
    if name in ("google", "google.auth", "google.auth.exceptions"):
        mock = MagicMock()
        mock.auth.default.return_value = (MagicMock(), "test-project")
        mock.auth.exceptions.DefaultCredentialsError = Exception
        return mock
    return original_import(name, *args, **kwargs)


# Save the real __import__ for delegation
import builtins  # noqa: E402
original_import = builtins.__import__


# --- Property 6: Invalid backend rejection ---


# Strategy: generate printable text strings that are NOT valid registry keys.
# We restrict to printable characters because env vars cannot contain null bytes
# and the error message uses repr() which escapes control characters.
@settings(max_examples=100, deadline=None)
@given(backend_name=st.text(
    min_size=1,
    max_size=50,
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S"), blacklist_characters="\x00"),
))
def test_invalid_backend_raises_value_error_with_details(backend_name):
    """
    Property 6: Invalid backend rejection

    For any string not in the PROVIDER_REGISTRY keys, _load_provider()
    must raise ValueError. The error message must contain the invalid
    backend name AND all valid option names from the registry.

    **Validates: Requirements 5.5**
    """
    # Only test strings that are NOT valid backend names
    assume(backend_name not in PROVIDER_REGISTRY)

    with patch.dict(os.environ, {"JANITOR_BACKEND": backend_name}):
        with pytest.raises(ValueError) as exc_info:
            _load_provider()

    error_message = str(exc_info.value)

    # The error message must contain the invalid backend name (possibly repr'd)
    assert repr(backend_name) in error_message, (
        f"ValueError message does not contain the invalid name {backend_name!r}. "
        f"Got: {error_message}"
    )

    # The error message must list all valid options
    for valid_name in sorted(PROVIDER_REGISTRY.keys()):
        assert valid_name in error_message, (
            f"ValueError message does not list valid option {valid_name!r}. "
            f"Got: {error_message}"
        )
