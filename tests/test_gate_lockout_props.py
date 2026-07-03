"""Property-based tests for approval gate lockout invariant.

**Validates: Requirements 1.2, 1.5**

Property 1: Approval Gate Lockout Invariant

For any resource ID and any sequence of failed approval attempts, the gate
SHALL become locked after exactly max_attempts (3) failures, and once locked,
all subsequent rollback and approval requests for that resource SHALL be
rejected with success=False regardless of process restarts.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from agents.approval_gate import ApprovalGateStore
from agents.remediation_architect import RemediationPlan
from orchestrator import Orchestrator


# --- Strategies ---

# Filesystem-safe resource IDs (no / : . to avoid path issues)
_fs_safe_chars = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)
fs_safe_resource_ids = st.text(_fs_safe_chars, min_size=1, max_size=40)

# Invalid approval commands — things that will NOT parse as valid "APPROVE <rid>"
_invalid_command_strats = st.one_of(
    # Wrong prefix entirely
    st.just("bad input"),
    # Lowercase approve
    st.just("approve"),
    # Missing resource_id
    st.just("APPROVE"),
    # Extra whitespace
    st.text(st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=30).filter(
        lambda s: not s.startswith("APPROVE ")
    ),
)

# Number of extra attempts after lockout to verify continued rejection
extra_attempts_after_lock = st.integers(min_value=1, max_value=5)


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


def _setup_orchestrator(project_dir: Path, resource_id: str) -> "Orchestrator":
    """Create an Orchestrator with a plan and rollback artifact for the given resource."""
    orch = Orchestrator(project_root=project_dir, approver="test-user")

    # Inject a remediation plan so approve() doesn't short-circuit at "no plan found"
    orch._last_plans = [
        RemediationPlan(
            resource_id=resource_id,
            finding={"resource_id": resource_id, "resource_type": "ebs"},
            blocked=False,
            remediation_hcl='resource "null_resource" "test" {}',
            rollback_hcl='resource "null_resource" "rollback" {}',
        ),
    ]

    # Create rollback artifact so rollback doesn't fail at "file not found"
    rollback_file = project_dir / "output" / "rollbacks" / f"{resource_id}.tf"
    rollback_file.write_text('resource "null_resource" "rollback" {}')

    return orch


# --- Property Test ---


class TestProperty1GateLockoutInvariant:
    """Property 1: Approval Gate Lockout Invariant.

    For any resource ID and any sequence of failed approval attempts, the gate
    SHALL become locked after exactly max_attempts (3) failures, and once locked,
    all subsequent rollback and approval requests for that resource SHALL be
    rejected with success=False regardless of process restarts.
    """

    @given(
        resource_id=fs_safe_resource_ids,
        extra_attempts=extra_attempts_after_lock,
    )
    @settings(
        max_examples=50,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_lockout_after_max_attempts_rejects_all_operations(
        self, resource_id, extra_attempts
    ):
        """After 3 failed approvals, approve() and rollback() both return success=False,
        and this persists across process restarts (new Orchestrator instances)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))

            # --- Phase 1: Exhaust the gate with exactly 3 bad attempts ---
            orch = _setup_orchestrator(project_dir, resource_id)

            for i in range(3):
                result = orch.approve("bad command that wont match", resource_id=resource_id)
                assert result.success is False, (
                    f"Attempt {i + 1} should fail (invalid command), "
                    f"but got success=True"
                )

            # After 3 failures, the gate MUST be locked
            gate = orch._get_or_create_gate(resource_id)
            assert gate.locked is True, (
                f"Gate should be locked after 3 failed attempts, "
                f"but locked={gate.locked}, attempts={gate.attempts}"
            )
            assert gate.attempts == 3, (
                f"Gate should have exactly 3 attempts, got {gate.attempts}"
            )

            # --- Phase 2: Verify locked gate rejects approve (even valid commands) ---
            valid_approve_cmd = f"APPROVE {resource_id}"
            approve_result = orch.approve(valid_approve_cmd, resource_id=resource_id)
            assert approve_result.success is False, (
                "Locked gate should reject even valid APPROVE command"
            )
            assert approve_result.locked is True, (
                f"ApprovalResult should indicate locked=True, got: {approve_result}"
            )

            # --- Phase 3: Verify locked gate rejects rollback ---
            rollback_result = orch.rollback(f"ROLLBACK {resource_id}")
            assert rollback_result.success is False, (
                "Locked gate should reject ROLLBACK command"
            )

            # --- Phase 4: Additional attempts after lockout still rejected ---
            for _ in range(extra_attempts):
                r = orch.approve("another bad attempt", resource_id=resource_id)
                assert r.success is False, (
                    "All post-lockout approve attempts must return success=False"
                )

            # --- Phase 5: Simulate process restart — new Orchestrator, same project_root ---
            # The new instance loads persisted gate state from disk
            orch2 = _setup_orchestrator(project_dir, resource_id)

            # Verify the restarted orchestrator still has the gate locked
            gate2 = orch2._get_or_create_gate(resource_id)
            assert gate2.locked is True, (
                "Gate lockout must survive process restart — "
                f"new Orchestrator loaded locked={gate2.locked}, attempts={gate2.attempts}"
            )
            assert gate2.attempts >= 3, (
                f"Persisted attempts should be >= 3 after restart, got {gate2.attempts}"
            )

            # Verify approve still rejected after restart
            approve_after_restart = orch2.approve(valid_approve_cmd, resource_id=resource_id)
            assert approve_after_restart.success is False, (
                "Approve must be rejected after process restart when gate is locked"
            )

            # Verify rollback still rejected after restart
            rollback_after_restart = orch2.rollback(f"ROLLBACK {resource_id}")
            assert rollback_after_restart.success is False, (
                "Rollback must be rejected after process restart when gate is locked"
            )

    @given(resource_id=fs_safe_resource_ids)
    @settings(
        max_examples=50,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_gate_not_locked_before_max_attempts(self, resource_id):
        """The gate must NOT be locked before reaching exactly 3 failures.

        This is the negative-case complement: verifying the gate doesn't
        lock prematurely (e.g., after 1 or 2 attempts).
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))
            orch = _setup_orchestrator(project_dir, resource_id)

            # After 1 failure: not locked
            orch.approve("wrong command 1", resource_id=resource_id)
            gate = orch._get_or_create_gate(resource_id)
            assert gate.locked is False, (
                f"Gate should NOT be locked after 1 attempt, "
                f"but locked={gate.locked}, attempts={gate.attempts}"
            )
            assert gate.attempts == 1

            # After 2 failures: still not locked
            orch.approve("wrong command 2", resource_id=resource_id)
            assert gate.locked is False, (
                f"Gate should NOT be locked after 2 attempts, "
                f"but locked={gate.locked}, attempts={gate.attempts}"
            )
            assert gate.attempts == 2

            # Gate should still allow a valid approval at this point
            # (we don't actually execute it since tf apply would run,
            # but the gate itself isn't blocking)
            assert gate.locked is False
