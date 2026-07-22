"""Unit tests for core/identity.py — IAM identity resolution.

Tests cover:
- Real-AWS mode: STS success → verified ARN returned
- Real-AWS mode: STS failures (NoCredentialsError, ClientError, timeout) → IdentityResolutionError
- Real-AWS mode: STS returning LocalStack account 000000000000 → IdentityResolutionError
- Sandbox mode: STS failure → fallback actor returned, no exception
- Sandbox mode: STS client is NEVER constructed at all (not merely "exception caught")
- Non-localhost custom endpoint (FIPS/PrivateLink) with JANITOR_BACKEND=aws → real-AWS mode, STS IS called
- JANITOR_ACTOR env var respected as fallback when set

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from cloud_janitor.core.identity import (
    ActorResolution,
    IdentityResolutionError,
    resolve_actor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _real_aws_env(extra: dict | None = None) -> dict:
    """Environment dict for real-AWS mode (JANITOR_BACKEND=aws, no localhost endpoint)."""
    env = {"JANITOR_BACKEND": "aws"}
    # Explicitly remove AWS_ENDPOINT_URL to ensure not-localstack
    if extra:
        env.update(extra)
    return env


def _sandbox_fixture_env(extra: dict | None = None) -> dict:
    """Environment dict for fixture/sandbox mode (default backend)."""
    env = {}  # JANITOR_BACKEND defaults to "fixture"
    if extra:
        env.update(extra)
    return env


def _sandbox_localstack_env(endpoint: str = "http://localhost:4566", extra: dict | None = None) -> dict:
    """Environment dict for LocalStack sandbox (aws backend + localhost endpoint)."""
    env = {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": endpoint}
    if extra:
        env.update(extra)
    return env


def _mock_sts_success(arn: str = "arn:aws:iam::123456789012:user/TestUser", account: str = "123456789012"):
    """Return a mock STS client whose get_caller_identity() succeeds."""
    mock_client = MagicMock()
    mock_client.get_caller_identity.return_value = {"Arn": arn, "Account": account}
    return mock_client


# ---------------------------------------------------------------------------
# Real-AWS mode: STS success → verified ARN returned
# ---------------------------------------------------------------------------

class TestRealAwsStsSuccess:
    """Requirement 1.2: STS success → use the returned ARN as the actor."""

    @patch.dict(os.environ, _real_aws_env(), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_returns_verified_arn(self, mock_make_client):
        expected_arn = "arn:aws:iam::123456789012:role/DeployRole"
        mock_make_client.return_value = _mock_sts_success(arn=expected_arn)

        result = resolve_actor("fallback-value")

        assert isinstance(result, ActorResolution)
        assert result.actor == expected_arn
        assert result.verified is True

    @patch.dict(os.environ, _real_aws_env(), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_sts_client_called_with_sts_service(self, mock_make_client):
        mock_make_client.return_value = _mock_sts_success()

        resolve_actor("system")

        mock_make_client.assert_called_once()
        call_args = mock_make_client.call_args
        assert call_args[0][0] == "sts"  # first positional arg is service name

    @patch.dict(os.environ, _real_aws_env(), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_fallback_not_used_when_sts_succeeds(self, mock_make_client):
        mock_make_client.return_value = _mock_sts_success(arn="arn:aws:iam::111111111111:user/Real")

        result = resolve_actor("should-not-appear")

        assert result.actor != "should-not-appear"
        assert result.actor == "arn:aws:iam::111111111111:user/Real"


# ---------------------------------------------------------------------------
# Real-AWS mode: STS failure → IdentityResolutionError raised
# ---------------------------------------------------------------------------

class TestRealAwsStsFailure:
    """Requirement 1.3: STS failures in real-AWS mode → hard stop."""

    @patch.dict(os.environ, _real_aws_env(), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_no_credentials_error_raises(self, mock_make_client):
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = NoCredentialsError()
        mock_make_client.return_value = mock_client

        with pytest.raises(IdentityResolutionError) as exc_info:
            resolve_actor("system")

        assert "Could not confirm caller identity" in str(exc_info.value)

    @patch.dict(os.environ, _real_aws_env(), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_client_error_access_denied_raises(self, mock_make_client):
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "User is not authorized"}},
            "GetCallerIdentity",
        )
        mock_make_client.return_value = mock_client

        with pytest.raises(IdentityResolutionError) as exc_info:
            resolve_actor("system")

        assert "Could not confirm caller identity" in str(exc_info.value)

    @patch.dict(os.environ, _real_aws_env(), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_timeout_raises(self, mock_make_client):
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = ConnectionError("Connection timed out")
        mock_make_client.return_value = mock_client

        with pytest.raises(IdentityResolutionError) as exc_info:
            resolve_actor("system")

        assert "Could not confirm caller identity" in str(exc_info.value)

    @patch.dict(os.environ, _real_aws_env(), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_generic_exception_raises(self, mock_make_client):
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = RuntimeError("Unexpected failure")
        mock_make_client.return_value = mock_client

        with pytest.raises(IdentityResolutionError) as exc_info:
            resolve_actor("system")

        assert "Could not confirm caller identity" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Real-AWS mode: STS returns LocalStack default account → IdentityResolutionError
# ---------------------------------------------------------------------------

class TestRealAwsLocalStackAccountRejection:
    """Requirement 1.4: account 000000000000 in real-AWS mode = misconfiguration."""

    @patch.dict(os.environ, _real_aws_env(), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_localstack_account_raises(self, mock_make_client):
        mock_make_client.return_value = _mock_sts_success(
            arn="arn:aws:iam::000000000000:root",
            account="000000000000",
        )

        with pytest.raises(IdentityResolutionError) as exc_info:
            resolve_actor("system")

        assert "LocalStack default identity" in str(exc_info.value)

    @patch.dict(os.environ, _real_aws_env(), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_localstack_account_raises_even_with_valid_arn_format(self, mock_make_client):
        """Even a well-formed ARN with account 000000000000 is rejected."""
        mock_make_client.return_value = _mock_sts_success(
            arn="arn:aws:sts::000000000000:assumed-role/Admin/session",
            account="000000000000",
        )

        with pytest.raises(IdentityResolutionError):
            resolve_actor("system")


# ---------------------------------------------------------------------------
# Sandbox mode: STS failure → fallback actor returned, no exception
# ---------------------------------------------------------------------------

class TestSandboxModeFallback:
    """Requirement 1.5: Sandbox mode falls back gracefully."""

    @patch.dict(os.environ, _sandbox_fixture_env(), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_fixture_backend_returns_fallback(self, mock_make_client):
        """Default fixture backend → use fallback, no STS call."""
        result = resolve_actor("my-fallback")

        assert result.actor == "my-fallback"
        assert result.verified is False

    @patch.dict(os.environ, _sandbox_localstack_env("http://localhost:4566"), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_aws_backend_with_localhost_returns_fallback(self, mock_make_client):
        """aws backend + localhost endpoint → sandbox mode, fallback returned."""
        result = resolve_actor("fallback-user")

        assert result.actor == "fallback-user"
        assert result.verified is False

    @patch.dict(os.environ, _sandbox_localstack_env("http://127.0.0.1:4566"), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_aws_backend_with_127_returns_fallback(self, mock_make_client):
        """aws backend + 127.0.0.1 endpoint → sandbox mode, fallback returned."""
        result = resolve_actor("fallback-user")

        assert result.actor == "fallback-user"
        assert result.verified is False


# ---------------------------------------------------------------------------
# Sandbox mode: STS client is NEVER constructed at all
# ---------------------------------------------------------------------------

class TestSandboxModeStsNeverCalled:
    """Critical behavioral test: in sandbox mode, _make_client is never invoked.

    This is stronger than "exception caught" — the STS client must not even
    be constructed. The code short-circuits before reaching the STS call path.
    """

    @patch.dict(os.environ, _sandbox_fixture_env(), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_fixture_backend_never_calls_make_client(self, mock_make_client):
        resolve_actor("system")

        mock_make_client.assert_not_called()

    @patch.dict(os.environ, _sandbox_localstack_env("http://localhost:4566"), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_localstack_localhost_never_calls_make_client(self, mock_make_client):
        resolve_actor("system")

        mock_make_client.assert_not_called()

    @patch.dict(os.environ, _sandbox_localstack_env("http://127.0.0.1:4566"), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_localstack_127_never_calls_make_client(self, mock_make_client):
        resolve_actor("system")

        mock_make_client.assert_not_called()

    @patch.dict(os.environ, {"JANITOR_BACKEND": "fixture", "AWS_ENDPOINT_URL": "http://localhost:9999"}, clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_fixture_backend_with_localhost_endpoint_never_calls_make_client(self, mock_make_client):
        """Even if AWS_ENDPOINT_URL points to localhost, fixture backend = sandbox."""
        resolve_actor("system")

        mock_make_client.assert_not_called()


# ---------------------------------------------------------------------------
# Non-localhost custom endpoint with JANITOR_BACKEND=aws → real-AWS mode
# ---------------------------------------------------------------------------

class TestNonLocalhostEndpointTreatedAsRealAws:
    """Requirement 1.1 precision: FIPS/PrivateLink endpoints must NOT be misclassified as LocalStack.

    AWS_ENDPOINT_URL is also used for FIPS endpoints, VPC endpoints, and PrivateLink.
    Only localhost/127.0.0.1 substring triggers sandbox mode.
    """

    @patch.dict(
        os.environ,
        {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": "https://sts-fips.us-east-1.amazonaws.com"},
        clear=True,
    )
    @patch("cloud_janitor.core.identity._make_client")
    def test_fips_endpoint_calls_sts(self, mock_make_client):
        mock_make_client.return_value = _mock_sts_success()

        result = resolve_actor("fallback")

        mock_make_client.assert_called_once()
        assert result.verified is True

    @patch.dict(
        os.environ,
        {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": "https://vpce-abc123.sts.us-east-1.vpce.amazonaws.com"},
        clear=True,
    )
    @patch("cloud_janitor.core.identity._make_client")
    def test_vpce_endpoint_calls_sts(self, mock_make_client):
        mock_make_client.return_value = _mock_sts_success()

        result = resolve_actor("fallback")

        mock_make_client.assert_called_once()
        assert result.verified is True

    @patch.dict(
        os.environ,
        {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": "https://sts.us-gov-west-1.amazonaws.com"},
        clear=True,
    )
    @patch("cloud_janitor.core.identity._make_client")
    def test_govcloud_endpoint_calls_sts(self, mock_make_client):
        mock_make_client.return_value = _mock_sts_success()

        result = resolve_actor("fallback")

        mock_make_client.assert_called_once()
        assert result.verified is True

    @patch.dict(
        os.environ,
        {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": "https://sts-fips.us-east-1.amazonaws.com"},
        clear=True,
    )
    @patch("cloud_janitor.core.identity._make_client")
    def test_fips_endpoint_sts_failure_still_raises(self, mock_make_client):
        """A FIPS endpoint is real-AWS; STS failure there is still a hard stop."""
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = NoCredentialsError()
        mock_make_client.return_value = mock_client

        with pytest.raises(IdentityResolutionError):
            resolve_actor("fallback")


# ---------------------------------------------------------------------------
# JANITOR_ACTOR env var respected as fallback
# ---------------------------------------------------------------------------

class TestJanitorActorEnvVar:
    """Requirement 1.5: JANITOR_ACTOR env var overrides the function's fallback parameter."""

    @patch.dict(os.environ, {**_sandbox_fixture_env(), "JANITOR_ACTOR": "custom-actor@corp"}, clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_janitor_actor_used_instead_of_fallback_param(self, mock_make_client):
        result = resolve_actor("default-fallback")

        assert result.actor == "custom-actor@corp"
        assert result.actor != "default-fallback"
        assert result.verified is False

    @patch.dict(os.environ, {**_sandbox_fixture_env()}, clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_no_janitor_actor_uses_fallback_param(self, mock_make_client):
        result = resolve_actor("default-fallback")

        assert result.actor == "default-fallback"
        assert result.verified is False

    @patch.dict(
        os.environ,
        {**_sandbox_localstack_env("http://localhost:4566"), "JANITOR_ACTOR": "localstack-user"},
        clear=True,
    )
    @patch("cloud_janitor.core.identity._make_client")
    def test_janitor_actor_respected_in_localstack_mode(self, mock_make_client):
        result = resolve_actor("system")

        assert result.actor == "localstack-user"
        assert result.verified is False

    @patch.dict(os.environ, _real_aws_env({"JANITOR_ACTOR": "ignored-in-real-aws"}), clear=True)
    @patch("cloud_janitor.core.identity._make_client")
    def test_janitor_actor_ignored_in_real_aws_mode_on_success(self, mock_make_client):
        """In real-AWS mode, STS-verified ARN is used regardless of JANITOR_ACTOR."""
        expected_arn = "arn:aws:iam::123456789012:user/RealUser"
        mock_make_client.return_value = _mock_sts_success(arn=expected_arn)

        result = resolve_actor("system")

        assert result.actor == expected_arn
        assert result.actor != "ignored-in-real-aws"
        assert result.verified is True
