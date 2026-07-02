"""Unit tests for Orchestrator._run_pre_remediation_hook_full().

Validates: Requirements 5.1, 5.2, 5.3, 5.5

Tests cover:
- Timeout enforcement (60s limit raises TimeoutError)
- Empty rollback file reported as failure
- Hook script non-zero exit code reported as failure
- Successful validation returns correct validated_paths list
"""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agents.remediation_architect import RemediationPlan
from orchestrator import Orchestrator


# --- Helpers ---


def _make_project(tmp_path: Path) -> Path:
    """Create the minimal project structure for Orchestrator."""
    (tmp_path / "hooks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "policies").mkdir(parents=True, exist_ok=True)

    # Create the pre-remediation hook script
    pre_hook = tmp_path / "hooks" / "pre-remediation.sh"
    pre_hook.write_text("#!/usr/bin/env bash\nexit 0\n")

    # Post hook needed by Orchestrator init
    post_hook = tmp_path / "hooks" / "post-remediation.sh"
    post_hook.write_text("#!/usr/bin/env bash\nexit 0\n")

    return tmp_path


def _make_plan(resource_id: str) -> RemediationPlan:
    """Create a RemediationPlan with the given resource_id."""
    return RemediationPlan(
        resource_id=resource_id,
        finding={"resource_id": resource_id, "resource_type": "ebs"},
        blocked=False,
        remediation_hcl='resource "null_resource" "remediate" {}',
        rollback_hcl='resource "null_resource" "rollback" {}',
    )


# --- Test: Timeout raises TimeoutError ---


class TestPreHookTimeout:
    """Requirement 5.5: Hook execution must not exceed 60s total."""

    def test_exceeding_60s_raises_timeout_error(self):
        """When time.monotonic indicates >60s elapsed, TimeoutError is raised
        and remediation is blocked (no validated paths returned)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project(Path(tmp_dir))
            orch = Orchestrator(project_root=project_dir, approver="test-user")

            rollbacks_dir = project_dir / "output" / "rollbacks"

            # Create a valid non-empty rollback file
            (rollbacks_dir / "res-001.tf").write_text("resource {}")

            plans = [_make_plan("res-001")]

            # Mock time.monotonic: first call (start) returns 0.0,
            # second call (loop check) returns 61.0 — exceeds 60s
            with patch("time.monotonic", side_effect=[0.0, 61.0]):
                with pytest.raises(TimeoutError, match="60s timeout"):
                    orch._run_pre_remediation_hook_full(plans)

    def test_timeout_blocks_all_subsequent_plans(self):
        """When timeout fires on the first plan, no plans get validated."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project(Path(tmp_dir))
            orch = Orchestrator(project_root=project_dir, approver="test-user")

            rollbacks_dir = project_dir / "output" / "rollbacks"

            # Create valid rollback files for multiple plans
            for rid in ["res-001", "res-002", "res-003"]:
                (rollbacks_dir / f"{rid}.tf").write_text("resource {}")

            plans = [_make_plan(rid) for rid in ["res-001", "res-002", "res-003"]]

            # Timeout immediately on first loop iteration
            with patch("time.monotonic", side_effect=[0.0, 61.0]):
                with pytest.raises(TimeoutError, match="60s timeout"):
                    orch._run_pre_remediation_hook_full(plans)


# --- Test: Empty rollback file ---


class TestPreHookEmptyRollbackFile:
    """Requirement 5.2: Empty rollback file must be reported as failure."""

    def test_empty_rollback_file_in_failures(self):
        """A plan whose rollback file exists but is 0 bytes appears in failures
        and does NOT appear in validated_paths."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project(Path(tmp_dir))
            orch = Orchestrator(project_root=project_dir, approver="test-user")

            rollbacks_dir = project_dir / "output" / "rollbacks"

            # res-good has a non-empty rollback file
            (rollbacks_dir / "res-good.tf").write_text("resource {}")
            # res-empty has a 0-byte rollback file
            (rollbacks_dir / "res-empty.tf").write_text("")

            plans = [_make_plan("res-good"), _make_plan("res-empty")]

            # Mock subprocess.run to return exit 0 (hook passes)
            hook_success = subprocess.CompletedProcess(
                args=["bash", "hook", "file"],
                returncode=0,
                stdout="",
                stderr="",
            )
            with patch("subprocess.run", return_value=hook_success):
                validated_paths, failures = orch._run_pre_remediation_hook_full(plans)

            # res-empty must be in failures
            assert "res-empty" in failures, (
                f"Empty rollback file should cause failure. failures={failures}"
            )

            # res-empty must NOT be in validated_paths
            validated_stems = {p.stem for p in validated_paths}
            assert "res-empty" not in validated_stems, (
                f"Empty rollback should not be validated. validated={validated_stems}"
            )

            # res-good should be validated
            assert "res-good" in validated_stems, (
                f"Valid rollback should be validated. validated={validated_stems}"
            )


# --- Test: Hook script non-zero exit code ---


class TestPreHookNonZeroExit:
    """Requirement 5.3: Hook script returning non-zero exit blocks the plan."""

    def test_nonzero_exit_code_in_failures(self):
        """A plan whose hook script returns exit code != 0 appears in failures."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project(Path(tmp_dir))
            orch = Orchestrator(project_root=project_dir, approver="test-user")

            rollbacks_dir = project_dir / "output" / "rollbacks"

            # Both plans have valid non-empty rollback files
            (rollbacks_dir / "res-pass.tf").write_text("resource {}")
            (rollbacks_dir / "res-fail.tf").write_text("resource {}")

            plans = [_make_plan("res-pass"), _make_plan("res-fail")]

            def mock_subprocess_run(args, **kwargs):
                """Return exit 0 for res-pass, exit 1 for res-fail."""
                # args = ["bash", hook_path, rollback_path]
                if len(args) >= 3 and "res-fail" in args[2]:
                    return subprocess.CompletedProcess(
                        args=args, returncode=1, stdout="", stderr="validation failed"
                    )
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="", stderr=""
                )

            with patch("subprocess.run", side_effect=mock_subprocess_run):
                validated_paths, failures = orch._run_pre_remediation_hook_full(plans)

            # res-fail must be in failures
            assert "res-fail" in failures, (
                f"Non-zero hook exit should cause failure. failures={failures}"
            )

            # res-pass must be in validated_paths
            validated_stems = {p.stem for p in validated_paths}
            assert "res-pass" in validated_stems, (
                f"Passing hook should be validated. validated={validated_stems}"
            )

            # res-fail must NOT be in validated_paths
            assert "res-fail" not in validated_stems, (
                f"Failed hook should not be validated. validated={validated_stems}"
            )


