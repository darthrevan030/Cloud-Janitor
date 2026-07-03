"""Unit tests for the Cloud Janitor CLI (cli.py).

Tests all Click subcommands via CliRunner. Mocks the Orchestrator (external I/O)
but never mocks the CLI handler functions themselves.

Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.8, 1.9, 1.10, 1.11, 12.2
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli import main


@pytest.fixture
def runner():
    """Provide a Click CliRunner."""
    return CliRunner()


# ─── scan (default: execute_audit) ────────────────────────────────────────────


class TestScanCommand:
    """Tests for the `scan` subcommand."""

    def test_scan_calls_execute_audit_and_prints_finding_count(self, runner):
        """scan invokes Orchestrator.execute_audit and echoes finding count."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.findings = [{"id": "vol-1"}, {"id": "vol-2"}, {"id": "vol-3"}]

        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.execute_audit.return_value = mock_result

            result = runner.invoke(main, ["scan"])

        assert result.exit_code == 0
        assert "3 finding(s) produced" in result.output
        instance.execute_audit.assert_called_once()

    def test_scan_finops_flag_calls_finops_scan(self, runner):
        """scan --finops invokes orch._finops.scan() directly."""
        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance._finops.scan.return_value = [{"id": "f1"}, {"id": "f2"}]

            result = runner.invoke(main, ["scan", "--finops"])

        assert result.exit_code == 0
        assert "2 finding(s) produced" in result.output
        instance._finops.scan.assert_called_once()
        instance.execute_audit.assert_not_called()

    def test_scan_secops_flag_calls_secops_scan(self, runner):
        """scan --secops invokes orch._secops.scan() directly."""
        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance._secops.scan.return_value = [{"id": "s1"}]

            result = runner.invoke(main, ["scan", "--secops"])

        assert result.exit_code == 0
        assert "1 finding(s) produced" in result.output
        instance._secops.scan.assert_called_once()
        instance.execute_audit.assert_not_called()

    def test_scan_zero_findings(self, runner):
        """scan with zero findings still reports 0 finding(s)."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.findings = []

        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.execute_audit.return_value = mock_result

            result = runner.invoke(main, ["scan"])

        assert result.exit_code == 0
        assert "0 finding(s) produced" in result.output

    def test_scan_audit_failure_prints_error_and_exits_1(self, runner):
        """scan exits 1 and prints error when AuditResult.success is False."""
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "FinOps Auditor failed: timeout"

        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.execute_audit.return_value = mock_result

            result = runner.invoke(main, ["scan"])

        assert result.exit_code == 1
        assert "FinOps Auditor failed: timeout" in result.stderr

    def test_scan_exception_prints_to_stderr_and_exits_1(self, runner):
        """scan exception from Orchestrator prints error to stderr and exits 1."""
        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.execute_audit.side_effect = RuntimeError("AWS credentials expired")

            result = runner.invoke(main, ["scan"])

        assert result.exit_code == 1
        assert "Agent failed" in result.stderr
        assert "AWS credentials expired" in result.stderr


# ─── approve ──────────────────────────────────────────────────────────────────


class TestApproveCommand:
    """Tests for the `approve` subcommand."""

    def test_approve_passes_correct_command_string(self, runner):
        """approve <id> passes 'APPROVE <id>' to orch.approve(command=...)."""
        mock_result = MagicMock()
        mock_result.success = True

        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.approve.return_value = mock_result

            result = runner.invoke(main, ["approve", "vol-abc123"])

        assert result.exit_code == 0
        assert "Approved: vol-abc123" in result.output
        instance.approve.assert_called_once_with(command="APPROVE vol-abc123")

    def test_approve_failure_prints_error_and_exits_1(self, runner):
        """approve exits 1 when ApprovalResult.success is False."""
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "No remediation plan found"

        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.approve.return_value = mock_result

            result = runner.invoke(main, ["approve", "vol-xyz"])

        assert result.exit_code == 1
        assert "No remediation plan found" in result.stderr

    def test_approve_exception_prints_error_and_exits_1(self, runner):
        """approve exits 1 on exception from Orchestrator."""
        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.approve.side_effect = RuntimeError("terraform init failed")

            result = runner.invoke(main, ["approve", "vol-err"])

        assert result.exit_code == 1
        assert "terraform init failed" in result.stderr

    def test_approve_requires_resource_id_argument(self, runner):
        """approve with no argument exits non-zero (Click enforces required arg)."""
        with patch("orchestrator.Orchestrator"):
            result = runner.invoke(main, ["approve"])

        assert result.exit_code != 0


# ─── rollback ─────────────────────────────────────────────────────────────────


class TestRollbackCommand:
    """Tests for the `rollback` subcommand."""

    def test_rollback_passes_correct_command_string(self, runner):
        """rollback <id> passes 'ROLLBACK <id>' to orch.rollback(command=...)."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.needs_confirmation = False

        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.rollback.return_value = mock_result

            result = runner.invoke(main, ["rollback", "sg-dead99"])

        assert result.exit_code == 0
        assert "Rollback complete: sg-dead99" in result.output
        instance.rollback.assert_called_once_with(command="ROLLBACK sg-dead99")

    def test_rollback_needs_confirmation(self, runner):
        """rollback prints pending confirmation when needs_confirmation=True."""
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.needs_confirmation = True

        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.rollback.return_value = mock_result

            result = runner.invoke(main, ["rollback", "sg-pend01"])

        assert result.exit_code == 0
        assert "Rollback pending confirmation: sg-pend01" in result.output

    def test_rollback_failure_prints_error_and_exits_1(self, runner):
        """rollback exits 1 when RollbackResult.success is False and not pending."""
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.needs_confirmation = False
        mock_result.error = "Rollback artifact not found"

        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.rollback.return_value = mock_result

            result = runner.invoke(main, ["rollback", "sg-missing"])

        assert result.exit_code == 1
        assert "Rollback artifact not found" in result.stderr

    def test_rollback_exception_prints_error_and_exits_1(self, runner):
        """rollback exits 1 on exception from Orchestrator."""
        with patch("orchestrator.Orchestrator") as MockOrch:
            instance = MockOrch.return_value
            instance.rollback.side_effect = OSError("disk full")

            result = runner.invoke(main, ["rollback", "vol-err2"])

        assert result.exit_code == 1
        assert "disk full" in result.stderr

    def test_rollback_requires_resource_id_argument(self, runner):
        """rollback with no argument exits non-zero."""
        with patch("orchestrator.Orchestrator"):
            result = runner.invoke(main, ["rollback"])

        assert result.exit_code != 0


