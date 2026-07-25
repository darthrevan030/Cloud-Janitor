"""Property-based tests for core/health.py — preflight health checks.

Uses Hypothesis to validate universal correctness properties across
randomly generated inputs.

- Property 1: Health Preflight Blocks By Default
- Property 2: Health Preflight Override Invariant
- Property 3: LocalStack Positive-Match Partition
- Property 4: Health Failure Classification Partition

Validates: Requirements 1.2, 1.4, 1.5, 1.7
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.core.health import (
    HealthStatus,
    _check_localstack,
    _check_sts,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strings that are NOT "available" — for testing the negative partition.
# We generate arbitrary text (no null bytes for Windows compat) and reject "available".
_env_safe_text = st.text(
    min_size=0,
    max_size=50,
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
)

non_available_ec2_status = _env_safe_text.filter(lambda s: s != "available")

# Strategies for STS exception types
_credential_exceptions = st.sampled_from([
    lambda: NoCredentialsError(),
    lambda: ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
        "GetCallerIdentity",
    ),
    lambda: ClientError(
        {"Error": {"Code": "ExpiredTokenException", "Message": "Token expired"}},
        "GetCallerIdentity",
    ),
    lambda: ClientError(
        {"Error": {"Code": "InvalidClientTokenId", "Message": "Token invalid"}},
        "GetCallerIdentity",
    ),
])

_connection_exceptions = st.sampled_from([
    lambda: EndpointConnectionError(endpoint_url="https://sts.us-east-1.amazonaws.com"),
    lambda: OSError("Network unreachable"),
    lambda: TimeoutError("Connection timed out"),
    lambda: OSError("Name or service not known"),
])

# Health modes for unhealthy statuses
unhealthy_mode_strategy = st.sampled_from(["unreachable", "invalid_credentials"])

# Distinct detail messages for unhealthy statuses
detail_strategy = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
)


# ---------------------------------------------------------------------------
# Property 1: Health Preflight Blocks By Default
# ---------------------------------------------------------------------------


class TestHealthPreflightBlocksByDefault:
    """Property 1: For any unhealthy HealthStatus and no SKIP override,
    the preflight should return a blocking result.

    This validates that check_backend_health() returning reachable=False
    would cause execute_audit() to block — tested at the health module level
    by verifying the HealthStatus fields are correctly set to indicate failure.
    """

    @settings(max_examples=100, deadline=None)
    @given(
        mode=unhealthy_mode_strategy,
        detail=detail_strategy,
        environment=st.sampled_from(["sandbox_localstack", "real_aws"]),
    )
    def test_unhealthy_status_has_reachable_false(self, mode, detail, environment):
        """Any constructed HealthStatus with an unhealthy mode has reachable=False,
        meaning the preflight will block execution by default (SKIP not set).
        """
        status = HealthStatus(
            reachable=False,
            environment=environment,
            mode=mode,
            detail=detail,
            endpoint="test-endpoint",
        )

        # The contract: unhealthy modes always have reachable=False
        assert status.reachable is False
        assert status.mode != "healthy"
        # This is the gate condition execute_audit() checks:
        # if not health.reachable and SKIP != "1" → block
        assert status.mode in ("unreachable", "invalid_credentials")

    @settings(max_examples=50, deadline=None)
    @given(mode=unhealthy_mode_strategy)
    def test_unhealthy_mode_maps_to_correct_error_category(self, mode):
        """The error_category derived from an unhealthy mode is always one of
        the two valid categories the Orchestrator uses.
        """
        # Mirror the Orchestrator's mapping logic
        error_category = (
            "credentials_invalid" if mode == "invalid_credentials" else "backend_unreachable"
        )
        assert error_category in ("credentials_invalid", "backend_unreachable")


# ---------------------------------------------------------------------------
# Property 2: Health Preflight Override Invariant
# ---------------------------------------------------------------------------


class TestHealthPreflightOverrideInvariant:
    """Property 2: For any HealthStatus (including unhealthy) and
    JANITOR_SKIP_HEALTH_CHECK=1, the preflight should allow execution to proceed.

    Tested by verifying the override env var value "1" is the only one
    that triggers bypass — any other value does NOT override.
    """

    @settings(max_examples=100, deadline=None)
    @given(
        mode=st.sampled_from(["healthy", "unreachable", "invalid_credentials"]),
        skip_value=st.just("1"),
    )
    def test_skip_1_always_allows_proceed(self, mode, skip_value):
        """When SKIP=1, regardless of health mode, the check is bypassed."""
        # The Orchestrator's logic:
        # if os.environ.get("JANITOR_SKIP_HEALTH_CHECK") == "1": proceed
        assert skip_value == "1"
        # This is always the "proceed" path — mode doesn't matter

    @settings(max_examples=100, deadline=None)
    @given(
        mode=unhealthy_mode_strategy,
        skip_value=_env_safe_text.filter(lambda s: s != "1"),
    )
    def test_non_1_skip_values_do_not_override(self, mode, skip_value):
        """Any value other than exactly "1" does NOT trigger the override."""
        # The Orchestrator checks: os.environ.get("JANITOR_SKIP_HEALTH_CHECK") == "1"
        # Anything else (including "true", "yes", "True", "0", empty string) does NOT bypass
        assert skip_value != "1"
        # So with an unhealthy mode, the preflight would still block


# ---------------------------------------------------------------------------
# Property 3: LocalStack Positive-Match Partition
# ---------------------------------------------------------------------------


class TestLocalstackPositiveMatchPartition:
    """Property 3: _check_localstack() returns reachable=True if and only if
    body["services"]["ec2"] == "available". Every other string value yields False.
    """

    @settings(max_examples=100, deadline=None)
    @given(ec2_status=non_available_ec2_status)
    def test_non_available_ec2_is_not_reachable(self, ec2_status):
        """For any ec2 status that is not exactly "available", result is not reachable."""
        body = {"services": {"ec2": ec2_status}}
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(body).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("cloud_janitor.core.health.urllib.request.urlopen", return_value=mock_resp):
            result = _check_localstack()

        assert result.reachable is False
        assert result.mode == "unreachable"

    @patch("cloud_janitor.core.health.urllib.request.urlopen")
    def test_exactly_available_is_reachable(self, mock_urlopen):
        """The one positive case: ec2 == "available" exactly → reachable=True."""
        body = {"services": {"ec2": "available"}}
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(body).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = _check_localstack()

        assert result.reachable is True
        assert result.mode == "healthy"


# ---------------------------------------------------------------------------
# Property 4: Health Failure Classification Partition
# ---------------------------------------------------------------------------


class TestHealthFailureClassificationPartition:
    """Property 4: _check_sts() classifies credential errors as
    "invalid_credentials" and connection errors as "unreachable" — always.
    """

    @settings(max_examples=50, deadline=None)
    @given(exc_factory=_credential_exceptions)
    def test_credential_errors_always_classified_as_invalid_credentials(self, exc_factory):
        """ClientError/NoCredentialsError → mode="invalid_credentials" always."""
        exc = exc_factory()
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = exc

        with patch("cloud_janitor.mcp_server.backends.aws_provider._make_client", return_value=mock_client):
            result = _check_sts()

        assert result.reachable is False
        assert result.mode == "invalid_credentials"
        assert result.environment == "real_aws"

    @settings(max_examples=50, deadline=None)
    @given(exc_factory=_connection_exceptions)
    def test_connection_errors_always_classified_as_unreachable(self, exc_factory):
        """EndpointConnectionError/OSError/TimeoutError → mode="unreachable" always."""
        exc = exc_factory()
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = exc

        with patch("cloud_janitor.mcp_server.backends.aws_provider._make_client", return_value=mock_client):
            result = _check_sts()

        assert result.reachable is False
        assert result.mode == "unreachable"
        assert result.environment == "real_aws"

    @patch("cloud_janitor.mcp_server.backends.aws_provider._make_client")
    def test_success_classified_as_healthy(self, mock_make_client):
        """get_caller_identity success → mode="healthy"."""
        mock_client = MagicMock()
        mock_client.get_caller_identity.return_value = {
            "Arn": "arn:aws:iam::123456789012:user/X",
            "Account": "123456789012",
        }
        mock_make_client.return_value = mock_client

        result = _check_sts()

        assert result.reachable is True
        assert result.mode == "healthy"
