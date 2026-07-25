"""Unit tests for rollback persistence wiring (Task 7.3).

Tests validate that the Orchestrator correctly wires pending rollback state
through the StateStore for cross-instance CONFIRM ROLLBACK flows.

- ROLLBACK <id> on one instance → CONFIRM ROLLBACK <id> on second instance succeeds
- Repeated ROLLBACK <id> doesn't duplicate
- CONFIRM ROLLBACK with no pending entry returns error

**Validates: Requirements 3.1, 3.2, 3.3**
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


from cloud_janitor.agents.remediation_architect import RemediationPlan
from cloud_janitor.orchestrator import Orchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_findings_store(tmp_path: Path) -> Path:
    """Write a valid findings_store.json."""
    (tmp_path / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "policies").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "remediations").mkdir(parents=True, exist_ok=True)
    (tmp_path / "hooks").mkdir(parents=True, exist_ok=True)
    store = {
        "schema_version": "1.0.0",
        "scan_id": "test-scan-001",
        "started_at": "2025-01-15T10:00:00Z",
        "completed_at": "2025-01-15T10:01:00Z",
        "findings": [
            {
                "id": "f1",
                "resource_id": "vol-rb-test",
                "resource_type": "ebs",
                "agent": "finops",
                "category": "waste",
                "severity": "MEDIUM",
                "title": "Unattached EBS volume",
                "description": "Idle for 45 days",
                "cost_estimate_monthly": 12.50,
                "idle_days": 45,
                "metadata": {},
                "detected_at": "2025-01-15T10:00:00Z",
            },
            {
                "id": "f2",
                "resource_id": "sg-rb-test",
                "resource_type": "security_group",
                "agent": "secops",
                "category": "security",
                "severity": "CRITICAL",
                "title": "Open SG",
                "description": "0.0.0.0/0",
                "cost_estimate_monthly": 0.0,
                "idle_days": 0,
                "metadata": {"port": 6379, "cidr": "0.0.0.0/0"},
                "detected_at": "2025-01-15T10:00:30Z",
            },
        ],
        "summary": {
            "total": 2,
            "by_severity": {"LOW": 0, "MEDIUM": 1, "HIGH": 0, "CRITICAL": 1},
            "by_agent": {"finops": 1, "secops": 1},
            "total_monthly_waste": 12.50,
        },
    }
    store_path = tmp_path / "output" / "findings_store.json"
    store_path.write_text(json.dumps(store, indent=2))
    return store_path


def _make_orchestrator(tmp_path: Path) -> Orchestrator:
    """Create an Orchestrator instance pointing at tmp_path."""
    env_patch = {"JANITOR_DRY_RUN": "1", "JANITOR_BACKEND": "fixture"}
    with patch.dict("os.environ", env_patch, clear=False):
        orch = Orchestrator(project_root=tmp_path, approver="test-user")
    return orch


def _setup_rollback_preconditions(tmp_path: Path, resource_id: str) -> None:
    """Create the rollback artifact file that the rollback flow checks for."""
    rollback_path = tmp_path / "output" / "rollbacks" / f"{resource_id}.tf"
    rollback_path.write_text(
        f'resource "null_resource" "rollback_{resource_id.replace("-", "_")}" {{}}'
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRollbackPersistenceWiring:
    """Tests that the Orchestrator correctly wires pending rollback state
    through StateStore for cross-instance CONFIRM ROLLBACK flows."""

    def test_rollback_on_instance1_confirm_on_instance2_succeeds(self, tmp_path):
        """ROLLBACK <id> on one instance marks the resource as pending in
        StateStore. CONFIRM ROLLBACK <id> on a SECOND instance (same
        project_root) finds it and succeeds.
        """
        _make_findings_store(tmp_path)
        resource_id = "vol-rb-test"
        _setup_rollback_preconditions(tmp_path, resource_id)

        env_patch = {"JANITOR_DRY_RUN": "1", "JANITOR_BACKEND": "fixture"}

        # Instance 1: issue ROLLBACK
        with patch.dict("os.environ", env_patch, clear=False):
            orch1 = Orchestrator(project_root=tmp_path, approver="test-user")

        # Inject a plan so the rollback finds a plan for scope check
        plan = RemediationPlan(
            resource_id=resource_id,
            finding={"resource_id": resource_id, "resource_type": "ebs", "category": "waste"},
            blocked=False,
            remediation_hcl='resource "null_resource" "test" {}',
            rollback_hcl='resource "null_resource" "rollback" {}',
        )
        orch1._state_store.replace_plans([plan], "test-run")

        rb_result = orch1.rollback(f"ROLLBACK {resource_id}")
        assert rb_result.needs_confirmation is True, (
            f"ROLLBACK should return needs_confirmation=True, got: {rb_result}"
        )

        # Instance 2: CONFIRM ROLLBACK (different Orchestrator instance, same file)
        with patch.dict("os.environ", env_patch, clear=False):
            orch2 = Orchestrator(project_root=tmp_path, approver="test-user")

        import subprocess as sp
        with patch("cloud_janitor.orchestrator.orchestrator.resolve_actor", return_value="test-user"), \
             patch("cloud_janitor.orchestrator.orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = sp.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            confirm_result = orch2.rollback(f"CONFIRM ROLLBACK {resource_id}")

        assert confirm_result.success is True, (
            f"CONFIRM ROLLBACK on second instance should succeed: {confirm_result.error}"
        )
        assert confirm_result.resource_id == resource_id

    def test_repeated_rollback_does_not_duplicate(self, tmp_path):
        """Calling ROLLBACK <id> twice for the same resource does NOT
        duplicate the pending_rollbacks entry (idempotent INSERT OR IGNORE).
        """
        _make_findings_store(tmp_path)
        resource_id = "vol-rb-test"
        _setup_rollback_preconditions(tmp_path, resource_id)

        env_patch = {"JANITOR_DRY_RUN": "1", "JANITOR_BACKEND": "fixture"}
        with patch.dict("os.environ", env_patch, clear=False):
            orch = Orchestrator(project_root=tmp_path, approver="test-user")

        # Inject a plan
        plan = RemediationPlan(
            resource_id=resource_id,
            finding={"resource_id": resource_id, "resource_type": "ebs", "category": "waste"},
            blocked=False,
            remediation_hcl='resource "null_resource" "test" {}',
            rollback_hcl='resource "null_resource" "rollback" {}',
        )
        orch._state_store.replace_plans([plan], "test-run")

        # First ROLLBACK
        r1 = orch.rollback(f"ROLLBACK {resource_id}")
        assert r1.needs_confirmation is True

        # Second ROLLBACK (same resource) — should not error or duplicate
        r2 = orch.rollback(f"ROLLBACK {resource_id}")
        # The second call should still indicate needs_confirmation
        assert r2.needs_confirmation is True

        # Verify only 1 row in pending_rollbacks
        rows = orch._state_store.execute_readonly_query(
            "SELECT COUNT(*) FROM pending_rollbacks WHERE resource_id = ?",
            (resource_id,),
        )
        assert rows[0][0] == 1, (
            f"Expected exactly 1 pending_rollbacks row, got {rows[0][0]} — "
            f"duplicate insert was not idempotent"
        )

    def test_confirm_rollback_with_no_pending_entry_returns_error(self, tmp_path):
        """CONFIRM ROLLBACK for a resource that was never ROLLBACK'd returns
        an error indicating no pending rollback exists.
        """
        _make_findings_store(tmp_path)
        resource_id = "vol-never-rolled-back"
        _setup_rollback_preconditions(tmp_path, resource_id)

        env_patch = {"JANITOR_DRY_RUN": "1", "JANITOR_BACKEND": "fixture"}
        with patch.dict("os.environ", env_patch, clear=False):
            orch = Orchestrator(project_root=tmp_path, approver="test-user")

        # Attempt CONFIRM ROLLBACK without a prior ROLLBACK
        result = orch.rollback(f"CONFIRM ROLLBACK {resource_id}")

        assert result.success is False, (
            "CONFIRM ROLLBACK should fail when no pending rollback exists"
        )
        assert "No pending rollback" in result.error, (
            f"Error should mention 'No pending rollback', got: {result.error}"
        )
