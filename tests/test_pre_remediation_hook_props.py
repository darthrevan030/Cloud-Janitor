"""Property-based tests for pre-remediation hook full validation.

**Validates: Requirements 5.1, 5.2, 5.3, 5.4**

Property 6: Pre-Remediation Hook Coverage

For any list of active remediation plans, the Pre_Remediation_Hook SHALL validate
the rollback file for every plan in the list. If any plan's rollback file is missing,
empty, or fails the hook script, the entire remediation run SHALL be blocked and
the error SHALL list every failing resource_id.

Property 7: Pre-Remediation Hook Success

For any list of active plans where every plan has a corresponding non-empty rollback
file that passes the hook script (exit code 0), the Pre_Remediation_Hook SHALL return
a result with validated_paths containing one path per plan, and an empty failures list.
"""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from agents.remediation_architect import RemediationPlan
from orchestrator import Orchestrator


# --- Strategies ---

# Filesystem-safe resource IDs (alphanumeric + hyphens + underscores, 1-30 chars)
_fs_safe_chars = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)
fs_safe_resource_ids = st.text(_fs_safe_chars, min_size=1, max_size=30)

# Non-zero exit codes for hook failure simulation
nonzero_exit_codes = st.integers(min_value=1, max_value=255)

# Non-empty HCL content (simulates valid rollback file content)
rollback_hcl_content = st.text(
    st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=200,
).map(lambda s: s.strip() or "x")  # Ensure non-empty after strip


# Strategy for lists of unique resource IDs (unique to avoid path collisions)
def unique_resource_id_lists(min_size=1, max_size=5):
    """Generate lists of unique filesystem-safe resource IDs."""
    return st.lists(
        fs_safe_resource_ids,
        min_size=min_size,
        max_size=max_size,
        unique=True,
    )


# Failure mode: which type of failure a plan will have
failure_modes = st.sampled_from(["missing_file", "empty_file", "hook_nonzero"])


# --- Helpers ---