# --- Test: Successful validation ---


class TestPreHookSuccess:
    """Requirement 5.1: Successful hook returns correct validated_paths."""

    def test_all_plans_validated_successfully(self):
        """When all plans have non-empty rollback files and hook exits 0,
        validated_paths contains correct Path objects and failures is empty."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project(Path(tmp_dir))
            orch = Orchestrator(project_root=project_dir, approver="test-user")

            rollbacks_dir = project_dir / "output" / "rollbacks"

            resource_ids = ["alpha", "beta", "gamma"]
            for rid in resource_ids:
                (rollbacks_dir / f"{rid}.tf").write_text(f'resource "null" "{rid}" {{}}')

            plans = [_make_plan(rid) for rid in resource_ids]

            hook_success = subprocess.CompletedProcess(
                args=["bash", "hook", "file"],
                returncode=0,
                stdout="",
                stderr="",
            )
            with patch("subprocess.run", return_value=hook_success):
                validated_paths, failures = orch._run_pre_remediation_hook_full(plans)

            # failures must be empty
            assert failures == [], (
                f"Expected no failures for valid plans. failures={failures}"
            )

            # validated_paths must have exactly one entry per plan
            assert len(validated_paths) == len(resource_ids), (
                f"Expected {len(resource_ids)} validated paths, "
                f"got {len(validated_paths)}: {validated_paths}"
            )

            # Each validated path must be the correct rollback file path
            expected_paths = {rollbacks_dir / f"{rid}.tf" for rid in resource_ids}
            actual_paths = set(validated_paths)
            assert actual_paths == expected_paths, (
                f"Path mismatch.\n"
                f"Expected: {sorted(str(p) for p in expected_paths)}\n"
                f"Actual:   {sorted(str(p) for p in actual_paths)}"
            )

    def test_validated_paths_are_existing_files(self):
        """Each path in validated_paths must point to an existing non-empty file."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project(Path(tmp_dir))
            orch = Orchestrator(project_root=project_dir, approver="test-user")

            rollbacks_dir = project_dir / "output" / "rollbacks"

            (rollbacks_dir / "vol-123.tf").write_text("resource {}")
            plans = [_make_plan("vol-123")]

            hook_success = subprocess.CompletedProcess(
                args=["bash", "hook", "file"],
                returncode=0,
                stdout="",
                stderr="",
            )
            with patch("subprocess.run", return_value=hook_success):
                validated_paths, failures = orch._run_pre_remediation_hook_full(plans)

            assert failures == []
            assert len(validated_paths) == 1

            # The returned path must actually exist and be non-empty
            path = validated_paths[0]
            assert path.exists(), f"Validated path {path} does not exist"
            assert path.stat().st_size > 0, f"Validated path {path} is empty"
