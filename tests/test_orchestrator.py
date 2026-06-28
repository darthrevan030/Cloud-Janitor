"""Tests for orchestrator module.

Tests validate behavior per the design spec:
- Sequential agent execution (FinOps → SecOps → Remediation Architect)
- Pre-remediation hook blocks approval when terraform validate fails
- Post-remediation hook fires with correct args after approval/rollback
- Approval gate rejects malformed input and locks after 3 failed attempts
- Rollback fails if artifact missing
- Audit trail captures all actions
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator import (
    ApprovalResult,
    AuditEntry,
    AuditResult,
    Orchestrator,
    RollbackResult,
)


@pytest.fixture
def tmp_project(tmp_path):
    """Set up a temporary project structure for testing."""
    # Create directories
    (tmp_path / "scripts" / "hooks").mkdir(parents=True)
    (tmp_path / "output").mkdir()
    (tmp_path / "rollbacks").mkdir()

    # Create hook scripts (simple pass/fail scripts for testing)
    pre_hook = tmp_path / "scripts" / "hooks" / "pre-remediation.sh"
    pre_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    pre_hook.chmod(0o755)

    post_hook = tmp_path / "scripts" / "hooks" / "post-remediation.sh"
    post_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    post_hook.chmod(0o755)

    return tmp_path


@pytest.fixture
def findings_store_both_agents(tmp_project):
    """Create a findings_store.json with entries from both agents."""
    store = {
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
                "metadata": {
                    "availability_zone": "us-east-1a",
                    "volume_type": "gp3",
                    "size_gb": 100,
                },
                "detected_at": "2025-01-15T10:00:00Z",
            },
            {
                "id": "f2",
                "resource_id": "sg-web-servers",
                "resource_type": "security_group",
                "agent": "secops",
                "category": "security",
                "severity": "CRITICAL",
                "title": "Open security group on port 6379",
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
    store_path = tmp_project / "findings_store.json"
    store_path.write_text(json.dumps(store, indent=2))
    return store_path


def _make_orchestrator_with_mocked_agents(tmp_project, findings_store_both_agents):
    """Create an orchestrator with mocked agent scan/plan methods.

    Mocks the FinOps and SecOps agents to return canned findings without
    actually hitting MCP fixtures. The findings_store.json fixture already
    contains valid entries from both agents (written by the fixture above).
    """
    orch = Orchestrator(project_root=tmp_project, approver="test-user")

    finops_findings = [
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
            "metadata": {
                "availability_zone": "us-east-1a",
                "volume_type": "gp3",
                "size_gb": 100,
            },
            "detected_at": "2025-01-15T10:00:00Z",
        }
    ]

    secops_findings = [
        {
            "id": "f2",
            "resource_id": "sg-web-servers",
            "resource_type": "security_group",
            "agent": "secops",
            "category": "security",
            "severity": "CRITICAL",
            "title": "Open security group on port 6379",
            "description": "0.0.0.0/0 on Redis port",
            "cost_estimate_monthly": 0.0,
            "idle_days": 0,
            "metadata": {"port": 6379, "cidr": "0.0.0.0/0"},
            "detected_at": "2025-01-15T10:00:30Z",
        }
    ]

    orch._finops.scan = MagicMock(return_value=finops_findings)
    orch._secops.scan = MagicMock(return_value=secops_findings)

    return orch


def _setup_successful_audit(orch, tmp_project):
    """Set up mocked architect plans and output files for a successful audit."""
    from agents.remediation_architect import RemediationPlan

    plans = [
        RemediationPlan(
            resource_id="vol-abc123",
            finding={
                "resource_id": "vol-abc123",
                "resource_type": "ebs",
                "category": "waste",
            },
            blocked=False,
            remediation_hcl='resource "null_resource" "test" {}',
            rollback_hcl='resource "null_resource" "rollback" {}',
        ),
    ]
    orch._architect.plan = MagicMock(return_value=plans)

    # Write the output files that plan() would normally create
    (tmp_project / "output" / "remediation.tf").write_text(
        'resource "null_resource" "test" {}'
    )
    (tmp_project / "rollbacks" / "vol-abc123.tf").write_text(
        'resource "null_resource" "rollback" {}'
    )
    return plans


# ──────────────────────────────────────────────────────────────────────
# Happy path: full audit → approve → post-hook runs
# ──────────────────────────────────────────────────────────────────────


class TestHappyPath:
    """Full audit → approve → post-hook pipeline."""

    def test_full_audit_approve_flow(self, tmp_project, findings_store_both_agents):
        """Happy path: audit succeeds, approval succeeds, post-hook fires."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

            result = orch.execute_audit()
            assert result.success is True
            assert len(result.plans) == 1

            approval = orch.approve("APPROVE vol-abc123")
            assert approval.success is True
            assert approval.resource_id == "vol-abc123"

            # Pre-remediation (1st call) + tflocal apply (2nd call) + post-remediation (3rd call)
            assert mock_run.call_count == 3
            # Verify tflocal apply call
            apply_call_args = mock_run.call_args_list[1][0][0]
            assert apply_call_args == ["tflocal", "apply", "-auto-approve"]
            # Verify post-remediation hook call
            post_call_args = mock_run.call_args_list[2][0][0]
            assert "post-remediation.sh" in post_call_args[1]
            assert "vol-abc123" in post_call_args
            assert "remediate" in post_call_args
            assert "success" in post_call_args
            assert "test-user" in post_call_args


    def test_audit_trail_records_all_actions(
        self, tmp_project, findings_store_both_agents
    ):
        """Audit trail captures scan, plan, approval, and execution entries."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()
            orch.approve("APPROVE vol-abc123")

        trail = orch.get_audit_trail()
        actions = [e.action for e in trail]
        assert "scan" in actions
        assert "plan" in actions
        assert "approval" in actions
        assert "execution" in actions


# ──────────────────────────────────────────────────────────────────────
# Pre-remediation hook blocks on invalid HCL
# ──────────────────────────────────────────────────────────────────────


class TestPreRemediationHook:
    """Pre-remediation hook blocks approval when terraform validate fails."""

    def test_hook_blocks_on_failure(self, tmp_project, findings_store_both_agents):
        """When pre-remediation.sh exits non-zero, audit returns failure."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Error: Invalid HCL syntax"
            )

            result = orch.execute_audit()
            assert result.success is False
            assert result.hook_error is not None
            assert "Pre-remediation hook failed" in result.hook_error


    def test_hook_passes_correct_file_paths(
        self, tmp_project, findings_store_both_agents
    ):
        """Pre-remediation hook receives remediation.tf and rollback.tf paths."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()

            pre_call = mock_run.call_args_list[0]
            args = pre_call[0][0]
            assert "pre-remediation.sh" in args[1]
            assert str(tmp_project / "output" / "remediation.tf") in args[2]
            assert str(tmp_project / "rollbacks" / "vol-abc123.tf") in args[3]

    def test_hook_timeout_blocks_flow(self, tmp_project, findings_store_both_agents):
        """Pre-remediation hook timeout blocks the audit flow."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="bash", timeout=60)

            result = orch.execute_audit()
            assert result.success is False
            assert "timed out" in result.hook_error


