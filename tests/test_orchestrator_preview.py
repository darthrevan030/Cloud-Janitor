"""Tests for Orchestrator.preview_plan() and cache-wired approve() integration.

Covers:
- preview_plan() → approve() within TTL = exactly 1 plan subprocess call
- approve() without preview = no regression (full inline path)
- Expired cache discarded + apply_dir removed
- _check_plan_scope stub when phase1 unavailable (NameError/TypeError)
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from cloud_janitor.agents.remediation_architect import RemediationPlan
from cloud_janitor.core.health import HealthStatus
from cloud_janitor.orchestrator import Orchestrator, PlanPreviewResult, PREVIEW_TTL_SECONDS
from cloud_janitor.orchestrator.orchestrator import _CachedPreview


@pytest.fixture(autouse=True)
def _mock_health_check():
    """All orchestrator tests bypass the backend health preflight."""
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

    pre_hook = tmp_path / "hooks" / "pre-remediation.sh"
    pre_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    pre_hook.chmod(0o755)

    post_hook = tmp_path / "hooks" / "post-remediation.sh"
    post_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    post_hook.chmod(0o755)

    return tmp_path


@pytest.fixture
def mock_plan():
    """A minimal RemediationPlan for testing."""
    return RemediationPlan(
        resource_id="vol-abc123",
        finding={
            "id": "f1",
            "resource_id": "vol-abc123",
            "resource_type": "ebs",
            "category": "waste",
            "severity": "MEDIUM",
            "title": "Unattached EBS volume",
        },
        remediation_hcl='resource "null_resource" "test" {}',
        rollback_hcl='resource "null_resource" "rollback" {}',
        blocked=False,
        block_reason="",
    )


@pytest.fixture
def orch(tmp_project, mock_plan):
    """Create an orchestrator with a plan injected into the state store."""
    with patch.dict("os.environ", {
        "TF_CMD": "terraform",
        "JANITOR_DRY_RUN": "0",
    }, clear=False):
        with patch("shutil.which", return_value="/usr/bin/terraform"):
            o = Orchestrator(project_root=tmp_project, approver="test-user")
            # Inject plan into state store
            o._state_store.replace_plans([mock_plan], "run-001")
            return o


def _make_subprocess_mock(plan_json: dict | None = None):
    """Create a subprocess.run mock that succeeds for init/plan/show/apply.

    Returns (mock, plan_json_used).
    """
    if plan_json is None:
        plan_json = {"resource_changes": []}

    def side_effect(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        if "show" in cmd and "-json" in cmd:
            result.stdout = json.dumps(plan_json)
        return result

    return side_effect, plan_json


class TestPreviewPlanBasic:
    """Basic preview_plan() functionality."""

    def test_preview_plan_success(self, orch):
        """preview_plan() returns success with a PlanPreview."""
        side_effect, _ = _make_subprocess_mock()
        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=side_effect):
            with patch("cloud_janitor.orchestrator.orchestrator._check_plan_scope", return_value=(True, "ok")):
                result = orch.preview_plan("vol-abc123")

        assert result.success is True
        assert result.resource_id == "vol-abc123"
        assert result.preview is not None
        assert result.scope_ok is True
        assert result.flow == "remediate"

    def test_preview_plan_no_plan_found(self, orch):
        """preview_plan() returns error when no plan exists for resource."""
        result = orch.preview_plan("nonexistent-resource")
        assert result.success is False
        assert "No plan found" in result.error

    def test_preview_plan_init_failure(self, orch):
        """preview_plan() returns error when terraform init fails."""
        def side_effect(cmd, **kwargs):
            result = MagicMock()
            if "init" in cmd:
                result.returncode = 1
                result.stdout = ""
                result.stderr = "init failed"
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=side_effect):
            result = orch.preview_plan("vol-abc123")

        assert result.success is False
        assert "init failed" in result.error

    def test_preview_plan_caches_result(self, orch):
        """preview_plan() stores a _CachedPreview in _plan_previews."""
        side_effect, _ = _make_subprocess_mock()
        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=side_effect):
            with patch("cloud_janitor.orchestrator.orchestrator._check_plan_scope", return_value=(True, "ok")):
                orch.preview_plan("vol-abc123")

        assert "vol-abc123" in orch._plan_previews
        cached = orch._plan_previews["vol-abc123"]
        assert cached.scope_ok is True
        assert cached.flow == "remediate"
        assert cached.apply_dir.exists()

        # Cleanup
        import shutil
        shutil.rmtree(cached.apply_dir, ignore_errors=True)


class TestPreviewThenApproveWithinTTL:
    """preview_plan() → approve() within 60s TTL = 1 plan call total."""

    def test_exactly_one_plan_call(self, orch):
        """After preview_plan, approve() should NOT run plan again."""
        plan_json = {"resource_changes": []}
        side_effect, _ = _make_subprocess_mock(plan_json)

        plan_calls = []

        def tracking_side_effect(cmd, **kwargs):
            if "plan" in cmd:
                plan_calls.append(cmd)
            return side_effect(cmd, **kwargs)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=tracking_side_effect):
            with patch("cloud_janitor.orchestrator.orchestrator._check_plan_scope", return_value=(True, "ok")):
                # Step 1: preview
                preview_result = orch.preview_plan("vol-abc123")
                assert preview_result.success is True
                plan_calls_after_preview = len(plan_calls)

                # Step 2: approve (should use cache — no additional plan call)
                approve_result = orch.approve("APPROVE vol-abc123", resource_id="vol-abc123")

        # Only 1 plan call total (from preview_plan, not from approve)
        assert len(plan_calls) == plan_calls_after_preview
        assert approve_result.success is True

    def test_approve_uses_cached_scope_result(self, orch):
        """approve() uses the cached scope_ok from preview, doesn't re-run scope check."""
        plan_json = {"resource_changes": []}
        side_effect, _ = _make_subprocess_mock(plan_json)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=side_effect):
            with patch(
                "cloud_janitor.orchestrator.orchestrator._check_plan_scope",
                return_value=(True, "ok"),
            ) as mock_scope:
                preview_result = orch.preview_plan("vol-abc123")
                assert preview_result.success is True
                scope_calls_after_preview = mock_scope.call_count

                approve_result = orch.approve("APPROVE vol-abc123", resource_id="vol-abc123")

                # Scope check not called again during approve
                assert mock_scope.call_count == scope_calls_after_preview
                assert approve_result.success is True


