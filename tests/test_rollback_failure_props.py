"""Property-based tests for rollback failure error propagation.

**Validates: Requirements 1.4, 1.6**

Property 3: Rollback Failure Error Propagation

For any non-zero exit code and any stderr string produced by TF_CMD, the rollback
handler SHALL return a RollbackResult with success=False, the exit code preserved
in exit_code, and the stderr content included in error. The original rollback file
SHALL remain unchanged on disk.
"""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from agents.approval_gate import ApprovalGateStore
from agents.remediation_architect import RemediationPlan
from orchestrator import Orchestrator, RollbackResult


# --- Strategies ---

# Non-zero exit codes (1-255)
nonzero_exit_codes = st.integers(min_value=1, max_value=255)

# Arbitrary stderr strings (printable text, non-empty to ensure meaningful error)
stderr_strings = st.text(
    st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=200,
)

# Filesystem-safe resource IDs (alphanumeric + hyphens + underscores, 1-40 chars)
_fs_safe_chars = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)
fs_safe_resource_ids = st.text(_fs_safe_chars, min_size=1, max_size=40)


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
    # After ROLLBACK command, the resource should be in pending state
    assert result.needs_confirmation is True, (
        f"ROLLBACK {resource_id} should set needs_confirmation=True, "
        f"got: success={result.success}, error={result.error}"
    )


# --- Property Tests ---