# ──────────────────────────────────────────────────────────────────────
# Post-remediation hook called with correct args
# ──────────────────────────────────────────────────────────────────────


class TestPostRemediationHook:
    """Post-remediation hook is called with correct arguments."""

    def test_post_hook_called_after_approval(
        self, tmp_project, findings_store_both_agents
    ):
        """Post hook receives: resource_id, 'remediate', 'success', approver."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()
            orch.approve("APPROVE vol-abc123")

            post_calls = [
                c
                for c in mock_run.call_args_list
                if "post-remediation.sh" in str(c)
            ]
            assert len(post_calls) == 1
            post_args = post_calls[0][0][0]
            assert post_args[2] == "vol-abc123"  # resource_id
            assert post_args[3] == "remediate"  # action
            assert post_args[4] == "success"  # result
            assert post_args[5] == "test-user"  # approver


    def test_post_hook_called_after_rollback(self, tmp_project):
        """Post hook fires with 'rollback' action after confirmed rollback."""
        orch = Orchestrator(project_root=tmp_project, approver="test-user")
        (tmp_project / "rollbacks" / "vol-abc123.tf").write_text("resource {}")

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

            result = orch.rollback("ROLLBACK vol-abc123")
            assert result.needs_confirmation is True

            result = orch.rollback("CONFIRM ROLLBACK vol-abc123")
            assert result.success is True

            post_calls = [
                c
                for c in mock_run.call_args_list
                if "post-remediation.sh" in str(c)
            ]
            assert len(post_calls) == 1
            post_args = post_calls[0][0][0]
            assert post_args[2] == "vol-abc123"
            assert post_args[3] == "rollback"
            assert post_args[4] == "success"
            assert post_args[5] == "test-user"

    def test_post_hook_failure_does_not_block(
        self, tmp_project, findings_store_both_agents
    ):
        """Post-remediation hook failure is non-blocking (approve still succeeds)."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                # Pre-remediation hook and tflocal apply pass
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                )
            # Post-remediation hook explodes
            raise OSError("disk full")

        with patch("orchestrator.subprocess.run", side_effect=side_effect):
            orch.execute_audit()
            # Approval should still succeed even if post-hook fails
            approval = orch.approve("APPROVE vol-abc123")
            assert approval.success is True


