"""Tests for subprocess.TimeoutExpired handling in orchestrator.

Validates that when subprocess.run raises TimeoutExpired at each call site
(init, plan/show/apply in approve(), and init/plan/show/apply in rollback),
the orchestrator returns a failure result without propagating the exception.
"""

import subprocess
from unittest.mock import patch

import pytest

from cloud_janitor.agents.remediation_architect import RemediationPlan
from cloud_janitor.core.health import HealthStatus
from cloud_janitor.orchestrator import ApprovalResult, Orchestrator, RollbackResult


@pytest.fixture(autouse=True)
def _mock_health_check():
    """All timeout tests bypass the backend health preflight."""
    healthy = HealthStatus(
        reachable=True, environment="sandbox_localstack", mode="healthy", detail="ok", endpoint="mock"
    )
    with patch("cloud_janitor.orchestrator.orchestrator.check_backend_health", return_value=healthy):
        yield


@pytest.fixture
def tmp_project(tmp_path):
    """Set up a temporary project structure for testing."""
    (tmp_path / "hooks").mkdir(parents=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True)
    (tmp_path / "output" / "remediations").mkdir(parents=True)
    (tmp_path / "output" / "logs").mkdir(parents=True)
    (tmp_path / "output" / "policies").mkdir(parents=True)

    # Create hook scripts
    pre_hook = tmp_path / "hooks" / "pre-remediation.sh"
    pre_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    pre_hook.chmod(0o755)

    post_hook = tmp_path / "hooks" / "post-remediation.sh"
    post_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    post_hook.chmod(0o755)

    return tmp_path


def _make_orchestrator(tmp_project):
    """Create an Orchestrator configured for timeout tests."""
    orch = Orchestrator(project_root=tmp_project, approver="test-user")
    return orch


def _inject_plan(orch, tmp_project, resource_id="vol-abc123"):
    """Inject a RemediationPlan into the orchestrator state store."""
    plan = RemediationPlan(
        resource_id=resource_id,
        finding={
            "resource_id": resource_id,
            "resource_type": "ebs",
            "category": "waste",
        },
        blocked=False,
        remediation_hcl='resource "null_resource" "test" {}',
        rollback_hcl='resource "null_resource" "rollback" {}',
    )
    # Store in state store so _find_plan() can locate it
    orch._state_store.replace_plans([plan], run_id="test-run")

    # Write required output files
    (tmp_project / "output" / "remediation.tf").write_text(
        'resource "null_resource" "test" {}'
    )
    (tmp_project / "output" / "rollbacks" / f"{resource_id}.tf").write_text(
        'resource "null_resource" "rollback" {}'
    )
    return plan


def _timeout_expired(cmd="terraform", timeout=120):
    """Create a subprocess.TimeoutExpired exception."""
    return subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)


# ──────────────────────────────────────────────────────────────────────
# approve() — TimeoutExpired at each subprocess step
# ──────────────────────────────────────────────────────────────────────


class TestApproveTimeoutInit:
    """TimeoutExpired during terraform init in approve()."""

    @patch.dict("os.environ", {"JANITOR_DRY_RUN": "0", "TF_CMD": "tflocal"}, clear=False)
    def test_init_timeout_returns_failure(self, tmp_project):
        """Timeout during init returns failure result, no exception propagates."""
        orch = _make_orchestrator(tmp_project)
        _inject_plan(orch, tmp_project)

        def _side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # Pre-remediation hook passes
            if any("pre-remediation" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # Terraform init times out
            raise _timeout_expired(cmd="terraform init", timeout=120)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=_side_effect):
            result = orch.approve("APPROVE vol-abc123")

        assert result.success is False
        assert "timed out" in result.error.lower()
        assert isinstance(result, ApprovalResult)


