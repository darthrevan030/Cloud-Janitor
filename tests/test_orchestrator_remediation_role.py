"""Unit tests for Orchestrator remediation-role wiring.

Validates that _terraform_env_for_apply() is correctly integrated into
approve() and _handle_confirm_rollback(), ensuring:
- Assume-role failure blocks all terraform subprocess calls
- Assume-role success injects temporary credentials into subprocess env
- The same credential set is used for init and apply within one invocation
- Unset role ARN falls back to ambient credentials without calling STS
- Hook subprocess env is unaffected by remediation-role state
- No credential caching between sequential approve() calls

Validates: Requirements 2.2, 2.3, 2.4, 2.5, 2.6
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from cloud_janitor.agents.remediation_architect import RemediationPlan
from cloud_janitor.orchestrator.orchestrator import (
    Orchestrator,
    _build_subprocess_env,
    RemediationRoleAssumptionError,
)


# ─── Constants ──────────────────────────────────────────────────────────

FAKE_ROLE_ARN = "arn:aws:iam::123456789012:role/janitor-remediation-role"
FAKE_CREDS = {
    "AWS_ACCESS_KEY_ID": "ASIAFAKETEMPCRED001",
    "AWS_SECRET_ACCESS_KEY": "fakesecret+temporary/key001",
    "AWS_SESSION_TOKEN": "FakeSessionToken001==",
}
FAKE_CREDS_2 = {
    "AWS_ACCESS_KEY_ID": "ASIAFAKETEMPCRED002",
    "AWS_SECRET_ACCESS_KEY": "fakesecret+temporary/key002",
    "AWS_SESSION_TOKEN": "FakeSessionToken002==",
}
RESOURCE_ID = "vol-abc123"


# ─── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mock_health_check():
    """Bypass backend health preflight for all tests in this module."""
    from cloud_janitor.core.health import HealthStatus

    healthy = HealthStatus(
        reachable=True, environment="sandbox_localstack", mode="healthy",
        detail="ok", endpoint="mock",
    )
    with patch(
        "cloud_janitor.orchestrator.orchestrator.check_backend_health",
        return_value=healthy,
    ):
        yield


@pytest.fixture(autouse=True)
def _reset_warning_latch():
    """Reset the module-level remediation-role warning latch between tests."""
    with patch(
        "cloud_janitor.orchestrator.orchestrator._REMEDIATION_ROLE_WARNED", False
    ):
        yield


@pytest.fixture
def tmp_project(tmp_path):
    """Create the directory tree the Orchestrator constructor requires."""
    (tmp_path / "output" / "remediations").mkdir(parents=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True)
    (tmp_path / "output" / "logs").mkdir(parents=True)
    (tmp_path / "output" / "policies").mkdir(parents=True)
    (tmp_path / "hooks").mkdir(parents=True)

    # Create hook scripts so hooks can execute
    pre_hook = tmp_path / "hooks" / "pre-remediation.sh"
    pre_hook.write_text("#!/usr/bin/env bash\nexit 0\n")

    post_hook = tmp_path / "hooks" / "post-remediation.sh"
    post_hook.write_text("#!/usr/bin/env bash\nexit 0\n")

    return tmp_path


@pytest.fixture
def orch(tmp_project):
    """Create an Orchestrator with explicit approver (bypasses STS)."""
    return Orchestrator(project_root=tmp_project, approver="test-user")


def _inject_plan(orch, resource_id=RESOURCE_ID):
    """Inject a RemediationPlan into the StateStore so _find_plan succeeds."""
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
    # Persist plan via StateStore so _find_plan() retrieves it
    orch._state_store.replace_plans([plan], "test-run-001")
    return plan


def _setup_rollback_pending(orch, tmp_project, resource_id=RESOURCE_ID):
    """Set up state for a CONFIRM ROLLBACK test (pending rollback + artifact)."""
    _inject_plan(orch, resource_id)
    orch._state_store.add_pending_rollback(resource_id)
    rollback_path = tmp_project / "output" / "rollbacks" / f"{resource_id}.tf"
    rollback_path.write_text('resource "null_resource" "rollback" {}')


def _terraform_subprocess_calls(mock_run):
    """Filter subprocess.run calls that contain terraform commands (init/plan/show/apply)."""
    tf_calls = []
    for c in mock_run.call_args_list:
        args = c[0][0] if c[0] else c[1].get("args", [])
        if isinstance(args, (list, tuple)) and len(args) >= 2:
            cmd_parts = [str(a) for a in args]
            if any(sub in cmd_parts for sub in ["init", "plan", "show", "apply"]):
                tf_calls.append(c)
    return tf_calls


def _hook_subprocess_calls(mock_run):
    """Filter subprocess.run calls that are hook invocations (contain .sh paths)."""
    hook_calls = []
    for c in mock_run.call_args_list:
        args = c[0][0] if c[0] else c[1].get("args", [])
        if isinstance(args, (list, tuple)):
            cmd_str = " ".join(str(a) for a in args)
            if ".sh" in cmd_str or "bash" in cmd_str.lower():
                hook_calls.append(c)
    return hook_calls


# ─── Test: Assume-role failure blocks terraform ─────────────────────────


class TestAssumeRoleFailureBlocksTerraform:
    """When JANITOR_REMEDIATION_ROLE_ARN is set but assume-role fails,
    approve() and _handle_confirm_rollback() return success=False
    and zero terraform subprocess calls are made."""

    @patch("subprocess.run")
    @patch(
        "cloud_janitor.orchestrator.orchestrator._assume_remediation_role",
        side_effect=RemediationRoleAssumptionError("Access denied"),
    )
    @patch.dict(os.environ, {"JANITOR_REMEDIATION_ROLE_ARN": FAKE_ROLE_ARN}, clear=False)
    def test_approve_blocked_on_assume_failure(
        self, mock_assume, mock_run, orch, tmp_project
    ):
        """approve() returns success=False, zero terraform subprocess calls."""
        _inject_plan(orch)
        # Write remediation HCL so _build_apply_dir doesn't fail
        (tmp_project / "output" / "remediations" / f"{RESOURCE_ID}.tf").write_text(
            'resource "null_resource" "test" {}'
        )

        result = orch.approve(f"APPROVE {RESOURCE_ID}")

        assert result.success is False
        assert "role" in result.error.lower() or "assumption" in result.error.lower()
        # No terraform commands should have been issued
        tf_calls = _terraform_subprocess_calls(mock_run)
        assert len(tf_calls) == 0

    @patch("subprocess.run")
    @patch(
        "cloud_janitor.orchestrator.orchestrator._assume_remediation_role",
        side_effect=RemediationRoleAssumptionError("Access denied"),
    )
    @patch.dict(os.environ, {"JANITOR_REMEDIATION_ROLE_ARN": FAKE_ROLE_ARN}, clear=False)
    def test_rollback_blocked_on_assume_failure(
        self, mock_assume, mock_run, orch, tmp_project
    ):
        """_handle_confirm_rollback() returns success=False, zero terraform subprocess calls."""
        _setup_rollback_pending(orch, tmp_project)

        result = orch.rollback(f"CONFIRM ROLLBACK {RESOURCE_ID}")

        assert result.success is False
        assert "role" in result.error.lower() or "assumption" in result.error.lower()
        tf_calls = _terraform_subprocess_calls(mock_run)
        assert len(tf_calls) == 0


# ─── Test: Assume-role success injects credentials ──────────────────────


class TestAssumeRoleSuccessInjectsCredentials:
    """When assume-role succeeds, the temporary credentials appear in
    the env= kwarg of terraform subprocess.run calls, not ambient creds."""

    @patch("subprocess.run")
    @patch(
        "cloud_janitor.orchestrator.orchestrator._assume_remediation_role",
        return_value=FAKE_CREDS,
    )
    @patch.dict(
        os.environ,
        {
            "JANITOR_REMEDIATION_ROLE_ARN": FAKE_ROLE_ARN,
            "AWS_ACCESS_KEY_ID": "AMBIENT_KEY_SHOULD_NOT_APPEAR",
            "AWS_SECRET_ACCESS_KEY": "AMBIENT_SECRET_SHOULD_NOT_APPEAR",
        },
        clear=False,
    )
    def test_approve_passes_temp_creds_to_terraform(
        self, mock_assume, mock_run, orch, tmp_project
    ):
        """Terraform subprocess calls receive the assumed-role credentials."""
        _inject_plan(orch)
        (tmp_project / "output" / "remediations" / f"{RESOURCE_ID}.tf").write_text(
            'resource "null_resource" "test" {}'
        )
        # Return empty stdout so "show -json" produces invalid JSON → scope check skipped
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )

        orch.approve(f"APPROVE {RESOURCE_ID}")

        tf_calls = _terraform_subprocess_calls(mock_run)
        assert len(tf_calls) > 0, "Expected at least one terraform subprocess call"

        for tc in tf_calls:
            env = tc[1].get("env") if len(tc) > 1 else tc.kwargs.get("env")
            if env is None:
                # Try kwargs
                env = tc.kwargs.get("env")
            assert env is not None, "Terraform call missing env= kwarg"
            assert env["AWS_ACCESS_KEY_ID"] == FAKE_CREDS["AWS_ACCESS_KEY_ID"]
            assert env["AWS_SECRET_ACCESS_KEY"] == FAKE_CREDS["AWS_SECRET_ACCESS_KEY"]
            assert env["AWS_SESSION_TOKEN"] == FAKE_CREDS["AWS_SESSION_TOKEN"]
            # Ambient credentials must NOT be present
            assert env["AWS_ACCESS_KEY_ID"] != "AMBIENT_KEY_SHOULD_NOT_APPEAR"

    @patch("subprocess.run")
    @patch(
        "cloud_janitor.orchestrator.orchestrator._assume_remediation_role",
        return_value=FAKE_CREDS,
    )
    @patch.dict(
        os.environ,
        {
            "JANITOR_REMEDIATION_ROLE_ARN": FAKE_ROLE_ARN,
            "AWS_ACCESS_KEY_ID": "AMBIENT_KEY_SHOULD_NOT_APPEAR",
            "AWS_SECRET_ACCESS_KEY": "AMBIENT_SECRET_SHOULD_NOT_APPEAR",
        },
        clear=False,
    )
    def test_rollback_passes_temp_creds_to_terraform(
        self, mock_assume, mock_run, orch, tmp_project
    ):
        """CONFIRM ROLLBACK terraform calls receive the assumed-role credentials."""
        _setup_rollback_pending(orch, tmp_project)
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )

        orch.rollback(f"CONFIRM ROLLBACK {RESOURCE_ID}")

        tf_calls = _terraform_subprocess_calls(mock_run)
        assert len(tf_calls) > 0, "Expected at least one terraform subprocess call"

        for tc in tf_calls:
            env = tc.kwargs.get("env")
            assert env is not None, "Terraform call missing env= kwarg"
            assert env["AWS_ACCESS_KEY_ID"] == FAKE_CREDS["AWS_ACCESS_KEY_ID"]
            assert env["AWS_SECRET_ACCESS_KEY"] == FAKE_CREDS["AWS_SECRET_ACCESS_KEY"]
            assert env["AWS_SESSION_TOKEN"] == FAKE_CREDS["AWS_SESSION_TOKEN"]
            assert env["AWS_ACCESS_KEY_ID"] != "AMBIENT_KEY_SHOULD_NOT_APPEAR"


# ─── Test: Same credential set for init and apply ───────────────────────


class TestSameCredentialForInitAndApply:
    """The env= kwarg from the init subprocess call and the apply subprocess
    call must contain the SAME credential set within one invocation."""

    @patch("subprocess.run")
    @patch(
        "cloud_janitor.orchestrator.orchestrator._assume_remediation_role",
        return_value=FAKE_CREDS,
    )
    @patch.dict(
        os.environ,
        {"JANITOR_REMEDIATION_ROLE_ARN": FAKE_ROLE_ARN},
        clear=False,
    )
    def test_approve_same_creds_init_and_apply(
        self, mock_assume, mock_run, orch, tmp_project
    ):
        """approve(): init and apply get identical AWS_* credentials."""
        _inject_plan(orch)
        (tmp_project / "output" / "remediations" / f"{RESOURCE_ID}.tf").write_text(
            'resource "null_resource" "test" {}'
        )
        # Return empty stdout for "show" so scope check is skipped (invalid JSON → plan_json=None)
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )

        orch.approve(f"APPROVE {RESOURCE_ID}")

        tf_calls = _terraform_subprocess_calls(mock_run)
        # Find init and apply calls
        init_envs = []
        apply_envs = []
        for tc in tf_calls:
            args = tc[0][0] if tc[0] else tc.kwargs.get("args", [])
            cmd_parts = [str(a) for a in args]
            env = tc.kwargs.get("env")
            if "init" in cmd_parts:
                init_envs.append(env)
            elif "apply" in cmd_parts:
                apply_envs.append(env)

        assert len(init_envs) >= 1, "Expected at least one init call"
        assert len(apply_envs) >= 1, "Expected at least one apply call"

        # All init and apply envs must have the same AWS_* credential triple
        for init_env in init_envs:
            for apply_env in apply_envs:
                assert init_env["AWS_ACCESS_KEY_ID"] == apply_env["AWS_ACCESS_KEY_ID"]
                assert init_env["AWS_SECRET_ACCESS_KEY"] == apply_env["AWS_SECRET_ACCESS_KEY"]
                assert init_env["AWS_SESSION_TOKEN"] == apply_env["AWS_SESSION_TOKEN"]

    @patch("subprocess.run")
    @patch(
        "cloud_janitor.orchestrator.orchestrator._assume_remediation_role",
        return_value=FAKE_CREDS,
    )
    @patch.dict(
        os.environ,
        {"JANITOR_REMEDIATION_ROLE_ARN": FAKE_ROLE_ARN},
        clear=False,
    )
    def test_rollback_same_creds_init_and_apply(
        self, mock_assume, mock_run, orch, tmp_project
    ):
        """_handle_confirm_rollback(): init and apply get identical AWS_* credentials."""
        _setup_rollback_pending(orch, tmp_project)
        # Return empty stdout for "show" so scope check is skipped (invalid JSON → plan_json=None)
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )

        orch.rollback(f"CONFIRM ROLLBACK {RESOURCE_ID}")

        tf_calls = _terraform_subprocess_calls(mock_run)
        init_envs = []
        apply_envs = []
        for tc in tf_calls:
            args = tc[0][0] if tc[0] else tc.kwargs.get("args", [])
            cmd_parts = [str(a) for a in args]
            env = tc.kwargs.get("env")
            if "init" in cmd_parts:
                init_envs.append(env)
            elif "apply" in cmd_parts:
                apply_envs.append(env)

        assert len(init_envs) >= 1, "Expected at least one init call"
        assert len(apply_envs) >= 1, "Expected at least one apply call"

        for init_env in init_envs:
            for apply_env in apply_envs:
                assert init_env["AWS_ACCESS_KEY_ID"] == apply_env["AWS_ACCESS_KEY_ID"]
                assert init_env["AWS_SECRET_ACCESS_KEY"] == apply_env["AWS_SECRET_ACCESS_KEY"]
                assert init_env["AWS_SESSION_TOKEN"] == apply_env["AWS_SESSION_TOKEN"]


# ─── Test: Unset role ARN — fallback to ambient ─────────────────────────


class TestUnsetRoleFallback:
    """When JANITOR_REMEDIATION_ROLE_ARN is not set, sts.assume_role is
    never called and the terraform env matches _build_subprocess_env("terraform")."""

    @patch("subprocess.run")
    @patch("cloud_janitor.mcp_server.backends.aws_provider._make_client")
    @patch.dict(os.environ, {}, clear=False)
    def test_approve_no_role_uses_ambient(
        self, mock_make_client, mock_run, orch, tmp_project
    ):
        """approve() without JANITOR_REMEDIATION_ROLE_ARN never calls STS."""
        os.environ.pop("JANITOR_REMEDIATION_ROLE_ARN", None)
        _inject_plan(orch)
        (tmp_project / "output" / "remediations" / f"{RESOURCE_ID}.tf").write_text(
            'resource "null_resource" "test" {}'
        )
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )

        orch.approve(f"APPROVE {RESOURCE_ID}")

        # _make_client should not have been called for STS assume-role
        mock_make_client.assert_not_called()

        # Terraform env should match _build_subprocess_env("terraform") exactly
        expected_env = _build_subprocess_env("terraform")
        tf_calls = _terraform_subprocess_calls(mock_run)
        assert len(tf_calls) > 0
        for tc in tf_calls:
            env = tc.kwargs.get("env")
            assert env is not None
            # All keys in expected_env should match
            for key, val in expected_env.items():
                assert env.get(key) == val, f"Mismatch on {key}: {env.get(key)} != {val}"
            # No extra AWS_SESSION_TOKEN if not in ambient
            if "AWS_SESSION_TOKEN" not in expected_env:
                assert "AWS_SESSION_TOKEN" not in env

    @patch("subprocess.run")
    @patch("cloud_janitor.mcp_server.backends.aws_provider._make_client")
    @patch.dict(os.environ, {}, clear=False)
    def test_rollback_no_role_uses_ambient(
        self, mock_make_client, mock_run, orch, tmp_project
    ):
        """CONFIRM ROLLBACK without JANITOR_REMEDIATION_ROLE_ARN never calls STS."""
        os.environ.pop("JANITOR_REMEDIATION_ROLE_ARN", None)
        _setup_rollback_pending(orch, tmp_project)
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )

        orch.rollback(f"CONFIRM ROLLBACK {RESOURCE_ID}")

        mock_make_client.assert_not_called()

        expected_env = _build_subprocess_env("terraform")
        tf_calls = _terraform_subprocess_calls(mock_run)
        assert len(tf_calls) > 0
        for tc in tf_calls:
            env = tc.kwargs.get("env")
            assert env is not None
            for key, val in expected_env.items():
                assert env.get(key) == val


# ─── Test: Hook env unaffected by remediation-role state ────────────────


class TestHookEnvUnaffected:
    """Hook subprocess calls (pre/post-remediation) use _build_subprocess_env("hook"),
    which is unaffected regardless of whether the Remediation_Role is configured."""

    @patch("subprocess.run")
    @patch(
        "cloud_janitor.orchestrator.orchestrator._assume_remediation_role",
        return_value=FAKE_CREDS,
    )
    @patch.dict(
        os.environ,
        {"JANITOR_REMEDIATION_ROLE_ARN": FAKE_ROLE_ARN},
        clear=False,
    )
    def test_hooks_use_hook_env_not_terraform_env(
        self, mock_assume, mock_run, orch, tmp_project
    ):
        """With role configured, hook calls still use _build_subprocess_env('hook')."""
        _inject_plan(orch)
        (tmp_project / "output" / "remediations" / f"{RESOURCE_ID}.tf").write_text(
            'resource "null_resource" "test" {}'
        )
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )

        orch.approve(f"APPROVE {RESOURCE_ID}")

        hook_calls = _hook_subprocess_calls(mock_run)
        _build_subprocess_env("hook")

        for hc in hook_calls:
            env = hc.kwargs.get("env")
            if env is not None:
                # Hook env should NOT contain the temporary remediation-role credentials
                # (unless they happen to match ambient, which they don't in this test)
                if "AWS_ACCESS_KEY_ID" in env:
                    assert env["AWS_ACCESS_KEY_ID"] != FAKE_CREDS["AWS_ACCESS_KEY_ID"], (
                        "Hook subprocess received remediation-role credentials — should use ambient"
                    )

    @patch("subprocess.run")
    @patch(
        "cloud_janitor.orchestrator.orchestrator._assume_remediation_role",
        return_value=None,
    )
    @patch.dict(os.environ, {}, clear=False)
    def test_hooks_unaffected_when_role_unset(
        self, mock_assume, mock_run, orch, tmp_project
    ):
        """With role unset, hook calls still use _build_subprocess_env('hook')."""
        os.environ.pop("JANITOR_REMEDIATION_ROLE_ARN", None)
        _inject_plan(orch)
        (tmp_project / "output" / "remediations" / f"{RESOURCE_ID}.tf").write_text(
            'resource "null_resource" "test" {}'
        )
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )

        orch.approve(f"APPROVE {RESOURCE_ID}")

        hook_calls = _hook_subprocess_calls(mock_run)
        expected_hook_env = _build_subprocess_env("hook")

        for hc in hook_calls:
            env = hc.kwargs.get("env")
            if env is not None:
                # Hook env keys should match the expected hook env
                for key, val in expected_hook_env.items():
                    assert env.get(key) == val, (
                        f"Hook env mismatch on {key}: got {env.get(key)}, expected {val}"
                    )


# ─── Test: No credential caching between sequential approve() calls ────


class TestNoCachingBetweenCalls:
    """Two sequential approve() calls with the role configured each
    independently call _assume_remediation_role() — no credential reuse."""

    @patch("subprocess.run")
    @patch("cloud_janitor.orchestrator.orchestrator._assume_remediation_role")
    @patch.dict(
        os.environ,
        {"JANITOR_REMEDIATION_ROLE_ARN": FAKE_ROLE_ARN},
        clear=False,
    )
    def test_two_approve_calls_each_assume_independently(
        self, mock_assume, mock_run, orch, tmp_project
    ):
        """Each approve() call triggers its own _assume_remediation_role()."""
        # First call returns FAKE_CREDS, second returns FAKE_CREDS_2
        mock_assume.side_effect = [FAKE_CREDS, FAKE_CREDS_2]
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )

        # Set up two separate resources
        resource_id_1 = "vol-first001"
        resource_id_2 = "vol-second02"

        plan1 = RemediationPlan(
            resource_id=resource_id_1,
            finding={"resource_id": resource_id_1, "resource_type": "ebs", "category": "waste"},
            blocked=False,
            remediation_hcl='resource "null_resource" "test1" {}',
            rollback_hcl='resource "null_resource" "rollback1" {}',
        )
        plan2 = RemediationPlan(
            resource_id=resource_id_2,
            finding={"resource_id": resource_id_2, "resource_type": "ebs", "category": "waste"},
            blocked=False,
            remediation_hcl='resource "null_resource" "test2" {}',
            rollback_hcl='resource "null_resource" "rollback2" {}',
        )
        orch._state_store.replace_plans([plan1, plan2], "test-run-002")

        (tmp_project / "output" / "remediations" / f"{resource_id_1}.tf").write_text(
            'resource "null_resource" "test1" {}'
        )
        (tmp_project / "output" / "remediations" / f"{resource_id_2}.tf").write_text(
            'resource "null_resource" "test2" {}'
        )

        # First approve
        orch.approve(f"APPROVE {resource_id_1}")
        # Second approve
        orch.approve(f"APPROVE {resource_id_2}")

        # _assume_remediation_role must have been called twice (once per approve)
        assert mock_assume.call_count == 2