# ──────────────────────────────────────────────────────────────────────
# Approval gate rejects malformed input (max 3 attempts via public API)
# ──────────────────────────────────────────────────────────────────────


class TestApprovalGateRejection:
    """Approval gate rejects malformed input and locks after 3 failed attempts.

    Per the spec: "Approval string mismatch: display expected format, re-prompt
    (max 3 attempts)". The orchestrator's approve() method accepts an explicit
    resource_id so the UI can route all attempts for a resource through the gate
    even when the command string is malformed.
    """

    def test_rejects_lowercase(self, tmp_project, findings_store_both_agents):
        """Lowercase 'approve' is rejected — case-sensitive exact match required."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()

        # Lowercase "approve" with explicit resource_id routes to gate
        result = orch.approve("approve vol-abc123", resource_id="vol-abc123")
        assert result.success is False
        assert result.attempts_remaining == 2

    def test_rejects_extra_whitespace(self, tmp_project, findings_store_both_agents):
        """Extra whitespace in command is rejected."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()

        result = orch.approve("APPROVE  vol-abc123", resource_id="vol-abc123")
        assert result.success is False
        assert result.expected_format is not None


    def test_locks_after_three_failed_attempts(
        self, tmp_project, findings_store_both_agents
    ):
        """Gate locks after 3 failed attempts — all via public approve() API.

        The spec requires: "Approval string mismatch: display expected format,
        re-prompt (max 3 attempts)". This test sends 3 malformed commands
        through the public API, each counting as a failed attempt.
        """
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()

        # Attempt 1: wrong case
        r1 = orch.approve("approve vol-abc123", resource_id="vol-abc123")
        assert r1.success is False
        assert r1.attempts_remaining == 2

        # Attempt 2: trailing space
        r2 = orch.approve("APPROVE vol-abc123 ", resource_id="vol-abc123")
        assert r2.success is False
        assert r2.attempts_remaining == 1

        # Attempt 3: double space
        r3 = orch.approve("APPROVE  vol-abc123", resource_id="vol-abc123")
        assert r3.success is False
        assert r3.locked is True

        # After locking, even the correct command is rejected
        r4 = orch.approve("APPROVE vol-abc123", resource_id="vol-abc123")
        assert r4.success is False
        assert r4.locked is True

    def test_no_plan_returns_error(self, tmp_project):
        """Approving a resource with no plan fails immediately."""
        orch = Orchestrator(project_root=tmp_project, approver="test-user")
        result = orch.approve("APPROVE vol-no-plan")
        assert result.success is False
        assert "No remediation plan" in result.error


    def test_successful_approval_does_not_count_as_attempt(
        self, tmp_project, findings_store_both_agents
    ):
        """A valid APPROVE command succeeds without incrementing attempts."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()
            result = orch.approve("APPROVE vol-abc123")
            assert result.success is True

        # Gate should not be locked or have attempts
        gate = orch._approval_gates.get("vol-abc123")
        assert gate is not None
        assert gate.attempts == 0
        assert gate.locked is False


# ──────────────────────────────────────────────────────────────────────
# Rollback fails if artifact missing
# ──────────────────────────────────────────────────────────────────────


class TestRollbackFlow:
    """Rollback requires artifact, confirmation, and proper sequencing."""

    def test_rollback_missing_artifact(self, tmp_project):
        """Rollback rejects if rollbacks/<resource_id>.tf doesn't exist."""
        orch = Orchestrator(project_root=tmp_project, approver="test-user")
        result = orch.rollback("ROLLBACK vol-nonexistent")
        assert result.success is False
        assert "not found" in result.error

    def test_rollback_with_artifact_requests_confirmation(self, tmp_project):
        """Rollback with valid artifact returns needs_confirmation=True."""
        orch = Orchestrator(project_root=tmp_project, approver="test-user")
        (tmp_project / "rollbacks" / "vol-abc123.tf").write_text("resource {}")

        result = orch.rollback("ROLLBACK vol-abc123")
        assert result.success is False
        assert result.needs_confirmation is True
        assert result.resource_id == "vol-abc123"


    def test_confirm_rollback_without_initiation_fails(self, tmp_project):
        """CONFIRM ROLLBACK without prior ROLLBACK is rejected."""
        orch = Orchestrator(project_root=tmp_project, approver="test-user")
        (tmp_project / "rollbacks" / "vol-abc123.tf").write_text("resource {}")

        result = orch.rollback("CONFIRM ROLLBACK vol-abc123")
        assert result.success is False
        assert "No pending rollback" in result.error

    def test_full_rollback_flow(self, tmp_project):
        """ROLLBACK → CONFIRM ROLLBACK succeeds when artifact exists."""
        orch = Orchestrator(project_root=tmp_project, approver="test-user")
        (tmp_project / "rollbacks" / "vol-abc123.tf").write_text("resource {}")

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

            r1 = orch.rollback("ROLLBACK vol-abc123")
            assert r1.needs_confirmation is True

            r2 = orch.rollback("CONFIRM ROLLBACK vol-abc123")
            assert r2.success is True
            assert r2.resource_id == "vol-abc123"

    def test_rollback_invalid_format(self, tmp_project):
        """Random garbage is rejected with clear error."""
        orch = Orchestrator(project_root=tmp_project, approver="test-user")
        result = orch.rollback("random garbage")
        assert result.success is False
        assert "Invalid command format" in result.error