# ─── dashboard ────────────────────────────────────────────────────────────────


class TestDashboardCommand:
    """Tests for the `dashboard` subcommand."""

    def test_dashboard_without_streamlit_prints_install_instructions_and_exits_1(
        self, runner
    ):
        """dashboard exits 1 with install instructions when streamlit is missing."""
        # Remove streamlit from sys.modules and force ImportError on import
        with patch.dict(sys.modules, {"streamlit": None}):
            result = runner.invoke(main, ["dashboard"])

        assert result.exit_code == 1
        assert "pip install cloud-janitor[dashboard]" in result.stderr
        assert "Dashboard requires the [dashboard] extra" in result.stderr


# ─── --version ────────────────────────────────────────────────────────────────


class TestVersionOption:
    """Tests for the --version flag."""

    def test_version_prints_version_string(self, runner):
        """--version prints the version and exits 0."""
        result = runner.invoke(main, ["--version"])

        assert result.exit_code == 0
        # The version output should contain the program name and a version string
        assert "cloud-janitor" in result.output
        # Must contain some version-like pattern (digits and dots or 'dev')
        assert any(
            c.isdigit() or c == "." for c in result.output
        ), f"No version digits found in: {result.output!r}"


# ─── unknown subcommand ───────────────────────────────────────────────────────


class TestUnknownSubcommand:
    """Tests for unknown/invalid subcommands."""

    def test_unknown_subcommand_exits_non_zero(self, runner):
        """An unknown subcommand should exit with a non-zero status."""
        result = runner.invoke(main, ["nonexistent-cmd"])

        assert result.exit_code != 0

    def test_unknown_subcommand_shows_error_message(self, runner):
        """An unknown subcommand should mention it's not a valid command."""
        result = runner.invoke(main, ["foobar"])

        assert result.exit_code != 0
        # Click shows "No such command" for invalid subcommands
        assert "No such command" in result.output or "no such command" in result.output.lower()
