"""Tests for Orchestrator preflight health check wiring.

Validates that:
- Unreachable backend + no override → zero calls to scan(), AuditResult.success == False
- Unreachable backend + JANITOR_SKIP_HEALTH_CHECK=1 → scan() IS called (proceeds)
- Reachable backend → pipeline proceeds unchanged
"""

from unittest.mock import MagicMock, patch

import pytest

from cloud_janitor.core.health import HealthStatus
from cloud_janitor.orchestrator import Orchestrator


@pytest.fixture
def tmp_project(tmp_path):
    """Set up a temporary project structure for testing."""
    (tmp_path / "hooks").mkdir(parents=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True)
    (tmp_path / "output" / "remediations").mkdir(parents=True)
    (tmp_path / "output" / "logs").mkdir(parents=True)
    (tmp_path / "output" / "policies").mkdir(parents=True)
    return tmp_path


def _make_orchestrator(tmp_project):
    """Create an Orchestrator with mocked agents pointing at tmp_project."""
    orch = Orchestrator(project_root=tmp_project, approver="test-user")
    # Mock the agent scan methods so they don't do real work
    orch._finops = MagicMock()
    orch._finops.scan = MagicMock(return_value=[])
    orch._secops = MagicMock()
    orch._secops.scan = MagicMock(return_value=[])
    orch._architect = MagicMock()
    orch._architect.plan = MagicMock(return_value=[])
    return orch


class TestUnreachableBackendNoOverride:
    """Unreachable backend + no override → blocked, no scan called."""

    def test_unreachable_blocks_pipeline(self, tmp_project):
        """Pipeline returns failure without calling scan when backend is unreachable."""
        unhealthy = HealthStatus(
            reachable=False,
            environment="sandbox_localstack",
            mode="unreachable",
            detail="Connection refused",
            endpoint="http://localhost:4566",
        )
        with patch("cloud_janitor.orchestrator.orchestrator.check_backend_health", return_value=unhealthy):
            orch = _make_orchestrator(tmp_project)
            result = orch.execute_audit()

        assert result.success is False
        assert result.error_category == "backend_unreachable"
        # Zero calls to scan — pipeline never started
        orch._finops.scan.assert_not_called()

    def test_invalid_credentials_blocks_pipeline(self, tmp_project):
        """Pipeline returns failure with credentials_invalid error_category."""
        unhealthy = HealthStatus(
            reachable=False,
            environment="real_aws",
            mode="invalid_credentials",
            detail="ExpiredTokenException",
            endpoint="sts:GetCallerIdentity",
        )
        with patch("cloud_janitor.orchestrator.orchestrator.check_backend_health", return_value=unhealthy):
            orch = _make_orchestrator(tmp_project)
            result = orch.execute_audit()

        assert result.success is False
        assert result.error_category == "credentials_invalid"
        assert "invalid_credentials" in result.error
        orch._finops.scan.assert_not_called()

    def test_error_agent_is_orchestrator(self, tmp_project):
        """error_agent field correctly identifies the Orchestrator."""
        unhealthy = HealthStatus(
            reachable=False,
            environment="sandbox_localstack",
            mode="unreachable",
            detail="timeout",
            endpoint="http://localhost:4566",
        )
        with patch("cloud_janitor.orchestrator.orchestrator.check_backend_health", return_value=unhealthy):
            orch = _make_orchestrator(tmp_project)
            result = orch.execute_audit()

        assert result.error_agent == "Orchestrator"


class TestUnreachableBackendWithOverride:
    """Unreachable backend + JANITOR_SKIP_HEALTH_CHECK=1 → scan IS called."""

    @patch.dict("os.environ", {"JANITOR_SKIP_HEALTH_CHECK": "1"}, clear=False)
    def test_skip_override_proceeds(self, tmp_project):
        """With skip override, pipeline proceeds even when backend reports unreachable."""
        unhealthy = HealthStatus(
            reachable=False,
            environment="sandbox_localstack",
            mode="unreachable",
            detail="Connection refused",
            endpoint="http://localhost:4566",
        )
        with patch("cloud_janitor.orchestrator.orchestrator.check_backend_health", return_value=unhealthy):
            orch = _make_orchestrator(tmp_project)
            orch.execute_audit()

        # scan() WAS called — pipeline proceeded past health check
        orch._finops.scan.assert_called_once()

    @patch.dict("os.environ", {"JANITOR_SKIP_HEALTH_CHECK": "0"}, clear=False)
    def test_skip_override_only_when_value_is_1(self, tmp_project):
        """Only the value '1' triggers the override; other values still block."""
        unhealthy = HealthStatus(
            reachable=False,
            environment="sandbox_localstack",
            mode="unreachable",
            detail="Connection refused",
            endpoint="http://localhost:4566",
        )
        with patch("cloud_janitor.orchestrator.orchestrator.check_backend_health", return_value=unhealthy):
            orch = _make_orchestrator(tmp_project)
            result = orch.execute_audit()

        assert result.success is False
        orch._finops.scan.assert_not_called()


class TestReachableBackend:
    """Reachable backend → pipeline proceeds unchanged."""

    def test_healthy_backend_proceeds(self, tmp_project):
        """When backend is healthy, pipeline calls scan and continues."""
        healthy = HealthStatus(
            reachable=True,
            environment="sandbox_localstack",
            mode="healthy",
            detail="ok",
            endpoint="http://localhost:4566",
        )
        with patch("cloud_janitor.orchestrator.orchestrator.check_backend_health", return_value=healthy):
            orch = _make_orchestrator(tmp_project)
            orch.execute_audit()

        # scan() was called — pipeline proceeded
        orch._finops.scan.assert_called_once()
        # SecOps also called (pipeline continued past FinOps)
        orch._secops.scan.assert_called_once()