class TestProperty3RollbackFailureErrorPropagation:
    """Property 3: Rollback Failure Error Propagation.

    For any non-zero exit code and any stderr string produced by TF_CMD, the
    rollback handler SHALL return a RollbackResult with success=False, the exit
    code preserved in exit_code, and the stderr content included in error. The
    original rollback file SHALL remain unchanged on disk.
    """

    @given(
        resource_id=fs_safe_resource_ids,
        exit_code=nonzero_exit_codes,
        stderr_text=stderr_strings,
    )
    @settings(
        max_examples=50,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_validate_failure_propagates_error(
        self, resource_id, exit_code, stderr_text
    ):
        """When terraform validate returns a non-zero exit code, RollbackResult
        must have success=False, exit_code matching the subprocess return code,
        and error containing the stderr text. The rollback file must be unchanged."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))
            orch = _setup_orchestrator(project_dir, resource_id)

            # Record the original rollback file content
            rollback_file = project_dir / "output" / "rollbacks" / f"{resource_id}.tf"
            original_content = rollback_file.read_text()

            # Set up pending rollback state
            _initiate_pending_rollback(orch, resource_id)

            # Mock subprocess.run to simulate validate failure
            failed_validate = subprocess.CompletedProcess(
                args=["terraform", "validate"],
                returncode=exit_code,
                stdout="",
                stderr=stderr_text,
            )

            with patch("subprocess.run", return_value=failed_validate) as mock_run:
                result = orch.rollback(f"CONFIRM ROLLBACK {resource_id}")

            # --- Assertions ---
            # RollbackResult fields
            assert isinstance(result, RollbackResult), (
                f"Expected RollbackResult, got {type(result)}"
            )
            assert result.success is False, (
                f"Expected success=False for validate failure, got success={result.success}"
            )
            assert result.exit_code == exit_code, (
                f"Expected exit_code={exit_code}, got exit_code={result.exit_code}"
            )
            assert result.error is not None, (
                "Expected error to be non-None for validate failure"
            )
            assert stderr_text.strip() in result.error, (
                f"Expected stderr '{stderr_text.strip()}' in error, got: '{result.error}'"
            )
            assert result.resource_id == resource_id, (
                f"Expected resource_id='{resource_id}', got '{result.resource_id}'"
            )

            # Rollback file must be unchanged
            assert rollback_file.exists(), (
                "Rollback file should still exist after validate failure"
            )
            assert rollback_file.read_text() == original_content, (
                "Rollback file content must not be modified after validate failure"
            )

    @given(
        resource_id=fs_safe_resource_ids,
        exit_code=nonzero_exit_codes,
        stderr_text=stderr_strings,
    )
    @settings(
        max_examples=50,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_apply_failure_propagates_error(
        self, resource_id, exit_code, stderr_text
    ):
        """When terraform validate succeeds but apply returns a non-zero exit code,
        RollbackResult must have success=False, exit_code matching the subprocess
        return code, and error containing the stderr text. The rollback file must
        be unchanged."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))
            orch = _setup_orchestrator(project_dir, resource_id)

            # Record the original rollback file content
            rollback_file = project_dir / "output" / "rollbacks" / f"{resource_id}.tf"
            original_content = rollback_file.read_text()

            # Set up pending rollback state
            _initiate_pending_rollback(orch, resource_id)

            # Mock subprocess.run: validate succeeds, apply fails
            successful_validate = subprocess.CompletedProcess(
                args=["terraform", "validate"],
                returncode=0,
                stdout="Success!",
                stderr="",
            )
            failed_apply = subprocess.CompletedProcess(
                args=["terraform", "apply", "-auto-approve"],
                returncode=exit_code,
                stdout="",
                stderr=stderr_text,
            )

            def side_effect(*args, **kwargs):
                cmd = args[0] if args else kwargs.get("args", [])
                if "validate" in cmd:
                    return successful_validate
                elif "apply" in cmd:
                    return failed_apply
                return successful_validate

            with patch("subprocess.run", side_effect=side_effect) as mock_run:
                result = orch.rollback(f"CONFIRM ROLLBACK {resource_id}")

            # --- Assertions ---
            # RollbackResult fields
            assert isinstance(result, RollbackResult), (
                f"Expected RollbackResult, got {type(result)}"
            )
            assert result.success is False, (
                f"Expected success=False for apply failure, got success={result.success}"
            )
            assert result.exit_code == exit_code, (
                f"Expected exit_code={exit_code}, got exit_code={result.exit_code}"
            )
            assert result.error is not None, (
                "Expected error to be non-None for apply failure"
            )
            assert stderr_text.strip() in result.error, (
                f"Expected stderr '{stderr_text.strip()}' in error, got: '{result.error}'"
            )
            assert result.resource_id == resource_id, (
                f"Expected resource_id='{resource_id}', got '{result.resource_id}'"
            )

            # Rollback file must be unchanged
            assert rollback_file.exists(), (
                "Rollback file should still exist after apply failure"
            )
            assert rollback_file.read_text() == original_content, (
                "Rollback file content must not be modified after apply failure"
            )

    @given(resource_id=fs_safe_resource_ids)
    @settings(
        max_examples=50,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_missing_rollback_file_returns_error(self, resource_id):
        """When the rollback file doesn't exist, RollbackResult must have
        success=False and error identifying the missing path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))
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

            # Set pending rollback state manually (since ROLLBACK command
            # checks file existence too, we bypass by adding to the set directly)
            orch._pending_rollbacks.add(resource_id)

            # Confirm the file does NOT exist
            rollback_file = project_dir / "output" / "rollbacks" / f"{resource_id}.tf"
            assert not rollback_file.exists(), "Test setup error: file should not exist"

            # Attempt confirm rollback
            result = orch.rollback(f"CONFIRM ROLLBACK {resource_id}")

            # --- Assertions ---
            assert isinstance(result, RollbackResult), (
                f"Expected RollbackResult, got {type(result)}"
            )
            assert result.success is False, (
                f"Expected success=False for missing file, got success={result.success}"
            )
            assert result.error is not None, (
                "Expected error to be non-None for missing rollback file"
            )
            assert resource_id in result.error, (
                f"Error should identify the resource '{resource_id}', got: '{result.error}'"
            )
            expected_path_fragment = f"rollbacks/{resource_id}.tf"
            assert expected_path_fragment in result.error, (
                f"Error should identify the missing path '{expected_path_fragment}', "
                f"got: '{result.error}'"
            )
