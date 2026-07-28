"""Unit tests for core/health.py — backend reachability preflight.

Tests cover:
- LocalStack probe: HTTP 200 with ec2 "available" → reachable
- LocalStack probe: HTTP 200 with ec2 "unavailable" → not reachable
- LocalStack probe: HTTP 200 with ec2 "starting" → not reachable (regression)
- LocalStack probe: URLError/timeout → not reachable, no exception propagates
- STS probe: get_caller_identity success in real-AWS mode → mode="healthy"
- STS probe: ClientError/NoCredentialsError → mode="invalid_credentials"
- STS probe: EndpointConnectionError/OSError/TimeoutError → mode="unreachable"
- STS probe: Config with connect_timeout=5, read_timeout=5 passed to _make_client

Requirements: 1.2, 1.3, 1.7
"""

from __future__ import annotations

import json
import os
import urllib.error
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from cloud_janitor.core.health import (
    HealthStatus,
    _check_localstack,
    _check_sts,
    check_backend_health,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_urlopen_response(body: dict, status: int = 200) -> MagicMock:
    """Create a mock urllib response with given JSON body and HTTP status."""
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.read.return_value = json.dumps(body).encode("utf-8")
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _real_aws_env() -> dict:
    """Environment dict that triggers real-AWS mode in _is_real_aws_mode()."""
    return {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": ""}


def _localstack_env(endpoint: str = "http://localhost:4566") -> dict:
    """Environment dict for LocalStack mode."""
    return {"JANITOR_BACKEND": "fixture", "AWS_ENDPOINT_URL": endpoint}


# ---------------------------------------------------------------------------
# LocalStack probe: ec2 "available" → reachable
# ---------------------------------------------------------------------------


class TestCheckLocalstackReachable:
    """Mocked urlopen returning HTTP 200 with ec2: "available" → reachable."""

    @patch("cloud_janitor.core.health.urllib.request.urlopen")
    def test_ec2_available_is_reachable(self, mock_urlopen):
        body = {"services": {"ec2": "available", "s3": "available"}}
        mock_urlopen.return_value = _make_urlopen_response(body)

        result = _check_localstack()

        assert result.reachable is True
        assert result.mode == "healthy"
        assert result.environment == "sandbox_localstack"
        assert result.detail == "ok"

    @patch("cloud_janitor.core.health.urllib.request.urlopen")
    def test_uses_aws_endpoint_url_env_var(self, mock_urlopen):
        body = {"services": {"ec2": "available"}}
        mock_urlopen.return_value = _make_urlopen_response(body)

        with patch.dict(os.environ, {"AWS_ENDPOINT_URL": "http://custom:9999"}, clear=False):
            result = _check_localstack()

        # Verify the URL called includes the custom endpoint
        call_args = mock_urlopen.call_args
        assert "http://custom:9999/_localstack/health" in call_args[0][0]
        assert result.reachable is True


# ---------------------------------------------------------------------------
# LocalStack probe: ec2 "unavailable" → not reachable
# ---------------------------------------------------------------------------


class TestCheckLocalstackUnavailable:
    """Mocked urlopen returning HTTP 200 with ec2: "unavailable" → not reachable."""

    @patch("cloud_janitor.core.health.urllib.request.urlopen")
    def test_ec2_unavailable_is_not_reachable(self, mock_urlopen):
        body = {"services": {"ec2": "unavailable", "s3": "available"}}
        mock_urlopen.return_value = _make_urlopen_response(body)

        result = _check_localstack()

        assert result.reachable is False
        assert result.mode == "unreachable"
        assert "unavailable" in result.detail


# ---------------------------------------------------------------------------
# LocalStack probe: ec2 "starting" → not reachable (regression)
# ---------------------------------------------------------------------------


class TestCheckLocalstackStarting:
    """Regression test: ec2 "starting" must NOT be treated as reachable.

    This catches the negative-match bug where checking != "unavailable"
    would accidentally pass intermediate states like "starting".
    """

    @patch("cloud_janitor.core.health.urllib.request.urlopen")
    def test_ec2_starting_is_not_reachable(self, mock_urlopen):
        body = {"services": {"ec2": "starting", "s3": "available"}}
        mock_urlopen.return_value = _make_urlopen_response(body)

        result = _check_localstack()

        assert result.reachable is False
        assert result.mode == "unreachable"
        assert "starting" in result.detail

    @patch("cloud_janitor.core.health.urllib.request.urlopen")
    def test_ec2_missing_from_services_is_not_reachable(self, mock_urlopen):
        body = {"services": {"s3": "available"}}
        mock_urlopen.return_value = _make_urlopen_response(body)

        result = _check_localstack()

        assert result.reachable is False
        assert result.mode == "unreachable"

    @patch("cloud_janitor.core.health.urllib.request.urlopen")
    def test_empty_services_dict_is_not_reachable(self, mock_urlopen):
        body = {"services": {}}
        mock_urlopen.return_value = _make_urlopen_response(body)

        result = _check_localstack()

        assert result.reachable is False
        assert result.mode == "unreachable"


# ---------------------------------------------------------------------------
# LocalStack probe: URLError/timeout → not reachable, no exception propagates
# ---------------------------------------------------------------------------


class TestCheckLocalstackNetworkErrors:
    """Mocked urlopen raising URLError/timeout → not reachable, no exception."""

    @patch("cloud_janitor.core.health.urllib.request.urlopen")
    def test_url_error_is_not_reachable(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        result = _check_localstack()

        assert result.reachable is False
        assert result.mode == "unreachable"
        assert "Connection refused" in result.detail

    @patch("cloud_janitor.core.health.urllib.request.urlopen")
    def test_timeout_error_is_not_reachable(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("timed out")

        result = _check_localstack()

        assert result.reachable is False
        assert result.mode == "unreachable"

    @patch("cloud_janitor.core.health.urllib.request.urlopen")
    def test_os_error_is_not_reachable(self, mock_urlopen):
        mock_urlopen.side_effect = OSError("Network unreachable")

        result = _check_localstack()

        assert result.reachable is False
        assert result.mode == "unreachable"

    @patch("cloud_janitor.core.health.urllib.request.urlopen")
    def test_json_decode_error_is_not_reachable(self, mock_urlopen):
        """Non-JSON response body → not reachable."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = _check_localstack()

        assert result.reachable is False
        assert result.mode == "unreachable"


# ---------------------------------------------------------------------------
# STS probe: success in real-AWS mode → mode="healthy"
# ---------------------------------------------------------------------------


class TestCheckStsSuccess:
    """Mocked STS get_caller_identity success → mode='healthy'."""

    @patch.dict(os.environ, {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": ""})
    def test_sts_success_returns_healthy(self):
        mock_client = MagicMock()
        mock_client.get_caller_identity.return_value = {
            "Arn": "arn:aws:iam::123456789012:user/TestUser",
            "Account": "123456789012",
        }
        mock_make_client = MagicMock(return_value=mock_client)

        result = _check_sts(_client_factory=mock_make_client)

        assert result.reachable is True
        assert result.mode == "healthy"
        assert result.environment == "real_aws"
        assert result.detail == "ok"
        assert result.endpoint == "sts:GetCallerIdentity"


# ---------------------------------------------------------------------------
# STS probe: ClientError/NoCredentialsError → mode="invalid_credentials"
# ---------------------------------------------------------------------------


class TestCheckStsCredentialFailures:
    """Mocked STS raising ClientError/NoCredentialsError → mode='invalid_credentials'."""

    @patch.dict(os.environ, {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": ""})
    @patch("botocore.config.Config")
    def test_client_error_access_denied(self, _mock_config):
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
            "GetCallerIdentity",
        )
        mock_make_client = MagicMock(return_value=mock_client)

        result = _check_sts(_client_factory=mock_make_client)

        assert result.reachable is False
        assert result.mode == "invalid_credentials"
        assert result.environment == "real_aws"
        assert "AccessDenied" in result.detail or "Access denied" in result.detail

    @patch.dict(os.environ, {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": ""})
    @patch("botocore.config.Config")
    def test_no_credentials_error(self, _mock_config):
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = NoCredentialsError()
        mock_make_client = MagicMock(return_value=mock_client)

        result = _check_sts(_client_factory=mock_make_client)

        assert result.reachable is False
        assert result.mode == "invalid_credentials"
        assert result.environment == "real_aws"

    @patch.dict(os.environ, {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": ""})
    @patch("botocore.config.Config")
    def test_expired_token_client_error(self, _mock_config):
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = ClientError(
            {"Error": {"Code": "ExpiredTokenException", "Message": "Token expired"}},
            "GetCallerIdentity",
        )
        mock_make_client = MagicMock(return_value=mock_client)

        result = _check_sts(_client_factory=mock_make_client)

        assert result.reachable is False
        assert result.mode == "invalid_credentials"


# ---------------------------------------------------------------------------
# STS probe: EndpointConnectionError/OSError/TimeoutError → mode="unreachable"
# ---------------------------------------------------------------------------


class TestCheckStsConnectionFailures:
    """Mocked STS raising connection errors → mode='unreachable'."""

    @patch.dict(os.environ, {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": ""})
    def test_endpoint_connection_error(self):
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = EndpointConnectionError(
            endpoint_url="https://sts.us-east-1.amazonaws.com"
        )
        mock_make_client = MagicMock(return_value=mock_client)

        result = _check_sts(_client_factory=mock_make_client)

        assert result.reachable is False
        assert result.mode == "unreachable"
        assert result.environment == "real_aws"

    @patch.dict(os.environ, {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": ""})
    def test_os_error(self):
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = OSError("Network unreachable")
        mock_make_client = MagicMock(return_value=mock_client)

        result = _check_sts(_client_factory=mock_make_client)

        assert result.reachable is False
        assert result.mode == "unreachable"

    @patch.dict(os.environ, {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": ""})
    def test_timeout_error(self):
        mock_client = MagicMock()
        mock_client.get_caller_identity.side_effect = TimeoutError("Connection timed out")
        mock_make_client = MagicMock(return_value=mock_client)

        result = _check_sts(_client_factory=mock_make_client)

        assert result.reachable is False
        assert result.mode == "unreachable"


# ---------------------------------------------------------------------------
# STS probe: Config with connect_timeout=5, read_timeout=5 passed to _make_client
# ---------------------------------------------------------------------------


class TestCheckStsTimeoutConfig:
    """Assert _check_sts() passes a Config with connect_timeout=5, read_timeout=5."""

    @patch.dict(os.environ, {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": ""})
    def test_config_has_5s_connect_and_read_timeout(self):
        mock_client = MagicMock()
        mock_client.get_caller_identity.return_value = {
            "Arn": "arn:aws:iam::123456789012:user/X",
            "Account": "123456789012",
        }
        mock_make_client = MagicMock(return_value=mock_client)

        _check_sts(_client_factory=mock_make_client)

        mock_make_client.assert_called_once()
        call_kwargs = mock_make_client.call_args
        # _client_factory is called as factory("sts", region=None, config=timeout_config)
        config_arg = call_kwargs[1].get("config") if call_kwargs[1] else call_kwargs[0][2]
        assert config_arg is not None, "_client_factory was not passed a config argument"
        assert config_arg.connect_timeout == 5
        assert config_arg.read_timeout == 5

    @patch.dict(os.environ, {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": ""})
    def test_sts_service_name_is_sts(self):
        mock_client = MagicMock()
        mock_client.get_caller_identity.return_value = {
            "Arn": "arn:aws:iam::123456789012:user/X",
            "Account": "123456789012",
        }
        mock_make_client = MagicMock(return_value=mock_client)

        _check_sts(_client_factory=mock_make_client)

        call_args = mock_make_client.call_args[0]
        assert call_args[0] == "sts"


# ---------------------------------------------------------------------------
# check_backend_health() dispatches correctly
# ---------------------------------------------------------------------------


class TestCheckBackendHealthDispatch:
    """Verify check_backend_health() routes to the correct probe."""

    @patch.dict(os.environ, {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": ""})
    @patch("cloud_janitor.core.health._check_sts")
    def test_real_aws_mode_dispatches_to_sts(self, mock_check_sts):
        mock_check_sts.return_value = HealthStatus(True, "real_aws", "healthy", "ok", "sts")

        result = check_backend_health()

        mock_check_sts.assert_called_once()
        assert result.environment == "real_aws"

    @patch.dict(os.environ, {"JANITOR_BACKEND": "fixture"})
    @patch("cloud_janitor.core.health._check_localstack")
    def test_fixture_mode_dispatches_to_localstack(self, mock_check_ls):
        mock_check_ls.return_value = HealthStatus(True, "sandbox_localstack", "healthy", "ok", "url")

        result = check_backend_health()

        mock_check_ls.assert_called_once()
        assert result.environment == "sandbox_localstack"

    @patch.dict(
        os.environ,
        {"JANITOR_BACKEND": "aws", "AWS_ENDPOINT_URL": "http://localhost:4566"},
        clear=True,
    )
    @patch("cloud_janitor.core.health._check_localstack")
    def test_aws_backend_with_localhost_dispatches_to_localstack(self, mock_check_ls):
        mock_check_ls.return_value = HealthStatus(True, "sandbox_localstack", "healthy", "ok", "url")

        check_backend_health()

        mock_check_ls.assert_called_once()
