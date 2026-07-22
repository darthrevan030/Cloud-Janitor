"""Unit tests for audit trail persistence wiring (Task 9.3).

Tests validate that the Orchestrator correctly wires audit trail persistence
through the StateStore, including cross-instance visibility and fallback behavior.

- _log_action() writes appear in get_audit_trail() output
- get_audit_trail() on a second instance includes entries from the first
- Mocked append_audit_entry returning False doesn't affect caller's return value
- Mocked get_audit_trail returning None causes fallback to self._audit_trail

**Validates: Requirements 4.3, 4.5, 5.5**
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cloud_janitor.orchestrator import AuditEntry, Orchestrator


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
                "resource_id": "vol-audit-test",
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
                "resource_id": "sg-audit-test",
                "resource_type": "security_group",
                "agent": "secops",
                "category": "security",
                "severity": "CRITICAL",
                "title": "Open SG",
                "description": "0.0.0.0/0",
                "cost_estimate_monthly": 0.0,
                "idle_days": 0,
                "metadata": {"port": 6379},
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
    """Create an Orchestrator instance pointed at tmp_path."""
    env_patch = {"JANITOR_DRY_RUN": "1", "JANITOR_BACKEND": "fixture"}
    with patch.dict("os.environ", env_patch, clear=False):
        orch = Orchestrator(project_root=tmp_path, approver="test-user")
    return orch


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuditPersistenceWiring:
    """Tests that _log_action() writes persist through StateStore and are
    visible via get_audit_trail() — including cross-instance visibility."""

    def test_log_action_writes_appear_in_get_audit_trail(self, tmp_path):
        """_log_action() writes an entry that get_audit_trail() returns.
        This validates the full write path: _log_action → append_audit_entry
        → SQLite → get_audit_trail() read-back.
        """
        _make_findings_store(tmp_path)
        orch = _make_orchestrator(tmp_path)

        # Trigger _log_action indirectly (it's a private method, but we can
        # call it directly for this unit test)
        orch._log_action("test_action", "vol-audit-test", "success", "test details")
        orch._log_action("second_action", "sg-audit-test", "failure", "more details")

        trail = orch.get_audit_trail()
        assert len(trail) >= 2, (
            f"Expected at least 2 audit entries, got {len(trail)}"
        )

        # Find our specific entries
        actions = [e.action for e in trail]
        assert "test_action" in actions, (
            f"'test_action' not found in audit trail actions: {actions}"
        )
        assert "second_action" in actions, (
            f"'second_action' not found in audit trail actions: {actions}"
        )

        # Verify field content of the first logged entry
        test_entry = next(e for e in trail if e.action == "test_action")
        assert test_entry.resource_id == "vol-audit-test"
        assert test_entry.result == "success"
        assert test_entry.details == "test details"
        assert test_entry.actor == "test-user"

    def test_get_audit_trail_on_second_instance_includes_entries_from_first(self, tmp_path):
        """Entries logged by instance 1 are visible to get_audit_trail()
        on instance 2 — validating cross-process audit trail visibility
        via the shared SQLite state.db.
        """
        _make_findings_store(tmp_path)

        env_patch = {"JANITOR_DRY_RUN": "1", "JANITOR_BACKEND": "fixture"}

        # Instance 1: log some actions
        with patch.dict("os.environ", env_patch, clear=False):
            orch1 = Orchestrator(project_root=tmp_path, approver="user-one")

        orch1._log_action("cross_process_audit", "vol-cross", "success", "from instance 1")
        orch1._log_action("another_action", "sg-cross", "blocked", "also from instance 1")

        # Instance 2: read audit trail (different Orchestrator, same state.db)
        with patch.dict("os.environ", env_patch, clear=False):
            orch2 = Orchestrator(project_root=tmp_path, approver="user-two")

        trail = orch2.get_audit_trail()

        # The entries from instance 1 must be visible
        actions = [e.action for e in trail]
        assert "cross_process_audit" in actions, (
            f"Entry from instance 1 not visible on instance 2. Actions: {actions}"
        )
        assert "another_action" in actions, (
            f"Second entry from instance 1 not visible. Actions: {actions}"
        )

        # Verify actor attribution is preserved
        cross_entry = next(e for e in trail if e.action == "cross_process_audit")
        assert cross_entry.actor == "user-one", (
            f"Actor should be 'user-one' (from instance 1), got: {cross_entry.actor}"
        )
        assert cross_entry.resource_id == "vol-cross"
        assert cross_entry.details == "from instance 1"

    def test_mocked_append_audit_entry_false_does_not_affect_caller_return(self, tmp_path):
        """When StateStore.append_audit_entry() returns False (simulating a
        persistence failure), _log_action() does NOT raise and the calling
        method's return value is unaffected.

        Specifically: execute_audit() still returns success=True when
        append_audit_entry consistently fails (audit persistence is non-blocking).
        """
        _make_findings_store(tmp_path)

        env_patch = {"JANITOR_DRY_RUN": "1", "JANITOR_BACKEND": "fixture"}
        with patch.dict("os.environ", env_patch, clear=False):
            orch = Orchestrator(project_root=tmp_path, approver="test-user")

        # Mock agents for a successful audit
        from cloud_janitor.agents.remediation_architect import RemediationPlan
        plans = [
            RemediationPlan(
                resource_id="vol-audit-test",
                finding={"resource_id": "vol-audit-test", "resource_type": "ebs", "category": "waste"},
                blocked=False,
                remediation_hcl='resource "null_resource" "test" {}',
                rollback_hcl=None,
            ),
        ]
        orch._finops.scan = MagicMock(return_value=[{
            "id": "f1", "resource_id": "vol-audit-test", "resource_type": "ebs",
            "agent": "finops", "category": "waste", "severity": "MEDIUM",
            "title": "Test", "description": "test", "cost_estimate_monthly": 1.0,
            "idle_days": 45, "metadata": {}, "detected_at": "2025-01-15T10:00:00Z",
        }])

        _store_content = (tmp_path / "output" / "findings_store.json").read_text()

        def _secops_scan():
            orch._secops.findings_store_path.parent.mkdir(parents=True, exist_ok=True)
            orch._secops.findings_store_path.write_text(_store_content)
            return [{
                "id": "f2", "resource_id": "sg-audit-test", "resource_type": "security_group",
                "agent": "secops", "category": "security", "severity": "CRITICAL",
                "title": "Test", "description": "test", "cost_estimate_monthly": 0.0,
                "idle_days": 0, "metadata": {"port": 6379}, "detected_at": "2025-01-15T10:00:30Z",
            }]

        orch._secops.scan = MagicMock(side_effect=_secops_scan)
        orch._architect.plan = MagicMock(return_value=plans)

        # Mock append_audit_entry to always return False
        mock_append = MagicMock(return_value=False)
        with patch.object(orch._state_store, "append_audit_entry", mock_append):
            # Also mock pre-hook since we're testing audit persistence, not hook validation
            orch._run_pre_remediation_hook = MagicMock(return_value=None)
            result = orch.execute_audit()

        # The audit should still succeed — append failures are non-blocking
        assert result.success is True, (
            f"execute_audit() should succeed even when append_audit_entry fails: {result.error}"
        )
        # Verify the mock was actually called (not just bypassed)
        assert mock_append.call_count > 0, (
            "append_audit_entry should have been called during execute_audit"
        )

    def test_mocked_get_audit_trail_none_causes_fallback_to_in_memory(self, tmp_path):
        """When StateStore.get_audit_trail() returns None (read failure),
        Orchestrator.get_audit_trail() falls back to self._audit_trail
        (in-memory list).
        """
        _make_findings_store(tmp_path)
        orch = _make_orchestrator(tmp_path)

        # Log some actions so the in-memory trail has entries
        orch._log_action("action_one", "vol-fallback", "success", "detail 1")
        orch._log_action("action_two", "sg-fallback", "failure", "detail 2")

        # Confirm the in-memory list has entries
        assert len(orch._audit_trail) >= 2, (
            "Precondition: in-memory audit trail should have entries"
        )

        # Mock StateStore.get_audit_trail to return None (read failure)
        with patch.object(orch._state_store, "get_audit_trail", return_value=None):
            trail = orch.get_audit_trail()

        # Should fall back to in-memory trail
        assert trail is not None, "get_audit_trail() should not return None"
        assert len(trail) >= 2, (
            f"Fallback trail should have at least 2 entries, got {len(trail)}"
        )

        # Verify the entries match what's in _audit_trail
        actions = [e.action for e in trail]
        assert "action_one" in actions, (
            f"Fallback trail should contain 'action_one', got actions: {actions}"
        )
        assert "action_two" in actions, (
            f"Fallback trail should contain 'action_two', got actions: {actions}"
        )