class TestApproveWithoutPreview:
    """approve() without a prior preview_plan() call — no regression."""

    def test_full_inline_path(self, orch):
        """approve() works without preview_plan — runs full init+plan+show+apply."""
        plan_json = {"resource_changes": []}
        side_effect, _ = _make_subprocess_mock(plan_json)

        subprocess_calls = []

        def tracking_side_effect(cmd, **kwargs):
            subprocess_calls.append(cmd)
            return side_effect(cmd, **kwargs)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=tracking_side_effect):
            with patch("cloud_janitor.orchestrator.orchestrator._check_plan_scope", return_value=(True, "ok")):
                result = orch.approve("APPROVE vol-abc123", resource_id="vol-abc123")

        assert result.success is True
        # Should have called: init, plan, show, apply (4 calls)
        all_cmds = " ".join(str(c) for c in subprocess_calls)
        assert "init" in all_cmds
        assert "plan" in all_cmds
        assert "apply" in all_cmds


class TestExpiredCacheDiscarded:
    """Expired cache entry (> 60s old) is discarded and apply_dir removed."""

    def test_expired_cache_cleanup(self, orch, tmp_path):
        """approve() discards expired cache and removes its apply_dir."""
        # Manually inject an expired cached preview
        expired_dir = tmp_path / "expired_apply"
        expired_dir.mkdir()
        (expired_dir / "main.tf").write_text("# expired")

        from cloud_janitor.core.plan_diff import PlanPreview
        orch._plan_previews["vol-abc123"] = _CachedPreview(
            apply_dir=expired_dir,
            plan_json={"resource_changes": []},
            plan_preview=PlanPreview(),
            scope_ok=True,
            scope_reason="ok",
            flow="remediate",
            created_at=time.monotonic() - (PREVIEW_TTL_SECONDS + 10),  # expired
        )

        plan_json = {"resource_changes": []}
        side_effect, _ = _make_subprocess_mock(plan_json)

        plan_calls = []

        def tracking_side_effect(cmd, **kwargs):
            if "plan" in cmd:
                plan_calls.append(cmd)
            return side_effect(cmd, **kwargs)

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=tracking_side_effect):
            with patch("cloud_janitor.orchestrator.orchestrator._check_plan_scope", return_value=(True, "ok")):
                result = orch.approve("APPROVE vol-abc123", resource_id="vol-abc123")

        assert result.success is True
        # Expired cache was discarded — full inline path ran (with plan call)
        assert len(plan_calls) >= 1
        # Expired apply_dir was cleaned up
        assert not expired_dir.exists()

    def test_evict_expired_previews(self, orch, tmp_path):
        """_evict_expired_previews() removes entries and their apply_dirs."""
        expired_dir = tmp_path / "old_apply"
        expired_dir.mkdir()

        from cloud_janitor.core.plan_diff import PlanPreview
        orch._plan_previews["vol-expired"] = _CachedPreview(
            apply_dir=expired_dir,
            plan_json={},
            plan_preview=PlanPreview(),
            scope_ok=True,
            scope_reason="ok",
            flow="remediate",
            created_at=time.monotonic() - (PREVIEW_TTL_SECONDS + 1),
        )

        orch._evict_expired_previews()

        assert "vol-expired" not in orch._plan_previews
        assert not expired_dir.exists()


