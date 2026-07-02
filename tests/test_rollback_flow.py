"""Unit tests for the rollback Terraform sequence (init → apply).

**Validates: Requirements 1.3, 1.4, 1.6**

Tests the actual _handle_confirm_rollback() implementation which:
1. Copies rollback HCL to remediation.tf
2. Runs TF_CMD init -input=false
3. On init success, runs TF_CMD apply -auto-approve
4. Returns RollbackResult with success/failure info

NOTE: The current implementation does NOT populate exit_code in RollbackResult
on failure — it only sets error with a formatted message. Tests reflect this
actual behavior.
"""

import subprocess
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agents.approval_gate import ApprovalGateStore
from agents.remediation_architect import RemediationPlan
from orchestrator import Orchestrator, RollbackResult


# --- Helpers ---


def _make_project_dirs(tmp_path: Path) -> Path:
    """Create the minimal project structure needed for Orchestrator init."""
    (tmp_path / "hooks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "policies").mkdir(parents=True, exist_ok=True)

    pre_hook = tmp_path / "hooks" / "pre-remediation.sh"
    pre_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    post_hook = tmp_path / "hooks" / "post-remediation.sh"
    post_hook.write_text("#!/usr/bin/env bash\nexit 0\n")

    return tmp_path


def _setup_orchestrator(project_dir: Path, resource_id: str) -> Orchestrator:
    """Create an Orchestrator with a plan and rollback artifact for the given resource."""
    with patch("orchestrator._validate_tf_cmd", return_value="tflocal"):
        orch = Orchestrator(project_root=project_dir, approver="test-user")

    # Inject a remediation plan so rollback flow doesn't short-circuit
    orch._last_plans = [
        RemediationPlan(
            resource_id=resource_id,
            finding={"resource_id": resource_id, "resource_type": "ebs"},
            blocked=False,
            remediation_hcl='resource "null_resource" "test" {}',
            rollback_hcl='resource "null_resource" "rollback" {}',
        ),
    ]

    # Create rollback artifact
    rollback_file = project_dir / "output" / "rollbacks" / f"{resource_id}.tf"
    rollback_file.write_text('resource "null_resource" "rollback" {}')

    return orch


def _initiate_pending_rollback(orch: Orchestrator, resource_id: str) -> None:
    """Set up the pending rollback state by calling ROLLBACK first."""
    result = orch.rollback(f"ROLLBACK {resource_id}")
    assert result.needs_confirmation is True, (
        f"ROLLBACK {resource_id} should set needs_confirmation=True, "
        f"got: success={result.success}, error={result.error}"
    )


# --- Tests ---


class TestInitApplySuccessSequence:
    """Test the happy path: init succeeds → apply succeeds → RollbackResult(success=True)."""

    def test_init_apply_success_sequence(self, tmp_path: Path):
        """When both init and apply return exit 0, rollback succeeds."""
        project_dir = _make_project_dirs(tmp_path)
        resource_id = "vol-abc123"
        orch = _setup_orchestrator(project_dir, resource_id)

        # Set up pending rollback state
        _initiate_pending_rollback(orch, resource_id)

        # Mock subprocess.run: both init and apply succeed
        success_init = subprocess.CompletedProcess(
            args=["tflocal", "init", "-input=false"],
            returncode=0,
            stdout="Terraform has been successfully initialized!",
            stderr="",
        )
        success_apply = subprocess.CompletedProcess(
            args=["tflocal", "apply", "-auto-approve"],
            returncode=0,
            stdout="Apply complete! Resources: 1 added.",
            stderr="",
        )

        def side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "init" in cmd:
                return success_init
            elif "apply" in cmd:
                return success_apply
            return success_init

        with patch("subprocess.run", side_effect=side_effect):
            result = orch.rollback(f"CONFIRM ROLLBACK {resource_id}")

        assert isinstance(result, RollbackResult)
        assert result.success is True
        assert result.resource_id == resource_id
        assert result.error is None


class TestInitFailureReturnsError:
    """Test that init failure returns RollbackResult(success=False) with error."""

    def test_init_failure_returns_rollback_result_with_error(self, tmp_path: Path):
        """When init returns exit 1 with stderr, result has success=False and error
        containing the stderr text. exit_code is NOT set (known gap in implementation)."""
        project_dir = _make_project_dirs(tmp_path)
        resource_id = "sg-deadbeef"
        orch = _setup_orchestrator(project_dir, resource_id)

        _initiate_pending_rollback(orch, resource_id)

        stderr_msg = "Error: Could not load plugin"
        failed_init = subprocess.CompletedProcess(
            args=["tflocal", "init", "-input=false"],
            returncode=1,
            stdout="",
            stderr=stderr_msg,
        )

        with patch("subprocess.run", return_value=failed_init):
            result = orch.rollback(f"CONFIRM ROLLBACK {resource_id}")

        assert isinstance(result, RollbackResult)
        assert result.success is False
        assert result.resource_id == resource_id
        assert result.error is not None
        # Error message should include stderr content
        assert stderr_msg in result.error
        # Error message is formatted as "tflocal init failed: <error>"
        assert "init failed" in result.error
        # exit_code should be populated with the subprocess returncode
        assert result.exit_code == 1


class TestApplyFailurePreservesRollbackFile:
    """Test that apply failure after successful init preserves the rollback file."""

    def test_apply_failure_after_successful_init_preserves_rollback_file(
        self, tmp_path: Path
    ):
        """When init succeeds but apply fails, the original rollback file
        must remain unchanged (content and mtime preserved)."""
        project_dir = _make_project_dirs(tmp_path)
        resource_id = "cache-cluster-1"
        orch = _setup_orchestrator(project_dir, resource_id)

        _initiate_pending_rollback(orch, resource_id)

        # Record the original rollback file content and mtime
        rollback_file = project_dir / "output" / "rollbacks" / f"{resource_id}.tf"
        original_content = rollback_file.read_text()
        # Small sleep to ensure any modification would be detectable
        time.sleep(0.05)
        original_mtime = rollback_file.stat().st_mtime

        # Mock subprocess.run: init succeeds, apply fails
        success_init = subprocess.CompletedProcess(
            args=["tflocal", "init", "-input=false"],
            returncode=0,
            stdout="Terraform has been successfully initialized!",
            stderr="",
        )
        stderr_msg = "Error: Failed to apply changes"
        failed_apply = subprocess.CompletedProcess(
            args=["tflocal", "apply", "-auto-approve"],
            returncode=1,
            stdout="",
            stderr=stderr_msg,
        )

        def side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "init" in cmd:
                return success_init
            elif "apply" in cmd:
                return failed_apply
            return success_init

        with patch("subprocess.run", side_effect=side_effect):
            result = orch.rollback(f"CONFIRM ROLLBACK {resource_id}")

        # Verify failure result
        assert result.success is False
        assert result.resource_id == resource_id
        assert "apply failed" in result.error
        assert stderr_msg in result.error

        # Verify rollback file is unchanged
        assert rollback_file.exists(), "Rollback file must still exist after apply failure"
        assert rollback_file.read_text() == original_content, (
            "Rollback file content must not be modified after apply failure"
        )
        assert rollback_file.stat().st_mtime == original_mtime, (
            "Rollback file mtime must not change after apply failure"
        )


class TestMissingRollbackFileReturnsError:
    """Test that a missing rollback file returns RollbackResult(success=False) with path info."""

    def test_missing_rollback_file_returns_error_with_path(self, tmp_path: Path):
        """When the rollback file doesn't exist, the result identifies the missing path."""
        project_dir = _make_project_dirs(tmp_path)
        resource_id = "vol-missing"

        with patch("orchestrator._validate_tf_cmd", return_value="tflocal"):
            orch = Orchestrator(project_root=project_dir, approver="test-user")

        # Inject a plan but do NOT create the rollback file
        orch._last_plans = [
            RemediationPlan(
                resource_id=resource_id,
                finding={"resource_id": resource_id, "resource_type": "ebs"},
                blocked=False,
                remediation_hcl='resource "null_resource" "test" {}',
                rollback_hcl='resource "null_resource" "rollback" {}',
            ),
        ]

        # Manually add to pending set (bypassing ROLLBACK command which also checks file)
        orch._pending_rollbacks.add(resource_id)

        # Confirm the file does NOT exist
        rollback_file = project_dir / "output" / "rollbacks" / f"{resource_id}.tf"
        assert not rollback_file.exists()

        # Attempt confirm rollback
        result = orch.rollback(f"CONFIRM ROLLBACK {resource_id}")

        assert isinstance(result, RollbackResult)
        assert result.success is False
        assert result.resource_id == resource_id
        assert result.error is not None
        # Error must identify the missing path
        assert f"rollbacks/{resource_id}.tf" in result.error
