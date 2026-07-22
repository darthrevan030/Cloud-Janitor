"""Property-based tests for preview cache reuse and expiry invariants.

Property 2: Preview Cache Reuse Invariant
  For any resource_id with a non-expired (< 60s) cached preview, calling
  approve(resource_id) results in zero additional terraform plan subprocess
  invocations beyond the one made by preview_plan().

Property 3: Preview Cache Expiry Invariant
  For any resource_id with a cached preview older than 60 seconds, calling
  approve(resource_id) discards the expired entry and runs init/plan/show
  inline, exactly as with no cache entry at all.
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

import pytest

from cloud_janitor.agents.remediation_architect import RemediationPlan
from cloud_janitor.core.health import HealthStatus
from cloud_janitor.orchestrator import Orchestrator, PlanPreviewResult, PREVIEW_TTL_SECONDS
from cloud_janitor.orchestrator.orchestrator import _CachedPreview
from cloud_janitor.core.plan_diff import PlanPreview


@pytest.fixture(autouse=True)
def _mock_health_check():
    """All orchestrator tests bypass the backend health preflight."""
    healthy = HealthStatus(
        reachable=True, environment="sandbox_localstack", mode="healthy", detail="ok", endpoint="mock"
    )
    with patch("cloud_janitor.orchestrator.orchestrator.check_backend_health", return_value=healthy):
        yield


def _make_orch(tmp_path, resource_id="vol-test"):
    """Create an Orchestrator with a plan injected."""
    (tmp_path / "hooks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "remediations").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "policies").mkdir(parents=True, exist_ok=True)

    pre_hook = tmp_path / "hooks" / "pre-remediation.sh"
    pre_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    pre_hook.chmod(0o755)
    post_hook = tmp_path / "hooks" / "post-remediation.sh"
    post_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    post_hook.chmod(0o755)

    with patch.dict("os.environ", {"TF_CMD": "terraform", "JANITOR_DRY_RUN": "0"}, clear=False):
        with patch("shutil.which", return_value="/usr/bin/terraform"):
            orch = Orchestrator(project_root=tmp_path, approver="test-user")

    plan = RemediationPlan(
        resource_id=resource_id,
        finding={
            "id": "f1",
            "resource_id": resource_id,
            "resource_type": "ebs",
            "category": "waste",
            "severity": "MEDIUM",
            "title": "Unattached EBS volume",
        },
        remediation_hcl='resource "null_resource" "test" {}',
        rollback_hcl='resource "null_resource" "rollback" {}',
    )
    orch._state_store.replace_plans([plan], "run-001")
    return orch


def _subprocess_mock_ok():
    """Create a subprocess.run mock that always succeeds."""
    def side_effect(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = json.dumps({"resource_changes": []})
        result.stderr = ""
        return result
    return side_effect


# Strategy for TTL offsets: generate ages within TTL (fresh) and beyond TTL (expired)
_fresh_age = st.floats(min_value=0.0, max_value=PREVIEW_TTL_SECONDS - 1.0)
_expired_age = st.floats(min_value=PREVIEW_TTL_SECONDS + 0.1, max_value=PREVIEW_TTL_SECONDS * 5)


@given(age=_fresh_age)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property2_cache_reuse_no_extra_plan_calls(age, tmp_path):
    """Property 2: fresh cached preview → approve() makes zero plan calls."""
    orch = _make_orch(tmp_path)

    # Inject a fresh cached preview manually
    apply_dir = tmp_path / "apply_fresh"
    apply_dir.mkdir(exist_ok=True)
    (apply_dir / "main.tf").write_text("# test")

    orch._plan_previews["vol-test"] = _CachedPreview(
        apply_dir=apply_dir,
        plan_json={"resource_changes": []},
        plan_preview=PlanPreview(),
        scope_ok=True,
        scope_reason="ok",
        flow="remediate",
        created_at=time.monotonic() - age,  # within TTL
    )

    plan_calls = []

    def tracking_side_effect(cmd, **kwargs):
        if "plan" in cmd:
            plan_calls.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=tracking_side_effect):
        result = orch.approve("APPROVE vol-test", resource_id="vol-test")

    # Zero plan calls — cache was reused
    assert len(plan_calls) == 0, f"Expected 0 plan calls with age={age}s, got {len(plan_calls)}"
    assert result.success is True


@given(age=_expired_age)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property3_expired_cache_discarded_and_inline_runs(age, tmp_path):
    """Property 3: expired cached preview → approve() discards cache, runs inline path."""
    orch = _make_orch(tmp_path)

    # Inject an expired cached preview
    apply_dir = tmp_path / "apply_expired"
    apply_dir.mkdir(exist_ok=True)
    (apply_dir / "main.tf").write_text("# expired")

    orch._plan_previews["vol-test"] = _CachedPreview(
        apply_dir=apply_dir,
        plan_json={"resource_changes": []},
        plan_preview=PlanPreview(),
        scope_ok=True,
        scope_reason="ok",
        flow="remediate",
        created_at=time.monotonic() - age,  # beyond TTL
    )

    plan_calls = []
    init_calls = []

    def tracking_side_effect(cmd, **kwargs):
        if "plan" in cmd:
            plan_calls.append(cmd)
        if "init" in cmd:
            init_calls.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stdout = json.dumps({"resource_changes": []})
        result.stderr = ""
        return result

    with patch("cloud_janitor.orchestrator.orchestrator.subprocess.run", side_effect=tracking_side_effect):
        with patch("cloud_janitor.orchestrator.orchestrator._check_plan_scope", return_value=(True, "ok")):
            result = orch.approve("APPROVE vol-test", resource_id="vol-test")

    # Inline path ran (init + plan calls)
    assert len(init_calls) >= 1, f"Expected init call with age={age}s, got {len(init_calls)}"
    assert len(plan_calls) >= 1, f"Expected plan call with age={age}s, got {len(plan_calls)}"
    assert result.success is True
    # Expired apply_dir was removed
    assert not apply_dir.exists(), "Expired apply_dir should have been removed"
