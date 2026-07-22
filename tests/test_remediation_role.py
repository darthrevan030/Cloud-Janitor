"""Unit tests for _assume_remediation_role() in orchestrator.orchestrator.

Validates: Requirements 2.1, 2.3, 2.4
"""

import logging
import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from cloud_janitor.orchestrator.orchestrator import (
    RemediationRoleAssumptionError,
    _assume_remediation_role,
)

FAKE_ROLE_ARN = "arn:aws:iam::123456789012:role/janitor-remediation-role"

FAKE_STS_RESPONSE = {
    "Credentials": {
        "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",
        "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "SessionToken": "FwoGZXIvYXdzEBYaDH...truncated...==",
        "Expiration": "2025-01-01T00:00:00Z",
    },
    "AssumedRoleUser": {
        "AssumedRoleId": "AROA3XFRBF23:janitor-remediation-1234567890",
        "Arn": f"{FAKE_ROLE_ARN}/janitor-remediation-1234567890",
    },
}


@pytest.fixture(autouse=True)
def _reset_warning_latch(monkeypatch):
    """Reset the module-level warning latch between tests."""
    monkeypatch.setattr(
        "cloud_janitor.orchestrator.orchestrator._REMEDIATION_ROLE_WARNED", False
    )


class TestSuccessfulAssumeRole:
    """Tests for the happy path — role ARN set and STS call succeeds."""

    @patch("cloud_janitor.mcp_server.backends.aws_provider._make_client")
    @patch.dict(os.environ, {"JANITOR_REMEDIATION_ROLE_ARN": FAKE_ROLE_ARN})
    def test_successful_assume_role_returns_credentials(self, mock_make_client):
        """Set JANITOR_REMEDIATION_ROLE_ARN, mock STS to return valid creds,
        assert dict with 3 keys returned."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = FAKE_STS_RESPONSE
        mock_make_client.return_value = mock_sts

        result = _assume_remediation_role()

        assert result is not None
        assert len(result) == 3
        assert "AWS_ACCESS_KEY_ID" in result
        assert "AWS_SECRET_ACCESS_KEY" in result
        assert "AWS_SESSION_TOKEN" in result

    @patch("cloud_janitor.mcp_server.backends.aws_provider._make_client")
    @patch.dict(os.environ, {"JANITOR_REMEDIATION_ROLE_ARN": FAKE_ROLE_ARN})
    def test_returned_keys_match_sts_response(self, mock_make_client):
        """Verify AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN
        map correctly to AccessKeyId/SecretAccessKey/SessionToken."""
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = FAKE_STS_RESPONSE
        mock_make_client.return_value = mock_sts

        result = _assume_remediation_role()

        assert result["AWS_ACCESS_KEY_ID"] == "AKIAIOSFODNN7EXAMPLE"
        assert result["AWS_SECRET_ACCESS_KEY"] == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        assert result["AWS_SESSION_TOKEN"] == "FwoGZXIvYXdzEBYaDH...truncated...=="


class TestFailureModes:
    """Tests for error paths — ClientError and timeout."""

    @patch("cloud_janitor.mcp_server.backends.aws_provider._make_client")
    @patch.dict(os.environ, {"JANITOR_REMEDIATION_ROLE_ARN": FAKE_ROLE_ARN})
    def test_client_error_raises_remediation_role_error(self, mock_make_client):
        """Set env var, mock STS to raise ClientError,
        assert RemediationRoleAssumptionError raised with role ARN in message."""
        mock_sts = MagicMock()
        mock_sts.assume_role.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Not authorized"}},
            "AssumeRole",
        )
        mock_make_client.return_value = mock_sts

        with pytest.raises(RemediationRoleAssumptionError) as exc_info:
            _assume_remediation_role()

        assert FAKE_ROLE_ARN in str(exc_info.value)

    @patch("cloud_janitor.mcp_server.backends.aws_provider._make_client")
    @patch.dict(os.environ, {"JANITOR_REMEDIATION_ROLE_ARN": FAKE_ROLE_ARN})
    def test_timeout_raises_remediation_role_error(self, mock_make_client):
        """Set env var, mock STS to raise TimeoutError."""
        mock_sts = MagicMock()
        mock_sts.assume_role.side_effect = TimeoutError("Connection timed out")
        mock_make_client.return_value = mock_sts

        with pytest.raises(RemediationRoleAssumptionError) as exc_info:
            _assume_remediation_role()

        assert FAKE_ROLE_ARN in str(exc_info.value)


class TestUnsetEnvVar:
    """Tests for behavior when JANITOR_REMEDIATION_ROLE_ARN is not set."""

    @patch("cloud_janitor.mcp_server.backends.aws_provider._make_client")
    @patch.dict(os.environ, {}, clear=False)
    def test_unset_env_returns_none(self, mock_make_client):
        """Do NOT set env var, assert returns None."""
        # Ensure the key is absent
        os.environ.pop("JANITOR_REMEDIATION_ROLE_ARN", None)

        result = _assume_remediation_role()

        assert result is None

    @patch("cloud_janitor.mcp_server.backends.aws_provider._make_client")
    @patch.dict(os.environ, {}, clear=False)
    def test_unset_env_never_calls_sts(self, mock_make_client):
        """Do NOT set env var, assert the mock was not called."""
        os.environ.pop("JANITOR_REMEDIATION_ROLE_ARN", None)

        _assume_remediation_role()

        mock_make_client.assert_not_called()


class TestWarningLatch:
    """Tests for the once-per-process warning behavior."""

    @patch("cloud_janitor.mcp_server.backends.aws_provider._make_client")
    @patch.dict(os.environ, {}, clear=False)
    def test_warning_logged_once_on_repeated_calls(self, mock_make_client, caplog):
        """Call twice without env var, assert WARNING in logs exactly once."""
        os.environ.pop("JANITOR_REMEDIATION_ROLE_ARN", None)

        with caplog.at_level(logging.WARNING, logger="cloud_janitor.orchestrator.orchestrator"):
            _assume_remediation_role()
            _assume_remediation_role()

        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "JANITOR_REMEDIATION_ROLE_ARN" in r.message
        ]
        assert len(warning_records) == 1