def _make_project_dirs(tmp_path: Path) -> Path:
    """Create the minimal project structure needed for Orchestrator init."""
    (tmp_path / "hooks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "policies").mkdir(parents=True, exist_ok=True)

    pre_hook = tmp_path / "hooks" / "pre-remediation.sh"
    pre_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    post_hook = tmp_path / "hooks" / "post-remediation.sh"
    post_hook.write_text("#!/usr/bin/env bash\nexit 0\n")

    return tmp_path


def _make_plan(resource_id: str) -> RemediationPlan:
    """Create a RemediationPlan for a given resource_id."""
    return RemediationPlan(
        resource_id=resource_id,
        finding={"resource_id": resource_id, "resource_type": "ebs"},
        blocked=False,
        remediation_hcl='resource "null_resource" "remediate" {}',
        rollback_hcl='resource "null_resource" "rollback" {}',
    )


def _create_rollback_file(rollbacks_dir: Path, resource_id: str, content: str = "resource {}") -> Path:
    """Create a non-empty rollback file for the given resource_id."""
    rollback_file = rollbacks_dir / f"{resource_id}.tf"
    rollback_file.write_text(content)
    return rollback_file


def _create_empty_rollback_file(rollbacks_dir: Path, resource_id: str) -> Path:
    """Create an empty (0-byte) rollback file for the given resource_id."""
    rollback_file = rollbacks_dir / f"{resource_id}.tf"
    rollback_file.write_text("")
    return rollback_file


# --- Property Tests ---


class TestProperty6PreRemediationHookCoverage:
    """Property 6: Pre-Remediation Hook Coverage.

    For any list of active remediation plans, the Pre_Remediation_Hook SHALL
    validate the rollback file for every plan in the list. If any plan's rollback
    file is missing, empty, or fails the hook script, the entire remediation run
    SHALL be blocked and the error SHALL list every failing resource_id.
    """

    @given(
        all_ids=unique_resource_id_lists(min_size=2, max_size=6),
        data=st.data(),
    )
    @settings(
        max_examples=50,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_all_failing_resource_ids_reported(self, all_ids, data):
        """Every plan whose rollback file is missing, empty, or whose hook exits
        non-zero MUST appear in the failures list. No plan is silently skipped."""
        # Decide which plans will fail (at least one must fail for this property)
        num_failing = data.draw(
            st.integers(min_value=1, max_value=len(all_ids)),
            label="num_failing",
        )
        failing_ids = set(all_ids[:num_failing])
        passing_ids = set(all_ids[num_failing:])

        # Draw failure mode per failing ID
        failure_mode_map = {}
        for fid in failing_ids:
            failure_mode_map[fid] = data.draw(failure_modes, label=f"mode_{fid}")

        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))
            orch = Orchestrator(project_root=project_dir, approver="test-user")

            rollbacks_dir = project_dir / "output" / "rollbacks"

            # Set up rollback files: passing plans get valid files, failing plans
            # get missing/empty based on their failure mode
            for rid in passing_ids:
                _create_rollback_file(rollbacks_dir, rid)

            # Hook-failure IDs need non-empty files (they fail at the hook stage)
            hook_fail_ids = set()
            for rid in failing_ids:
                mode = failure_mode_map[rid]
                if mode == "missing_file":
                    pass  # Don't create any file
                elif mode == "empty_file":
                    _create_empty_rollback_file(rollbacks_dir, rid)
                elif mode == "hook_nonzero":
                    _create_rollback_file(rollbacks_dir, rid)
                    hook_fail_ids.add(rid)

            plans = [_make_plan(rid) for rid in all_ids]

            # Mock subprocess.run to control hook outcomes
            def mock_subprocess_run(args, **kwargs):
                """Return exit 0 for passing IDs, non-zero for hook_fail IDs."""
                # Extract the rollback path from the command args
                # args = ["bash", hook_path, rollback_path]
                if len(args) >= 3:
                    rollback_path_str = args[2]
                    for rid in hook_fail_ids:
                        if rid in rollback_path_str:
                            return subprocess.CompletedProcess(
                                args=args, returncode=1, stdout="", stderr="hook failed"
                            )
                # Default: pass
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="", stderr=""
                )

            with patch("subprocess.run", side_effect=mock_subprocess_run):
                validated_paths, failures = orch._run_pre_remediation_hook_full(plans)

            # --- Assertions ---
            # Every failing resource_id MUST appear in the failures list
            for fid in failing_ids:
                assert fid in failures, (
                    f"Resource '{fid}' (failure_mode={failure_mode_map[fid]}) "
                    f"should be in failures but is not. "
                    f"failures={failures}, validated_paths={validated_paths}"
                )

            # Every plan must end up in EITHER validated_paths or failures
            # (no silent skip)
            validated_resource_ids = set()
            for vp in validated_paths:
                # Extract resource_id from path like .../rollbacks/<resource_id>.tf
                validated_resource_ids.add(vp.stem)

            for rid in all_ids:
                in_validated = rid in validated_resource_ids
                in_failures = rid in failures
                assert in_validated or in_failures, (
                    f"Resource '{rid}' is neither in validated_paths nor failures — "
                    f"it was silently skipped. "
                    f"validated_stems={validated_resource_ids}, failures={failures}"
                )

            # No resource should appear in BOTH validated and failures
            overlap = validated_resource_ids & set(failures)
            assert len(overlap) == 0, (
                f"Resources appear in BOTH validated and failures: {overlap}"
            )

    @given(
        resource_ids=unique_resource_id_lists(min_size=1, max_size=5),
    )
    @settings(
        max_examples=50,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_missing_files_always_reported(self, resource_ids):
        """When ALL rollback files are missing, every resource_id must appear
        in failures and validated_paths must be empty."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))
            orch = Orchestrator(project_root=project_dir, approver="test-user")

            # Don't create any rollback files
            plans = [_make_plan(rid) for rid in resource_ids]

            with patch("subprocess.run") as mock_run:
                validated_paths, failures = orch._run_pre_remediation_hook_full(plans)

            # subprocess.run should never be called if files don't exist
            mock_run.assert_not_called()

            # All resource IDs must be in failures
            assert len(failures) == len(resource_ids), (
                f"Expected {len(resource_ids)} failures, got {len(failures)}. "
                f"failures={failures}"
            )
            for rid in resource_ids:
                assert rid in failures, (
                    f"Resource '{rid}' missing from failures list. failures={failures}"
                )

            # No validated paths
            assert validated_paths == [], (
                f"Expected empty validated_paths when all files missing, "
                f"got: {validated_paths}"
            )


class TestProperty7PreRemediationHookSuccess:
    """Property 7: Pre-Remediation Hook Success.

    For any list of active plans where every plan has a corresponding non-empty
    rollback file that passes the hook script (exit code 0), the Pre_Remediation_Hook
    SHALL return a result with validated_paths containing one path per plan, and
    an empty failures list.
    """

    @given(
        resource_ids=unique_resource_id_lists(min_size=1, max_size=6),
    )
    @settings(
        max_examples=50,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_all_valid_plans_yield_full_validated_paths(self, resource_ids):
        """When all plans have non-empty rollback files and hook exits 0,
        validated_paths has one entry per plan and failures is empty."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))
            orch = Orchestrator(project_root=project_dir, approver="test-user")

            rollbacks_dir = project_dir / "output" / "rollbacks"

            # Create valid rollback files for all plans
            for rid in resource_ids:
                _create_rollback_file(rollbacks_dir, rid)

            plans = [_make_plan(rid) for rid in resource_ids]

            # Mock subprocess.run to always return exit 0
            hook_success = subprocess.CompletedProcess(
                args=["bash", "hook", "file"],
                returncode=0,
                stdout="",
                stderr="",
            )

            with patch("subprocess.run", return_value=hook_success):
                validated_paths, failures = orch._run_pre_remediation_hook_full(plans)

            # --- Assertions ---
            # Failures must be empty
            assert failures == [], (
                f"Expected empty failures when all plans are valid, got: {failures}"
            )

            # validated_paths must have exactly one entry per plan
            assert len(validated_paths) == len(resource_ids), (
                f"Expected {len(resource_ids)} validated paths, "
                f"got {len(validated_paths)}: {validated_paths}"
            )

            # Each validated path must correspond to a resource_id's rollback file
            validated_stems = {vp.stem for vp in validated_paths}
            for rid in resource_ids:
                assert rid in validated_stems, (
                    f"Resource '{rid}' not found in validated_paths stems. "
                    f"validated_stems={validated_stems}"
                )

            # Each validated path must point to an actual existing file
            for vp in validated_paths:
                assert vp.exists(), (
                    f"Validated path {vp} does not exist on disk"
                )
                assert vp.stat().st_size > 0, (
                    f"Validated path {vp} is empty (0 bytes)"
                )

    @given(
        resource_ids=unique_resource_id_lists(min_size=1, max_size=5),
        data=st.data(),
    )
    @settings(
        max_examples=50,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_validated_paths_are_correct_filesystem_paths(self, resource_ids, data):
        """Each path in validated_paths must be the exact rollbacks/<resource_id>.tf
        path for the corresponding plan."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))
            orch = Orchestrator(project_root=project_dir, approver="test-user")

            rollbacks_dir = project_dir / "output" / "rollbacks"

            # Create valid rollback files
            for rid in resource_ids:
                content = data.draw(rollback_hcl_content, label=f"content_{rid}")
                _create_rollback_file(rollbacks_dir, rid, content)

            plans = [_make_plan(rid) for rid in resource_ids]

            # Hook always passes
            hook_success = subprocess.CompletedProcess(
                args=["bash", "hook", "file"],
                returncode=0,
                stdout="",
                stderr="",
            )

            with patch("subprocess.run", return_value=hook_success):
                validated_paths, failures = orch._run_pre_remediation_hook_full(plans)

            # --- Assertions ---
            assert failures == [], (
                f"Expected empty failures, got: {failures}"
            )

            # Each validated path must be exactly rollbacks_dir / <resource_id>.tf
            expected_paths = {rollbacks_dir / f"{rid}.tf" for rid in resource_ids}
            actual_paths = set(validated_paths)
            assert actual_paths == expected_paths, (
                f"Validated paths mismatch.\n"
                f"Expected: {sorted(str(p) for p in expected_paths)}\n"
                f"Actual:   {sorted(str(p) for p in actual_paths)}"
            )
