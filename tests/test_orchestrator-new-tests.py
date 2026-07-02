"""Regression tests for rollback apply behavior."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator import Orchestrator


@pytest.fixture
def tmp_project(tmp_path):
    """Set up a temporary project structure for testing."""
    (tmp_path / "hooks").mkdir(parents=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True)
    (tmp_path / "output" / "logs").mkdir(parents=True)
    (tmp_path / "output" / "policies").mkdir(parents=True)

    pre_hook = tmp_path / "hooks" / "pre-remediation.sh"
    pre_hook.write_text("#!/usr/bin/env bash\nexit 0\n")

    post_hook = tmp_path / "hooks" / "post-remediation.sh"
    post_hook.write_text("#!/usr/bin/env bash\nexit 0\n")

    return tmp_path


class TestRollbackApply:
    """Regression tests: CONFIRM ROLLBACK must run terraform apply."""

    def test_confirm_rollback_applies_terraform(self, tmp_project):
        """CONFIRM ROLLBACK must actually run terraform apply, not just log success.

        Regression test: previously _handle_confirm_rollback marked the rollback
        as successful and fired the post-remediation hook without ever applying
        the rollback .tf against LocalStack.
        """
        orch = Orchestrator(project_root=tmp_project, approver="test-user")
        (tmp_project / "output" / "rollbacks" / "vol-abc123.tf").write_text(
            'resource "null_resource" "rollback" {}'
        )

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

            orch.rollback("ROLLBACK vol-abc123")
            orch.rollback("CONFIRM ROLLBACK vol-abc123")

            apply_calls = [
                c for c in mock_run.call_args_list
                if "apply" in str(c.args[0]) and "-auto-approve" in str(c.args[0])
            ]
            assert len(apply_calls) == 1, (
                "CONFIRM ROLLBACK must invoke terraform/tflocal apply before "
                "reporting success"
            )

    def test_confirm_rollback_fails_if_apply_fails(self, tmp_project):
        """If terraform apply fails during rollback, success must not be reported
        and the post-remediation hook must not fire."""
        orch = Orchestrator(project_root=tmp_project, approver="test-user")
        (tmp_project / "output" / "rollbacks" / "vol-abc123.tf").write_text(
            'resource "null_resource" "rollback" {}'
        )

        def fake_run(cmd, **kwargs):
            if "apply" in cmd and "-auto-approve" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr="apply failed"
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("orchestrator.subprocess.run", side_effect=fake_run) as mock_run:
            orch.rollback("ROLLBACK vol-abc123")
            result = orch.rollback("CONFIRM ROLLBACK vol-abc123")

            assert result.success is False
            assert "apply failed" in result.error

            hook_calls = [
                c for c in mock_run.call_args_list
                if "post-remediation.sh" in str(c)
            ]
            assert len(hook_calls) == 0, (
                "Post-remediation hook must not fire when the rollback apply fails"
            )