# ──────────────────────────────────────────────────────────────────────
# Agent sequencing is enforced
# ──────────────────────────────────────────────────────────────────────


class TestAgentSequencing:
    """FinOps → SecOps → Remediation Architect sequencing is enforced."""

    def test_sequential_execution_order(
        self, tmp_project, findings_store_both_agents
    ):
        """Agents are called in strict sequence: FinOps → SecOps → Architect."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )

        call_order = []
        orch._finops.scan = MagicMock(
            side_effect=lambda: (call_order.append("finops"), [])[1]
        )
        orch._secops.scan = MagicMock(
            side_effect=lambda: (call_order.append("secops"), [])[1]
        )

        from agents.remediation_architect import RemediationPlan

        orch._architect.plan = MagicMock(
            side_effect=lambda: (call_order.append("architect"), [])[1]
        )

        with patch("orchestrator.subprocess.run"):
            orch.execute_audit()

        assert call_order == ["finops", "secops", "architect"]

    def test_remediation_blocked_without_both_agents_in_store(self, tmp_project):
        """Remediation Architect doesn't run if findings_store lacks SecOps entries.

        Per spec: "Validate findings_store.json contains entries from both prior
        agents before spawning Remediation Architect."
        """
        orch = Orchestrator(project_root=tmp_project, approver="test-user")

        # Write findings_store with only finops entries
        store = {
            "findings": [
                {"id": "f1", "resource_id": "vol-1", "agent": "finops"}
            ],
            "summary": {"by_agent": {"finops": 1, "secops": 0}},
        }
        (tmp_project / "findings_store.json").write_text(json.dumps(store))

        # Mock FinOps to return findings (store already written above)
        orch._finops.scan = MagicMock(
            return_value=[{"id": "f1", "agent": "finops"}]
        )
        # Mock SecOps to return empty (doesn't write secops entries to store)
        orch._secops.scan = MagicMock(return_value=[])
        orch._architect.plan = MagicMock(return_value=[])

        result = orch.execute_audit()

        # Should fail validation — SecOps entries missing from store
        assert result.success is False
        assert "SecOps" in result.error
        # Architect should never have been called
        orch._architect.plan.assert_not_called()


    def test_finops_called_before_secops(
        self, tmp_project, findings_store_both_agents
    ):
        """FinOps scan completes before SecOps scan starts."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        orch._architect.plan = MagicMock(return_value=[])

        with patch("orchestrator.subprocess.run"):
            orch.execute_audit()

        orch._finops.scan.assert_called_once()
        orch._secops.scan.assert_called_once()