class TestApproveTimeoutApply:
    """TimeoutExpired during terraform apply in approve()."""

    @patch.dict("os.environ", {"JANITOR_DRY_RUN": "0", "TF_CMD": "tflocal"}, clear=False)
    def test_apply_timeout_returns_failure(self, tmp_project):
        """Timeout during apply returns failure result."""
        orch = _make_orchestrator(tmp_project)
        _inject_plan(orch, tmp_project)

        call_count = {"n": 0}

        def _side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            call_count["n"] += 1
            # Pre-remediation hook passes (call 1)
            if any("pre-remediation" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # init passes (call 2)
            if any("init" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # plan passes (call 3)
            if any("plan" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # show passes (call 4) - return empty JSON to skip scope check
            if any("show" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")
            # apply times out (call 5)
            raise _timeout_expired(cmd="terraform apply", timeout=120)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=_side_effect):
            result = orch.approve("APPROVE vol-abc123")

        assert result.success is False
        assert "timed out" in result.error.lower()
        assert isinstance(result, ApprovalResult)


class TestApproveTimeoutPlan:
    """TimeoutExpired during terraform plan in approve()."""

    @patch.dict("os.environ", {"JANITOR_DRY_RUN": "0", "TF_CMD": "tflocal"}, clear=False)
    def test_plan_timeout_returns_failure(self, tmp_project):
        """Timeout during plan returns failure result."""
        orch = _make_orchestrator(tmp_project)
        _inject_plan(orch, tmp_project)

        def _side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # Pre-remediation hook passes
            if any("pre-remediation" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # init passes
            if any("init" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # plan times out
            raise _timeout_expired(cmd="terraform plan", timeout=120)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=_side_effect):
            result = orch.approve("APPROVE vol-abc123")

        assert result.success is False
        assert "timed out" in result.error.lower()


# ──────────────────────────────────────────────────────────────────────
# _handle_confirm_rollback() — TimeoutExpired at each subprocess step
# ──────────────────────────────────────────────────────────────────────


class TestRollbackTimeoutInit:
    """TimeoutExpired during terraform init in _handle_confirm_rollback()."""

    def test_rollback_init_timeout_returns_failure(self, tmp_project):
        """Timeout during rollback init returns failure, no exception propagates."""
        orch = _make_orchestrator(tmp_project)
        _inject_plan(orch, tmp_project)

        # First, initiate rollback to set pending state
        r1 = orch.rollback("ROLLBACK vol-abc123")
        assert r1.needs_confirmation is True

        def _side_effect(*args, **kwargs):
            args[0] if args else kwargs.get("args", [])
            # Terraform init times out
            raise _timeout_expired(cmd="terraform init", timeout=120)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=_side_effect):
            result = orch.rollback("CONFIRM ROLLBACK vol-abc123")

        assert result.success is False
        assert "timed out" in result.error.lower()
        assert isinstance(result, RollbackResult)


class TestRollbackTimeoutPlan:
    """TimeoutExpired during terraform plan in _handle_confirm_rollback()."""

    def test_rollback_plan_timeout_returns_failure(self, tmp_project):
        """Timeout during rollback plan returns failure."""
        orch = _make_orchestrator(tmp_project)
        _inject_plan(orch, tmp_project)

        r1 = orch.rollback("ROLLBACK vol-abc123")
        assert r1.needs_confirmation is True

        def _side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # init passes
            if any("init" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # plan times out
            raise _timeout_expired(cmd="terraform plan", timeout=120)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=_side_effect):
            result = orch.rollback("CONFIRM ROLLBACK vol-abc123")

        assert result.success is False
        assert "timed out" in result.error.lower()
        assert isinstance(result, RollbackResult)


class TestRollbackTimeoutShow:
    """TimeoutExpired during terraform show in _handle_confirm_rollback()."""

    def test_rollback_show_timeout_returns_failure(self, tmp_project):
        """Timeout during rollback show returns failure."""
        orch = _make_orchestrator(tmp_project)
        _inject_plan(orch, tmp_project)

        r1 = orch.rollback("ROLLBACK vol-abc123")
        assert r1.needs_confirmation is True

        def _side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # init passes
            if any("init" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # plan passes
            if any("plan" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # show times out
            raise _timeout_expired(cmd="terraform show", timeout=120)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=_side_effect):
            result = orch.rollback("CONFIRM ROLLBACK vol-abc123")

        assert result.success is False
        assert "timed out" in result.error.lower()
        assert isinstance(result, RollbackResult)


class TestRollbackTimeoutApply:
    """TimeoutExpired during terraform apply in _handle_confirm_rollback()."""

    def test_rollback_apply_timeout_returns_failure(self, tmp_project):
        """Timeout during rollback apply returns failure."""
        orch = _make_orchestrator(tmp_project)
        _inject_plan(orch, tmp_project)

        r1 = orch.rollback("ROLLBACK vol-abc123")
        assert r1.needs_confirmation is True

        def _side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # init passes
            if any("init" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # plan passes
            if any("plan" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # show passes (empty JSON to skip scope check)
            if any("show" in str(c) for c in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")
            # apply times out
            raise _timeout_expired(cmd="terraform apply", timeout=120)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=_side_effect):
            result = orch.rollback("CONFIRM ROLLBACK vol-abc123")

        assert result.success is False
        assert "timed out" in result.error.lower()
        assert isinstance(result, RollbackResult)


# ──────────────────────────────────────────────────────────────────────
# Pre-remediation hook timeout (already handled — verify contract)
# ──────────────────────────────────────────────────────────────────────


class TestPreRemediationHookTimeout:
    """TimeoutExpired during pre-remediation hook returns hook error string."""

    @patch.dict("os.environ", {"JANITOR_DRY_RUN": "0", "TF_CMD": "tflocal"}, clear=False)
    def test_pre_hook_timeout_blocks_approval(self, tmp_project):
        """Pre-remediation hook timeout prevents approval from proceeding."""
        orch = _make_orchestrator(tmp_project)
        _inject_plan(orch, tmp_project)

        def _side_effect(*args, **kwargs):
            # All subprocess calls time out (hook is the first call)
            raise _timeout_expired(cmd="bash pre-remediation.sh", timeout=180)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=_side_effect):
            result = orch.approve("APPROVE vol-abc123")

        # Approval fails because pre-hook timed out
        assert result.success is False
        assert "timed out" in result.error.lower()


# ──────────────────────────────────────────────────────────────────────
# Post-remediation hook timeout (non-blocking — already handled)
# ──────────────────────────────────────────────────────────────────────


class TestPostRemediationHookTimeout:
    """TimeoutExpired during post-remediation hook is non-blocking."""

    @patch.dict("os.environ", {"JANITOR_DRY_RUN": "1", "TF_CMD": "tflocal"}, clear=False)
    def test_post_hook_timeout_does_not_fail_approval(self, tmp_project):
        """Post-remediation hook timeout doesn't cause approval to fail (dry-run mode)."""
        orch = _make_orchestrator(tmp_project)
        _inject_plan(orch, tmp_project)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run") as mock_run:
            # Post-hook is the only subprocess call in dry-run mode — make it timeout
            mock_run.side_effect = _timeout_expired(cmd="bash post-remediation.sh", timeout=30)
            result = orch.approve("APPROVE vol-abc123")

        # Approval still succeeds (post-hook is non-blocking)
        assert result.success is True