class TestScopeCheckStubFallback:
    """preview_plan() with _check_plan_scope unavailable (NameError/TypeError)."""

    def test_name_error_stubs_scope_ok(self, orch):
        """When _check_plan_scope raises NameError, scope_ok is stubbed True."""
        side_effect, _ = _make_subprocess_mock()

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=side_effect):
            with patch(
                "cloud_janitor.orchestrator.orchestrator._check_plan_scope",
                side_effect=NameError("_check_plan_scope not defined"),
            ):
                result = orch.preview_plan("vol-abc123")

        assert result.success is True
        assert result.scope_ok is True
        assert "pending" in result.scope_reason

    def test_type_error_stubs_scope_ok(self, orch):
        """When _check_plan_scope raises TypeError (incompatible signature), scope_ok is stubbed True."""
        side_effect, _ = _make_subprocess_mock()

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=side_effect):
            with patch(
                "cloud_janitor.orchestrator.orchestrator._check_plan_scope",
                side_effect=TypeError("got an unexpected keyword argument 'flow'"),
            ):
                result = orch.preview_plan("vol-abc123")

        assert result.success is True
        assert result.scope_ok is True
        assert "pending" in result.scope_reason

    def test_scope_check_failure_blocks_approve_via_cache(self, orch):
        """When scope_ok=False in cache, approve() returns failure."""
        side_effect, _ = _make_subprocess_mock()

        with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=side_effect):
            with patch(
                "cloud_janitor.orchestrator.orchestrator._check_plan_scope",
                return_value=(False, "out of scope: unexpected resource"),
            ):
                preview_result = orch.preview_plan("vol-abc123")

        assert preview_result.success is True
        assert preview_result.scope_ok is False

        # Now approve should fail using cached scope result (no subprocess needed)
        approve_result = orch.approve("APPROVE vol-abc123", resource_id="vol-abc123")

        assert approve_result.success is False
        assert "Scope check failed" in approve_result.error