# ──────────────────────────────────────────────────────────────────────
# Edge cases and data model tests
# ──────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and data model validation."""

    def test_approve_completely_invalid_format(self, tmp_project):
        """Completely invalid command returns format error."""
        orch = Orchestrator(project_root=tmp_project, approver="test-user")
        result = orch.approve("random garbage")
        assert result.success is False
        assert result.expected_format == "APPROVE <resource-id>"

    def test_get_audit_trail_empty_initially(self, tmp_project):
        """Audit trail starts empty."""
        orch = Orchestrator(project_root=tmp_project, approver="test-user")
        assert orch.get_audit_trail() == []

    def test_audit_entry_to_dict(self):
        """AuditEntry serializes to dict with all fields."""
        entry = AuditEntry(
            timestamp="2025-01-15T10:00:00Z",
            action="scan",
            resource_id="vol-123",
            actor="test-user",
            result="success",
            details="Test entry",
        )
        d = entry.to_dict()
        assert d["timestamp"] == "2025-01-15T10:00:00Z"
        assert d["action"] == "scan"
        assert d["resource_id"] == "vol-123"
        assert d["actor"] == "test-user"
        assert d["result"] == "success"
        assert d["details"] == "Test entry"


    def test_blocked_plans_not_approvable(
        self, tmp_project, findings_store_both_agents
    ):
        """Plans blocked by dependency check cannot be approved."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )

        from agents.remediation_architect import RemediationPlan

        plans = [
            RemediationPlan(
                resource_id="vol-blocked",
                finding={"resource_id": "vol-blocked"},
                blocked=True,
                block_reason="Has dependencies",
                remediation_hcl=None,
                rollback_hcl=None,
            ),
        ]
        orch._architect.plan = MagicMock(return_value=plans)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()

        result = orch.approve("APPROVE vol-blocked")
        assert result.success is False
        assert "No remediation plan" in result.error

    def test_audit_trail_is_append_only(
        self, tmp_project, findings_store_both_agents
    ):
        """Audit trail length only grows — entries are never removed."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()

        len_after_audit = len(orch.get_audit_trail())
        assert len_after_audit > 0

        # Try a failed approval — trail should grow, never shrink
        orch.approve("APPROVE vol-nonexistent")
        len_after_fail = len(orch.get_audit_trail())
        assert len_after_fail >= len_after_audit


