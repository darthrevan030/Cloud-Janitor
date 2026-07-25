"""Unit tests for plan persistence wiring (Task 6.4).

Tests validate that the Orchestrator correctly wires plan persistence through
the StateStore, and that cross-instance visibility works for the approve() flow.

- execute_audit() → approve() on a second Orchestrator instance finds the plan
- Blocked plan not found by second instance
- Second execute_audit() replaces first run's plans
- AuditResult.success=True + AuditResult.warnings non-empty when replace_plans() fails

**Validates: Requirements 2.3, 2.4, 2.5**
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


from cloud_janitor.agents.remediation_architect import RemediationPlan
from cloud_janitor.orchestrator import Orchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_findings_store(tmp_path: Path) -> Path:
    """Write a valid findings_store.json with entries from both agents."""
    (tmp_path / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "policies").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "remediations").mkdir(parents=True, exist_ok=True)
    (tmp_path / "hooks").mkdir(parents=True, exist_ok=True)

    # Create hook scripts so execute_audit doesn't fail at step 5
    pre_hook = tmp_path / "hooks" / "pre-remediation.sh"
    pre_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    pre_hook.chmod(0o755)
    post_hook = tmp_path / "hooks" / "post-remediation.sh"
    post_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    post_hook.chmod(0o755)

    store = {
        "schema_version": "1.0.0",
        "scan_id": "test-scan-001",
        "started_at": "2025-01-15T10:00:00Z",
        "completed_at": "2025-01-15T10:01:00Z",
        "findings": [
            {
                "id": "f1",
                "resource_id": "vol-abc123",
                "resource_type": "ebs",
                "agent": "finops",
                "category": "waste",
                "severity": "MEDIUM",
                "title": "Unattached EBS volume",
                "description": "Idle for 45 days",
                "cost_estimate_monthly": 12.50,
                "idle_days": 45,
                "metadata": {"availability_zone": "us-east-1a", "volume_type": "gp3", "size_gb": 100},
                "detected_at": "2025-01-15T10:00:00Z",
            },
            {
                "id": "f2",
                "resource_id": "sg-web-servers",
                "resource_type": "security_group",
                "agent": "secops",
                "category": "security",
                "severity": "CRITICAL",
                "title": "Open security group",
                "description": "0.0.0.0/0 on Redis port",
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
    """Create an Orchestrator instance with mocked agents pointed at tmp_path."""
    env_patch = {
        "JANITOR_DRY_RUN": "1",
        "JANITOR_BACKEND": "fixture",
    }
    with patch.dict("os.environ", env_patch, clear=False):
        orch = Orchestrator(project_root=tmp_path, approver="test-user")
    return orch


def _mock_agents_for_audit(orch: Orchestrator, plans: list[RemediationPlan]) -> None:
    """Mock FinOps, SecOps, Architect, and pre-hook so execute_audit() succeeds."""
    # Build a valid findings store content inline for the secops side_effect
    _store_content = json.dumps({
        "schema_version": "1.0.0",
        "scan_id": "test-scan-001",
        "started_at": "2025-01-15T10:00:00Z",
        "completed_at": "2025-01-15T10:01:00Z",
        "agents_completed": ["finops", "secops"],
        "findings": [
            {
                "id": "f1", "resource_id": "vol-abc123", "resource_type": "ebs",
                "agent": "finops", "category": "waste", "severity": "MEDIUM",
                "title": "Unattached EBS volume", "description": "Idle for 45 days",
                "cost_estimate_monthly": 12.50, "idle_days": 45, "metadata": {},
                "detected_at": "2025-01-15T10:00:00Z",
            },
            {
                "id": "f2", "resource_id": "sg-web-servers", "resource_type": "security_group",
                "agent": "secops", "category": "security", "severity": "CRITICAL",
                "title": "Open security group", "description": "0.0.0.0/0 on Redis port",
                "cost_estimate_monthly": 0.0, "idle_days": 0,
                "metadata": {"port": 6379, "cidr": "0.0.0.0/0"},
                "detected_at": "2025-01-15T10:00:30Z",
            },
        ],
        "summary": {"total": 2, "by_severity": {"LOW": 0, "MEDIUM": 1, "HIGH": 0, "CRITICAL": 1},
                    "by_agent": {"finops": 1, "secops": 1}, "total_monthly_waste": 12.50},
    }, indent=2)

    orch._finops.scan = MagicMock(return_value=[
        {
            "id": "f1",
            "resource_id": "vol-abc123",
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
        }
    ])

    def _secops_scan():
        orch._secops.findings_store_path.parent.mkdir(parents=True, exist_ok=True)
        orch._secops.findings_store_path.write_text(_store_content)
        return [
            {
                "id": "f2",
                "resource_id": "sg-web-servers",
                "resource_type": "security_group",
                "agent": "secops",
                "category": "security",
                "severity": "CRITICAL",
                "title": "Open security group",
                "description": "0.0.0.0/0 on Redis port",
                "cost_estimate_monthly": 0.0,
                "idle_days": 0,
                "metadata": {"port": 6379, "cidr": "0.0.0.0/0"},
                "detected_at": "2025-01-15T10:00:30Z",
            }
        ]

    orch._secops.scan = MagicMock(side_effect=_secops_scan)
    orch._architect.plan = MagicMock(return_value=plans)
    # Mock the pre-remediation hook to always pass (we're testing plan
    # persistence wiring, not hook validation)
    orch._run_pre_remediation_hook = MagicMock(return_value=None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlanPersistenceWiring:
    """Tests that the Orchestrator correctly wires plan persistence through
    the StateStore for cross-instance approve() visibility."""

    def test_execute_audit_then_approve_on_second_instance(self, tmp_path):
        """execute_audit() persists plans; a second Orchestrator instance
        can approve() against one of those plans (finds it via StateStore).
        """
        _make_findings_store(tmp_path)

        plans = [
            RemediationPlan(
                resource_id="vol-abc123",
                finding={"resource_id": "vol-abc123", "resource_type": "ebs", "category": "waste"},
                blocked=False,
                block_reason="",
                dependency_report=None,
                remediation_hcl='resource "null_resource" "test" {}',
                rollback_hcl='resource "null_resource" "rollback" {}',
            ),
        ]

        # Instance 1: run audit to persist plans
        env_patch = {"JANITOR_DRY_RUN": "1", "JANITOR_BACKEND": "fixture"}
        with patch.dict("os.environ", env_patch, clear=False):
            orch1 = Orchestrator(project_root=tmp_path, approver="test-user")
        _mock_agents_for_audit(orch1, plans)
        result = orch1.execute_audit()
        assert result.success is True, f"execute_audit() failed: {result.error}"

        # Write rollback artifact (approve checks it's present for rollback path)
        (tmp_path / "output" / "rollbacks" / "vol-abc123.tf").write_text(
            'resource "null_resource" "rollback" {}'
        )

        # Instance 2: approve() should find the plan via StateStore
        with patch.dict("os.environ", env_patch, clear=False):
            orch2 = Orchestrator(project_root=tmp_path, approver="test-user")

            with patch("cloud_janitor.orchestrator.orchestrator.resolve_actor", return_value="test-user"):
                approval = orch2.approve("APPROVE vol-abc123")

        assert approval.success is True, (
            f"approve() on second instance failed: {approval.error}"
        )
        assert approval.resource_id == "vol-abc123"

    def test_blocked_plan_not_found_by_second_instance(self, tmp_path):
        """A blocked plan persisted by execute_audit() is NOT returned by
        get_plan() on a second instance — blocked plans are filtered.
        """
        _make_findings_store(tmp_path)

        plans = [
            RemediationPlan(
                resource_id="vol-blocked",
                finding={"resource_id": "vol-blocked", "resource_type": "ebs", "category": "waste"},
                blocked=True,
                block_reason="Has active dependencies",
                dependency_report=None,
                remediation_hcl='resource "null_resource" "test" {}',
                rollback_hcl=None,
            ),
        ]

        env_patch = {"JANITOR_DRY_RUN": "1", "JANITOR_BACKEND": "fixture"}
        with patch.dict("os.environ", env_patch, clear=False):
            orch1 = Orchestrator(project_root=tmp_path, approver="test-user")
        _mock_agents_for_audit(orch1, plans)
        result = orch1.execute_audit()
        assert result.success is True

        # Instance 2: approve() should NOT find the blocked plan
        with patch.dict("os.environ", env_patch, clear=False):
            orch2 = Orchestrator(project_root=tmp_path, approver="test-user")

        approval = orch2.approve("APPROVE vol-blocked")
        assert approval.success is False
        assert "No remediation plan found" in approval.error

    def test_second_execute_audit_replaces_first_runs_plans(self, tmp_path):
        """A second execute_audit() replaces the first run's plans wholesale.
        The first run's plans are no longer visible on any instance.
        """
        _make_findings_store(tmp_path)

        plans_run1 = [
            RemediationPlan(
                resource_id="vol-first-run",
                finding={"resource_id": "vol-first-run", "resource_type": "ebs", "category": "waste"},
                blocked=False,
                remediation_hcl='resource "null_resource" "first" {}',
                rollback_hcl=None,
            ),
        ]
        plans_run2 = [
            RemediationPlan(
                resource_id="vol-second-run",
                finding={"resource_id": "vol-second-run", "resource_type": "ebs", "category": "waste"},
                blocked=False,
                remediation_hcl='resource "null_resource" "second" {}',
                rollback_hcl=None,
            ),
        ]

        env_patch = {"JANITOR_DRY_RUN": "1", "JANITOR_BACKEND": "fixture"}
        with patch.dict("os.environ", env_patch, clear=False):
            orch = Orchestrator(project_root=tmp_path, approver="test-user")

        # Run 1
        _mock_agents_for_audit(orch, plans_run1)
        r1 = orch.execute_audit()
        assert r1.success is True

        # Run 2 (replaces run 1's plans)
        _mock_agents_for_audit(orch, plans_run2)
        r2 = orch.execute_audit()
        assert r2.success is True

        # Instance 2: first run's plan is gone, second run's plan is present
        with patch.dict("os.environ", env_patch, clear=False):
            orch2 = Orchestrator(project_root=tmp_path, approver="test-user")

            # First run's plan should NOT be found
            approval_old = orch2.approve("APPROVE vol-first-run")
            assert approval_old.success is False, (
                "First run's plan should be replaced and not found"
            )
            assert "No remediation plan found" in approval_old.error

            # Second run's plan should be found
            (tmp_path / "output" / "rollbacks" / "vol-second-run.tf").write_text(
                'resource "null_resource" "rollback" {}'
            )
            with patch("cloud_janitor.orchestrator.orchestrator.resolve_actor", return_value="test-user"):
                approval_new = orch2.approve("APPROVE vol-second-run")
            assert approval_new.success is True, (
                f"Second run's plan should be visible: {approval_new.error}"
            )

    def test_audit_result_warnings_when_replace_plans_fails(self, tmp_path):
        """When StateStore.replace_plans() returns False (mocked failure),
        AuditResult.success is still True (audit completed) but
        AuditResult.warnings is non-empty to surface the persistence failure.
        """
        _make_findings_store(tmp_path)

        plans = [
            RemediationPlan(
                resource_id="vol-abc123",
                finding={"resource_id": "vol-abc123", "resource_type": "ebs", "category": "waste"},
                blocked=False,
                remediation_hcl='resource "null_resource" "test" {}',
                rollback_hcl=None,
            ),
        ]

        env_patch = {"JANITOR_DRY_RUN": "1", "JANITOR_BACKEND": "fixture"}
        with patch.dict("os.environ", env_patch, clear=False):
            orch = Orchestrator(project_root=tmp_path, approver="test-user")
        _mock_agents_for_audit(orch, plans)

        # Mock StateStore.replace_plans to fail
        with patch.object(orch._state_store, "replace_plans", return_value=False):
            result = orch.execute_audit()

        # Audit still succeeds — the pipeline ran — but warnings should be present
        assert result.success is True, (
            f"Audit should succeed even when replace_plans fails: {result.error}"
        )
        assert len(result.warnings) > 0, (
            "AuditResult.warnings must be non-empty when replace_plans() fails"
        )
        # At least one warning must mention persistence
        assert any("persist" in w.lower() or "state" in w.lower() for w in result.warnings), (
            f"Expected a persistence-related warning, got: {result.warnings}"
        )
