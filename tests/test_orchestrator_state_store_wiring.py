"""Wiring integrity tests for Orchestrator ↔ StateStore (Task 10.2).

Mocks StateStore.get_plan / has_pending_rollback / get_audit_trail and asserts
each is called by the corresponding Orchestrator method — validating that the
Orchestrator actually delegates to StateStore rather than using stale in-memory
state.

**Validates: Requirement 10.1 (wiring correctness)**
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from cloud_janitor.agents.remediation_architect import RemediationPlan
from cloud_janitor.orchestrator import AuditEntry, Orchestrator
from cloud_janitor.core.state_store import StateStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_findings_store(tmp_path: Path) -> Path:
    """Write a minimal valid findings_store.json."""
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
                "resource_id": "vol-wiring-test",
                "resource_type": "ebs",
                "agent": "finops",
                "category": "waste",
                "severity": "MEDIUM",
                "title": "Unattached EBS volume",
                "description": "Idle",
                "cost_estimate_monthly": 10.0,
                "idle_days": 45,
                "metadata": {},
                "detected_at": "2025-01-15T10:00:00Z",
            },
            {
                "id": "f2",
                "resource_id": "sg-wiring-test",
                "resource_type": "security_group",
                "agent": "secops",
                "category": "security",
                "severity": "CRITICAL",
                "title": "Open SG",
                "description": "0.0.0.0/0",
                "cost_estimate_monthly": 0.0,
                "idle_days": 0,
                "metadata": {"port": 22},
                "detected_at": "2025-01-15T10:00:30Z",
            },
        ],
        "summary": {
            "total": 2,
            "by_severity": {"LOW": 0, "MEDIUM": 1, "HIGH": 0, "CRITICAL": 1},
            "by_agent": {"finops": 1, "secops": 1},
            "total_monthly_waste": 10.0,
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStateStoreWiring:
    """Verify that Orchestrator methods delegate to StateStore rather than
    using stale in-memory state."""

    def test_approve_calls_state_store_get_plan(self, tmp_path):
        """Orchestrator.approve() calls StateStore.get_plan() to find the plan.

        The approve() flow uses _find_plan() which delegates to
        self._state_store.get_plan(). Mock it and verify it's called with
        the correct resource_id.
        """
        _make_findings_store(tmp_path)
        orch = _make_orchestrator(tmp_path)

        # Mock get_plan to return a plan
        mock_plan = RemediationPlan(
            resource_id="vol-wiring-test",
            finding={"resource_id": "vol-wiring-test", "resource_type": "ebs", "category": "waste"},
            blocked=False,
            remediation_hcl='resource "null_resource" "test" {}',
            rollback_hcl='resource "null_resource" "rollback" {}',
        )

        with patch.object(orch._state_store, "get_plan", return_value=mock_plan) as mock_get_plan:
            with patch("cloud_janitor.orchestrator.orchestrator.resolve_actor", return_value="test-user"):
                orch.approve("APPROVE vol-wiring-test")

        # Verify get_plan was called with the correct resource_id
        mock_get_plan.assert_called_with("vol-wiring-test")

    def test_confirm_rollback_calls_state_store_has_pending_rollback(self, tmp_path):
        """Orchestrator._handle_confirm_rollback() calls
        StateStore.has_pending_rollback() to verify the resource is pending.

        Mock it to return False and verify the call happens.
        """
        _make_findings_store(tmp_path)
        orch = _make_orchestrator(tmp_path)

        resource_id = "vol-wiring-test"
        (tmp_path / "output" / "rollbacks" / f"{resource_id}.tf").write_text(
            'resource "null_resource" "rollback" {}'
        )

        with patch.object(
            orch._state_store, "has_pending_rollback", return_value=False
        ) as mock_has_pending:
            result = orch.rollback(f"CONFIRM ROLLBACK {resource_id}")

        # has_pending_rollback must have been called with the resource_id
        mock_has_pending.assert_called_with(resource_id)

        # With False return, the confirm should fail
        assert result.success is False
        assert "No pending rollback" in result.error

    def test_confirm_rollback_with_pending_calls_has_pending_rollback_true(self, tmp_path):
        """When has_pending_rollback returns True, the confirm flow proceeds.
        Verify the call is made and the flow continues past the check.
        """
        _make_findings_store(tmp_path)
        orch = _make_orchestrator(tmp_path)

        resource_id = "vol-wiring-test"
        (tmp_path / "output" / "rollbacks" / f"{resource_id}.tf").write_text(
            'resource "null_resource" "rollback" {}'
        )

        with patch.object(
            orch._state_store, "has_pending_rollback", return_value=True
        ) as mock_has_pending:
            with patch("cloud_janitor.orchestrator.orchestrator.resolve_actor", return_value="test-user"):
                result = orch.rollback(f"CONFIRM ROLLBACK {resource_id}")

        # has_pending_rollback must have been called
        mock_has_pending.assert_called_with(resource_id)

        # The flow proceeded past the pending check (may succeed or fail at TF step,
        # but it did NOT fail with "No pending rollback" error)
        if not result.success:
            assert "No pending rollback" not in (result.error or ""), (
                "Should have passed the pending check with mocked True return"
            )

    def test_get_audit_trail_calls_state_store_get_audit_trail(self, tmp_path):
        """Orchestrator.get_audit_trail() calls StateStore.get_audit_trail()
        as its primary data source.
        """
        _make_findings_store(tmp_path)
        orch = _make_orchestrator(tmp_path)

        # Create some known audit entries to return
        fake_entries = [
            AuditEntry(
                timestamp="2025-01-15T10:00:00Z",
                action="test_wiring",
                resource_id="vol-wiring-test",
                actor="wiring-tester",
                result="success",
                details="verifying wiring",
            ),
        ]

        with patch.object(
            orch._state_store, "get_audit_trail", return_value=fake_entries
        ) as mock_get_trail:
            trail = orch.get_audit_trail()

        # Verify get_audit_trail was called on the state store
        mock_get_trail.assert_called_once()

        # Verify the return value is what the mock provided
        assert trail is fake_entries, (
            "get_audit_trail() should return the StateStore's result directly"
        )
        assert len(trail) == 1
        assert trail[0].action == "test_wiring"
        assert trail[0].actor == "wiring-tester"

    def test_get_audit_trail_falls_back_when_state_store_returns_none(self, tmp_path):
        """When StateStore.get_audit_trail() returns None, the Orchestrator
        falls back to self._audit_trail rather than returning None.
        """
        _make_findings_store(tmp_path)
        orch = _make_orchestrator(tmp_path)

        # Add an entry to the in-memory trail directly
        in_memory_entry = AuditEntry(
            timestamp="2025-01-15T11:00:00Z",
            action="in_memory_only",
            resource_id="vol-inmem",
            actor="test-user",
            result="success",
            details="only in memory",
        )
        orch._audit_trail.append(in_memory_entry)

        with patch.object(
            orch._state_store, "get_audit_trail", return_value=None
        ) as mock_get_trail:
            trail = orch.get_audit_trail()

        # The mock was called
        mock_get_trail.assert_called_once()

        # Fallback to in-memory: the trail should contain our entry
        assert len(trail) >= 1
        actions = [e.action for e in trail]
        assert "in_memory_only" in actions, (
            f"Fallback trail should include in-memory entries, got: {actions}"
        )

    def test_rollback_calls_state_store_add_pending_rollback(self, tmp_path):
        """Orchestrator.rollback() calls StateStore.add_pending_rollback()
        when a valid ROLLBACK command is processed.
        """
        _make_findings_store(tmp_path)
        orch = _make_orchestrator(tmp_path)

        resource_id = "vol-wiring-test"
        (tmp_path / "output" / "rollbacks" / f"{resource_id}.tf").write_text(
            'resource "null_resource" "rollback" {}'
        )

        with patch.object(
            orch._state_store, "add_pending_rollback", return_value=True
        ) as mock_add_pending:
            result = orch.rollback(f"ROLLBACK {resource_id}")

        # add_pending_rollback must have been called with the resource_id
        mock_add_pending.assert_called_with(resource_id)
        assert result.needs_confirmation is True