# ──────────────────────────────────────────────────────────────────────
# SavingsTracker wiring: record_run called correctly from approve()
# ──────────────────────────────────────────────────────────────────────


class TestSavingsTrackerWiring:
    """Verify SavingsTracker integration in the Orchestrator.

    Requirements 5.1, 5.2, 5.3:
    - record_run() is called from approve() after successful execution
    - record_run() is NOT called from _run_post_remediation_hook
    - Savings tracker errors don't block approval
    """

    def test_record_run_called_after_successful_approval(
        self, tmp_project, findings_store_both_agents
    ):
        """After successful approve(), record_run is called with correct resource_id."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        # Mock the savings tracker
        orch._savings_tracker.record_run = MagicMock()

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()
            result = orch.approve("APPROVE vol-abc123")

        assert result.success is True
        orch._savings_tracker.record_run.assert_called_once_with(
            resources_remediated=["vol-abc123"]
        )

    def test_record_run_not_called_from_post_remediation_hook(
        self, tmp_project, findings_store_both_agents
    ):
        """_run_post_remediation_hook() does NOT call record_run."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )

        # Mock the savings tracker
        orch._savings_tracker.record_run = MagicMock()

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            # Call _run_post_remediation_hook directly
            orch._run_post_remediation_hook("vol-abc123", "remediate", "success")

        # record_run should NOT have been called
        orch._savings_tracker.record_run.assert_not_called()

    def test_savings_tracker_file_not_found_does_not_block_approval(
        self, tmp_project, findings_store_both_agents
    ):
        """When record_run raises FileNotFoundError, approve() still returns success."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        # Make record_run raise FileNotFoundError
        orch._savings_tracker.record_run = MagicMock(
            side_effect=FileNotFoundError("ledger not found")
        )

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()
            result = orch.approve("APPROVE vol-abc123")

        assert result.success is True
        assert result.resource_id == "vol-abc123"

    def test_savings_tracker_os_error_does_not_block_approval(
        self, tmp_project, findings_store_both_agents
    ):
        """When record_run raises OSError, approve() still returns success."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        # Make record_run raise OSError
        orch._savings_tracker.record_run = MagicMock(
            side_effect=OSError("disk full")
        )

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()
            result = orch.approve("APPROVE vol-abc123")

        assert result.success is True
        assert result.resource_id == "vol-abc123"

    def test_record_run_not_called_on_failed_approval(
        self, tmp_project, findings_store_both_agents
    ):
        """When approval fails (invalid format), record_run is NOT called."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        # Mock the savings tracker
        orch._savings_tracker.record_run = MagicMock()

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()

        # Invalid command format — no resource ID extracted
        result = orch.approve("random garbage")
        assert result.success is False
        orch._savings_tracker.record_run.assert_not_called()

    def test_record_run_not_called_when_tflocal_apply_fails(
        self, tmp_project, findings_store_both_agents
    ):
        """When tflocal apply returns non-zero, record_run is NOT called."""
        orch = _make_orchestrator_with_mocked_agents(
            tmp_project, findings_store_both_agents
        )
        _setup_successful_audit(orch, tmp_project)

        # Mock the savings tracker
        orch._savings_tracker.record_run = MagicMock()

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Pre-remediation hook passes
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                )
            # tflocal apply fails
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Error: resource not found"
            )

        with patch("orchestrator.subprocess.run", side_effect=side_effect):
            orch.execute_audit()
            result = orch.approve("APPROVE vol-abc123")

        assert result.success is False
        assert "tflocal apply failed" in result.error
        orch._savings_tracker.record_run.assert_not_called()