"""Property tests for remediation-role credential assumption.

Property 1: Remediation-Role Credential Isolation
    For any invocation where JANITOR_REMEDIATION_ROLE_ARN is set and assume_role
    succeeds, the returned env's AWS_* keys are exactly the values from
    sts:AssumeRole, never mixed with ambient.

Property 2: Fail-Closed Role Assumption
    For any sts:AssumeRole failure when the role ARN is set, zero subprocess.run
    calls happen and failure result is returned (RemediationRoleAssumptionError raised).

Property 3: Unset-Role Fallback Invariant
    When JANITOR_REMEDIATION_ROLE_ARN is unset, _assume_remediation_role() returns
    None, sts.assume_role is never called, and terraform env is identical to
    _build_subprocess_env("terraform")'s output.

**Validates: Requirements 2.2, 2.3, 2.4**
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

import cloud_janitor.orchestrator.orchestrator as orch_mod
from cloud_janitor.orchestrator.orchestrator import (
    _assume_remediation_role,
    _build_subprocess_env,
    RemediationRoleAssumptionError,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Safe text strategy for Windows — no null bytes or control characters
_safe_text = st.text(
    min_size=1,
    max_size=64,
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
)

# Generate valid STS Credentials response dicts
_sts_credentials = st.fixed_dictionaries({
    "AccessKeyId": _safe_text,
    "SecretAccessKey": _safe_text,
    "SessionToken": _safe_text,
})

# Generate random ambient AWS_* env values to prove they're overridden
_ambient_aws_env = st.fixed_dictionaries({
    "AWS_ACCESS_KEY_ID": _safe_text,
    "AWS_SECRET_ACCESS_KEY": _safe_text,
    "AWS_SESSION_TOKEN": _safe_text,
})

# Role ARN strategy — realistic-looking ARN strings
_role_arn = st.from_regex(
    r"arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9_-]{1,30}",
    fullmatch=True,
)

# Exception types that sts:AssumeRole can raise
_sts_exceptions = st.sampled_from([
    ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Not authorized"}},
        "AssumeRole",
    ),
    ClientError(
        {"Error": {"Code": "MalformedPolicyDocument", "Message": "Bad policy"}},
        "AssumeRole",
    ),
    TimeoutError("Connection timed out"),
    ConnectionError("Network unreachable"),
    RuntimeError("Unexpected STS failure"),
])


# ---------------------------------------------------------------------------
# Property 1: Remediation-Role Credential Isolation
# ---------------------------------------------------------------------------


class TestRemediationRoleCredentialIsolation:
    """Property 1: Returned credentials are exactly from STS, never mixed with ambient.

    **Validates: Requirements 2.2**
    """

    @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        sts_creds=_sts_credentials,
        ambient=_ambient_aws_env,
        role_arn=_role_arn,
    )
    def test_returned_creds_are_exactly_sts_values(
        self, sts_creds, ambient, role_arn
    ):
        """When role assumption succeeds, returned dict contains exactly the STS values."""
        # Ensure the generated STS creds differ from ambient to prove isolation
        assume(sts_creds["AccessKeyId"] != ambient["AWS_ACCESS_KEY_ID"])
        assume(sts_creds["SecretAccessKey"] != ambient["AWS_SECRET_ACCESS_KEY"])
        assume(sts_creds["SessionToken"] != ambient["AWS_SESSION_TOKEN"])

        # Build a controlled env with the role ARN and ambient creds
        controlled_env = {
            "JANITOR_REMEDIATION_ROLE_ARN": role_arn,
            "AWS_ACCESS_KEY_ID": ambient["AWS_ACCESS_KEY_ID"],
            "AWS_SECRET_ACCESS_KEY": ambient["AWS_SECRET_ACCESS_KEY"],
            "AWS_SESSION_TOKEN": ambient["AWS_SESSION_TOKEN"],
            "PATH": "/usr/bin",
        }

        # Mock _make_client to return a mock STS client
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": sts_creds["AccessKeyId"],
                "SecretAccessKey": sts_creds["SecretAccessKey"],
                "SessionToken": sts_creds["SessionToken"],
            }
        }

        with (
            patch.dict(os.environ, controlled_env, clear=True),
            patch.object(orch_mod, "_REMEDIATION_ROLE_WARNED", False),
            patch(
                "cloud_janitor.mcp_server.backends.aws_provider._make_client",
                return_value=mock_sts,
            ),
        ):
            result = _assume_remediation_role()

        # The result MUST contain exactly the STS-provided values
        assert result is not None
        assert result["AWS_ACCESS_KEY_ID"] == sts_creds["AccessKeyId"]
        assert result["AWS_SECRET_ACCESS_KEY"] == sts_creds["SecretAccessKey"]
        assert result["AWS_SESSION_TOKEN"] == sts_creds["SessionToken"]

        # The result MUST NOT contain any ambient values
        assert result["AWS_ACCESS_KEY_ID"] != ambient["AWS_ACCESS_KEY_ID"]
        assert result["AWS_SECRET_ACCESS_KEY"] != ambient["AWS_SECRET_ACCESS_KEY"]
        assert result["AWS_SESSION_TOKEN"] != ambient["AWS_SESSION_TOKEN"]

        # The result MUST contain exactly three keys
        assert set(result.keys()) == {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
        }


# ---------------------------------------------------------------------------
# Property 2: Fail-Closed Role Assumption
# ---------------------------------------------------------------------------


class TestFailClosedRoleAssumption:
    """Property 2: Any STS failure raises RemediationRoleAssumptionError.

    **Validates: Requirements 2.4**
    """

    @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        role_arn=_role_arn,
        exc=_sts_exceptions,
    )
    def test_sts_failure_raises_remediation_error(self, role_arn, exc):
        """When sts:AssumeRole fails, RemediationRoleAssumptionError is raised."""
        controlled_env = {
            "JANITOR_REMEDIATION_ROLE_ARN": role_arn,
            "PATH": "/usr/bin",
        }

        # Mock _make_client to return an STS client that raises
        mock_sts = MagicMock()
        mock_sts.assume_role.side_effect = exc

        with (
            patch.dict(os.environ, controlled_env, clear=True),
            patch.object(orch_mod, "_REMEDIATION_ROLE_WARNED", False),
            patch(
                "cloud_janitor.mcp_server.backends.aws_provider._make_client",
                return_value=mock_sts,
            ),
        ):
            with pytest.raises(RemediationRoleAssumptionError) as exc_info:
                _assume_remediation_role()

        # The error message must include the role ARN for debuggability
        assert role_arn in str(exc_info.value)

    @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        role_arn=_role_arn,
        exc=_sts_exceptions,
    )
    def test_sts_failure_means_zero_subprocess_calls(self, role_arn, exc):
        """No subprocess.run calls happen when role assumption fails."""
        controlled_env = {
            "JANITOR_REMEDIATION_ROLE_ARN": role_arn,
            "PATH": "/usr/bin",
        }

        mock_sts = MagicMock()
        mock_sts.assume_role.side_effect = exc

        with (
            patch.dict(os.environ, controlled_env, clear=True),
            patch.object(orch_mod, "_REMEDIATION_ROLE_WARNED", False),
            patch(
                "cloud_janitor.mcp_server.backends.aws_provider._make_client",
                return_value=mock_sts,
            ),
            patch("subprocess.run") as mock_subprocess,
        ):
            with pytest.raises(RemediationRoleAssumptionError):
                _assume_remediation_role()

            # Zero subprocess calls must have been made
            mock_subprocess.assert_not_called()


# ---------------------------------------------------------------------------
# Property 3: Unset-Role Fallback Invariant
# ---------------------------------------------------------------------------


class TestUnsetRoleFallbackInvariant:
    """Property 3: When JANITOR_REMEDIATION_ROLE_ARN is unset, returns None.

    **Validates: Requirements 2.3**
    """

    @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        ambient=_ambient_aws_env,
    )
    def test_unset_role_returns_none(self, ambient):
        """_assume_remediation_role() returns None when JANITOR_REMEDIATION_ROLE_ARN is unset."""
        # Env does NOT have JANITOR_REMEDIATION_ROLE_ARN
        controlled_env = {
            "PATH": "/usr/bin",
            "AWS_ACCESS_KEY_ID": ambient["AWS_ACCESS_KEY_ID"],
            "AWS_SECRET_ACCESS_KEY": ambient["AWS_SECRET_ACCESS_KEY"],
            "AWS_SESSION_TOKEN": ambient["AWS_SESSION_TOKEN"],
        }

        with (
            patch.dict(os.environ, controlled_env, clear=True),
            patch.object(orch_mod, "_REMEDIATION_ROLE_WARNED", False),
            patch(
                "cloud_janitor.mcp_server.backends.aws_provider._make_client"
            ) as mock_make_client,
        ):
            result = _assume_remediation_role()

        assert result is None
        # _make_client must never be called (early return before the import)
        mock_make_client.assert_not_called()

    @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        ambient=_ambient_aws_env,
    )
    def test_unset_role_terraform_env_unchanged(self, ambient):
        """When role is unset, terraform env is identical to _build_subprocess_env output."""
        # Build an env without JANITOR_REMEDIATION_ROLE_ARN but with ambient AWS creds
        controlled_env = {
            "PATH": "/usr/bin",
            "AWS_ACCESS_KEY_ID": ambient["AWS_ACCESS_KEY_ID"],
            "AWS_SECRET_ACCESS_KEY": ambient["AWS_SECRET_ACCESS_KEY"],
            "AWS_SESSION_TOKEN": ambient["AWS_SESSION_TOKEN"],
        }

        with (
            patch.dict(os.environ, controlled_env, clear=True),
            patch.object(orch_mod, "_REMEDIATION_ROLE_WARNED", False),
            patch(
                "cloud_janitor.mcp_server.backends.aws_provider._make_client"
            ) as mock_make_client,
        ):
            role_result = _assume_remediation_role()
            # Also build the terraform env in the same controlled context
            tf_env = _build_subprocess_env("terraform")

        assert role_result is None
        mock_make_client.assert_not_called()

        # When role returns None, the terraform env should pass through
        # the ambient AWS creds unchanged (no credential override happens)
        assert tf_env.get("AWS_ACCESS_KEY_ID") == ambient["AWS_ACCESS_KEY_ID"]
        assert tf_env.get("AWS_SECRET_ACCESS_KEY") == ambient["AWS_SECRET_ACCESS_KEY"]
        assert tf_env.get("AWS_SESSION_TOKEN") == ambient["AWS_SESSION_TOKEN"]

    @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        ambient=_ambient_aws_env,
    )
    def test_unset_role_sts_never_called(self, ambient):
        """sts.assume_role is explicitly never called when ARN is unset."""
        controlled_env = {
            "PATH": "/usr/bin",
            "AWS_ACCESS_KEY_ID": ambient["AWS_ACCESS_KEY_ID"],
            "AWS_SECRET_ACCESS_KEY": ambient["AWS_SECRET_ACCESS_KEY"],
            "AWS_SESSION_TOKEN": ambient["AWS_SESSION_TOKEN"],
        }

        mock_sts = MagicMock()

        with (
            patch.dict(os.environ, controlled_env, clear=True),
            patch.object(orch_mod, "_REMEDIATION_ROLE_WARNED", False),
            patch(
                "cloud_janitor.mcp_server.backends.aws_provider._make_client",
                return_value=mock_sts,
            ) as mock_make_client,
        ):
            result = _assume_remediation_role()

        assert result is None
        # _make_client itself should never be called (early return before import)
        mock_make_client.assert_not_called()
        # And therefore assume_role on the STS mock is also never called
        mock_sts.assume_role.assert_not_called()
