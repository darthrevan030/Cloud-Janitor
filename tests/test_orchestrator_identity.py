"""Unit tests for Orchestrator identity wiring (approve/rollback identity resolution).

Tests cover:
1. Real-AWS mode + STS failure → approve() returns success=False, zero subprocess.run calls
2. Sandbox mode + STS failure → action proceeds with fallback actor, actor_verified=False in audit
3. Explicit approver= at construction → STS never called, value used verbatim
4. Concurrency-safety: sequential calls with different resolved actors each produce own audit entry
5. Property 8: Orchestrator Real-AWS Identity Fail-Closed Invariant (Hypothesis)

Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 1.7, 1.8
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.core.identity import ActorResolution, IdentityResolutionError
from cloud_janitor.orchestrator.orchestrator import Orchestrator, ApprovalResult
from cloud_janitor.agents.remediation_architect import RemediationPlan


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def orchestrator_root(tmp_path: Path) -> Path:
    """Create the directory structure required by Orchestrator(project_root=...)."""
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "remediations").mkdir()
    (tmp_path / "output" / "rollbacks").mkdir()
    (tmp_path / "output" / "logs").mkdir()
    (tmp_path / "output" / "policies").mkdir()
    (tmp_path / "hooks").mkdir()
    # Minimal findings_store.json so orchestrator internals don't fail on load
    findings = {
        "schema_version": "1.0.0",
        "findings": [
            {
                "id": "resource-123",
                "resource_id": "resource-123",
                "resource_type": "ebs",
                "agent": "finops",
                "category": "waste",
                "severity": "MEDIUM",
                "title": "Idle EBS volume",
                "description": "test",
                "cost_estimate_monthly": 10.0,
                "idle_days": 45,
                "metadata": {},
                "detected_at": "2025-01-01T00:00:00Z",
            }
        ],
    }
    (tmp_path / "output" / "findings_store.json").write_text(
        json.dumps(findings), encoding="utf-8"
    )
    return tmp_path


def _make_orchestrator(
    root: Path,
    approver: str | None = None,
) -> Orchestrator:
    """Build an Orchestrator pointed at a tmp directory with a resource plan ready."""
    orch = Orchestrator(project_root=root, approver=approver)
    # Inject a plan so approve() passes the _find_plan gate
    plan = RemediationPlan(
        resource_id="resource-123",
        finding={
            "id": "resource-123",
            "resource_id": "resource-123",
            "resource_type": "ebs",
            "category": "waste",
        },
        blocked=False,
        remediation_hcl='resource "null_resource" "test" {}',
        rollback_hcl='resource "null_resource" "rollback" {}',
    )
    orch._last_plans = [plan]
    return orch


# ---------------------------------------------------------------------------
# Test 1: Real-AWS mode + STS failure → approve() returns success=False,
#          zero subprocess.run calls
# ---------------------------------------------------------------------------

class TestRealAwsStsFailureBlocksApproval:
    """Req 1.3: When identity cannot be verified in real-AWS mode, approve() fails
    and no Terraform subprocess is ever spawned."""

    @patch("subprocess.run")
    @patch("cloud_janitor.orchestrator.orchestrator.resolve_actor")
    def test_approve_returns_failure_on_identity_error(
        self, mock_resolve, mock_subprocess, orchestrator_root: Path
    ):
        mock_resolve.side_effect = IdentityResolutionError("STS unavailable")

        with patch.dict(os.environ, {"JANITOR_DRY_RUN": "1"}, clear=False):
            orch = _make_orchestrator(orchestrator_root, approver=None)
            result = orch.approve("APPROVE resource-123", "resource-123")

        assert isinstance(result, ApprovalResult)
        assert result.success is False
        assert "Identity verification failed" in (result.error or "")
        # Critical: subprocess.run must never have been called
        mock_subprocess.assert_not_called()

    @patch("subprocess.run")
    @patch("cloud_janitor.orchestrator.orchestrator.resolve_actor")
    def test_audit_trail_contains_identity_verification_failed(
        self, mock_resolve, mock_subprocess, orchestrator_root: Path
    ):
        mock_resolve.side_effect = IdentityResolutionError("expired session")

        with patch.dict(os.environ, {"JANITOR_DRY_RUN": "1"}, clear=False):
            orch = _make_orchestrator(orchestrator_root, approver=None)
            orch.approve("APPROVE resource-123", "resource-123")

        trail = orch.get_audit_trail()
        identity_entries = [
            e for e in trail if e.action == "identity_verification_failed"
        ]
        assert len(identity_entries) >= 1
        assert identity_entries[0].result == "blocked"


# ---------------------------------------------------------------------------
# Test 2: Sandbox mode + STS failure → action proceeds with fallback actor,
#          actor_verified=False in audit trail
# ---------------------------------------------------------------------------

class TestSandboxFallbackActorInAudit:
    """Req 1.5: In sandbox mode, resolve_actor returns an unverified fallback
    and approve() proceeds. The audit trail records actor_verified=False."""

    @patch("cloud_janitor.orchestrator.orchestrator.resolve_actor")
    def test_sandbox_fallback_actor_stamped_in_audit(
        self, mock_resolve, orchestrator_root: Path
    ):
        mock_resolve.return_value = ActorResolution(
            actor="fallback-user", verified=False
        )

        with patch.dict(os.environ, {"JANITOR_DRY_RUN": "1"}, clear=False):
            orch = _make_orchestrator(orchestrator_root, approver=None)
            result = orch.approve("APPROVE resource-123", "resource-123")

        assert result.success is True

        trail = orch.get_audit_trail()
        approval_entries = [
            e for e in trail if e.action == "approval" and e.result == "success"
        ]
        assert len(approval_entries) == 1
        assert approval_entries[0].actor == "fallback-user"
        assert approval_entries[0].actor_verified is False

    @patch("cloud_janitor.orchestrator.orchestrator.resolve_actor")
    def test_sandbox_fallback_does_not_block_execution(
        self, mock_resolve, orchestrator_root: Path
    ):
        """Even though actor is unverified, the dry-run approval completes."""
        mock_resolve.return_value = ActorResolution(
            actor="sandbox-operator", verified=False
        )

        with patch.dict(os.environ, {"JANITOR_DRY_RUN": "1"}, clear=False):
            orch = _make_orchestrator(orchestrator_root, approver=None)
            result = orch.approve("APPROVE resource-123", "resource-123")

        assert result.success is True
        assert result.resource_id == "resource-123"


# ---------------------------------------------------------------------------
# Test 3: Explicit approver= at construction → STS never called
# ---------------------------------------------------------------------------

class TestExplicitApproverBypassesSts:
    """Req 1.6: When approver is explicitly provided, STS is never called."""

    @patch("cloud_janitor.orchestrator.orchestrator.resolve_actor")
    def test_sts_not_called_when_explicit_approver(
        self, mock_resolve, orchestrator_root: Path
    ):
        with patch.dict(os.environ, {"JANITOR_DRY_RUN": "1"}, clear=False):
            orch = _make_orchestrator(orchestrator_root, approver="explicit@corp")
            result = orch.approve("APPROVE resource-123", "resource-123")

        # resolve_actor should never be invoked
        mock_resolve.assert_not_called()
        assert result.success is True

    @patch("cloud_janitor.orchestrator.orchestrator.resolve_actor")
    def test_explicit_approver_used_verbatim_in_audit(
        self, mock_resolve, orchestrator_root: Path
    ):
        with patch.dict(os.environ, {"JANITOR_DRY_RUN": "1"}, clear=False):
            orch = _make_orchestrator(orchestrator_root, approver="explicit@corp")
            orch.approve("APPROVE resource-123", "resource-123")

        trail = orch.get_audit_trail()
        approval_entries = [
            e for e in trail if e.action == "approval" and e.result == "success"
        ]
        assert len(approval_entries) == 1
        assert approval_entries[0].actor == "explicit@corp"
        # Explicit approver is treated as verified
        assert approval_entries[0].actor_verified is True

    @patch("cloud_janitor.orchestrator.orchestrator.resolve_actor")
    def test_explicit_approver_system_string_still_bypasses_sts(
        self, mock_resolve, orchestrator_root: Path
    ):
        """Explicitly passing 'system' is NOT the same as omitting approver."""
        with patch.dict(os.environ, {"JANITOR_DRY_RUN": "1"}, clear=False):
            orch = _make_orchestrator(orchestrator_root, approver="system")
            orch.approve("APPROVE resource-123", "resource-123")

        mock_resolve.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: Concurrency-safety — sequential calls each get own actor
# ---------------------------------------------------------------------------

class TestConcurrencySafetySequentialActors:
    """Req 1.8: Each call resolves its own actor into a local variable.
    Two sequential calls with different resolved actors must produce
    distinct audit entries, not share a stale instance attribute."""

    @patch("cloud_janitor.orchestrator.orchestrator.resolve_actor")
    def test_sequential_calls_produce_distinct_actors(
        self, mock_resolve, orchestrator_root: Path
    ):
        mock_resolve.side_effect = [
            ActorResolution(actor="arn:aws:iam::111111111111:user/Alice", verified=True),
            ActorResolution(actor="arn:aws:iam::222222222222:user/Bob", verified=True),
        ]

        with patch.dict(os.environ, {"JANITOR_DRY_RUN": "1"}, clear=False):
            orch = _make_orchestrator(orchestrator_root, approver=None)
            # Need a second resource plan for the second approve call
            plan2 = RemediationPlan(
                resource_id="resource-456",
                finding={
                    "id": "resource-456",
                    "resource_id": "resource-456",
                    "resource_type": "ebs",
                    "category": "waste",
                },
                blocked=False,
                remediation_hcl='resource "null_resource" "test2" {}',
                rollback_hcl='resource "null_resource" "rollback2" {}',
            )
            orch._last_plans.append(plan2)

            result1 = orch.approve("APPROVE resource-123", "resource-123")
            result2 = orch.approve("APPROVE resource-456", "resource-456")

        assert result1.success is True
        assert result2.success is True

        trail = orch.get_audit_trail()
        approval_successes = [
            e for e in trail if e.action == "approval" and e.result == "success"
        ]
        assert len(approval_successes) == 2
        actors = [e.actor for e in approval_successes]
        assert actors[0] == "arn:aws:iam::111111111111:user/Alice"
        assert actors[1] == "arn:aws:iam::222222222222:user/Bob"
        # Critical: they must NOT be the same actor
        assert actors[0] != actors[1]


# ---------------------------------------------------------------------------
# Test 5 / Property 8: Orchestrator Real-AWS Identity Fail-Closed Invariant
# ---------------------------------------------------------------------------

# Strategy: generate arbitrary exception types that resolve_actor could raise
_EXCEPTION_TYPES = st.sampled_from([
    RuntimeError,
    ConnectionError,
    TimeoutError,
    OSError,
    ValueError,
    PermissionError,
    IOError,
])


@settings(max_examples=50, deadline=10000)
@given(exc_type=_EXCEPTION_TYPES, msg=st.text(min_size=1, max_size=100))
def test_property_approve_always_fails_when_identity_unresolvable(
    exc_type: type, msg: str, tmp_path_factory
):
    """Property 8: For ANY exception raised by resolve_actor, approve() MUST
    return success=False and subprocess.run MUST NOT be called.

    This is the fail-closed invariant: no identity verification = no action.
    """
    root = tmp_path_factory.mktemp("orch")
    # Set up directory structure
    (root / "output").mkdir(exist_ok=True)
    (root / "output" / "remediations").mkdir(exist_ok=True)
    (root / "output" / "rollbacks").mkdir(exist_ok=True)
    (root / "output" / "logs").mkdir(exist_ok=True)
    (root / "output" / "policies").mkdir(exist_ok=True)
    (root / "hooks").mkdir(exist_ok=True)

    findings = {
        "schema_version": "1.0.0",
        "findings": [
            {
                "id": "resource-123",
                "resource_id": "resource-123",
                "resource_type": "ebs",
                "agent": "finops",
                "category": "waste",
                "severity": "MEDIUM",
                "title": "Idle EBS volume",
                "description": "test",
                "cost_estimate_monthly": 10.0,
                "idle_days": 45,
                "metadata": {},
                "detected_at": "2025-01-01T00:00:00Z",
            }
        ],
    }
    (root / "output" / "findings_store.json").write_text(
        json.dumps(findings), encoding="utf-8"
    )

    with (
        patch("cloud_janitor.orchestrator.orchestrator.resolve_actor") as mock_resolve,
        patch("subprocess.run") as mock_subprocess,
        patch.dict(os.environ, {"JANITOR_DRY_RUN": "1"}, clear=False),
    ):
        mock_resolve.side_effect = IdentityResolutionError(msg)

        orch = Orchestrator(project_root=root, approver=None)
        plan = RemediationPlan(
            resource_id="resource-123",
            finding={
                "id": "resource-123",
                "resource_id": "resource-123",
                "resource_type": "ebs",
                "category": "waste",
            },
            blocked=False,
            remediation_hcl='resource "null_resource" "test" {}',
            rollback_hcl='resource "null_resource" "rollback" {}',
        )
        orch._last_plans = [plan]

        result = orch.approve("APPROVE resource-123", "resource-123")

        # Invariant: approve MUST fail
        assert result.success is False, (
            f"approve() returned success=True when resolve_actor raised "
            f"{exc_type.__name__}({msg!r}) — fail-closed invariant violated"
        )
        # Invariant: no subprocess was ever called
        mock_subprocess.assert_not_called()
