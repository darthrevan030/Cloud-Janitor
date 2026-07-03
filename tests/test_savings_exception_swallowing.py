"""Property-based tests for Orchestrator savings tracker exception swallowing.

**Validates: Requirements 8.1, 8.2, 8.3**

Property 5: Savings Tracker Exception Swallowing

For any Exception subclass raised by savings_tracker.record_run(), the Orchestrator
SHALL: (a) not propagate the exception, (b) log a WARNING containing the exception
type name and message, and (c) return ApprovalResult(success=True) identical to the
non-exception case.
"""

import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from agents.remediation_architect import RemediationPlan
from orchestrator import Orchestrator, ApprovalResult


# --- Strategies ---

# Filesystem-safe resource IDs (matching the allowlist pattern)
_fs_safe_chars = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)
fs_safe_resource_ids = st.text(_fs_safe_chars, min_size=1, max_size=40)

# Exception messages — arbitrary text (can be empty)
exception_messages = st.text(
    st.characters(min_codepoint=32, max_codepoint=126),
    min_size=0,
    max_size=200,
)


# Exception class strategy — diverse Exception subclasses
def _build_exception(exc_cls, msg):
    """Build an exception instance, handling special constructors."""
    if exc_cls is json.JSONDecodeError:
        return json.JSONDecodeError(msg or "test", doc="", pos=0)
    if exc_cls is KeyError:
        return KeyError(msg)
    if exc_cls is UnicodeDecodeError:
        return UnicodeDecodeError("utf-8", b"\xff", 0, 1, msg or "invalid byte")
    return exc_cls(msg)


# Well-known exception classes that must be caught
_EXCEPTION_CLASSES = [
    ValueError,
    KeyError,
    json.JSONDecodeError,
    FileNotFoundError,
    OSError,
    RuntimeError,
    TypeError,
    AttributeError,
    IndexError,
    PermissionError,
    IOError,
    UnicodeDecodeError,
]

exception_class_strategy = st.sampled_from(_EXCEPTION_CLASSES)


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


def _setup_orchestrator_for_approval(project_dir: Path, resource_id: str) -> Orchestrator:
    """Create an Orchestrator with a plan so approve() can reach the savings tracker call."""
    orch = Orchestrator(project_root=project_dir, approver="test-user")

    # Inject a remediation plan so approve() doesn't short-circuit at "no plan found"
    orch._last_plans = [
        RemediationPlan(
            resource_id=resource_id,
            finding={"resource_id": resource_id, "resource_type": "ebs"},
            blocked=False,
            remediation_hcl='resource "null_resource" "test" {}',
            rollback_hcl='resource "null_resource" "rollback" {}',
        ),
    ]

    # Create rollback artifact so rollback flow doesn't interfere
    rollback_file = project_dir / "output" / "rollbacks" / f"{resource_id}.tf"
    rollback_file.write_text('resource "null_resource" "rollback" {}')

    return orch


def _mock_subprocess_success(*args, **kwargs):
    """Simulate subprocess.run returning success (exit code 0)."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Success"
    mock_result.stderr = ""
    return mock_result


# --- Property Test ---


class TestProperty5SavingsExceptionSwallowing:
    """Property 5: Savings Tracker Exception Swallowing.

    For any Exception subclass raised by savings_tracker.record_run(), the Orchestrator
    SHALL: (a) not propagate the exception, (b) log a WARNING containing the exception
    type name and message, and (c) return ApprovalResult(success=True) identical to the
    non-exception case.
    """

    @given(
        resource_id=fs_safe_resource_ids,
        exc_cls=exception_class_strategy,
        exc_msg=exception_messages,
    )
    @settings(
        max_examples=100,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_exception_not_propagated_and_returns_success(
        self, resource_id, exc_cls, exc_msg
    ):
        """Any Exception raised by savings_tracker.record_run() must be caught.

        The approve() method must return ApprovalResult(success=True, resource_id=resource_id)
        and never propagate the exception to the caller.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))
            orch = _setup_orchestrator_for_approval(project_dir, resource_id)

            # Build the exception to raise
            exc_instance = _build_exception(exc_cls, exc_msg)

            # Mock subprocess to succeed (so we reach the savings tracker call)
            # Mock the savings_tracker.record_run to raise the exception
            with patch("subprocess.run", side_effect=_mock_subprocess_success), \
                 patch.object(
                     orch._savings_tracker, "record_run",
                     side_effect=exc_instance,
                 ):
                # This MUST NOT raise — the exception should be swallowed
                result = orch.approve(f"APPROVE {resource_id}")

            # (a) Exception not propagated — we reached here without raising
            # (c) Returns ApprovalResult(success=True) with correct resource_id
            assert isinstance(result, ApprovalResult), (
                f"Expected ApprovalResult, got {type(result).__name__}"
            )
            assert result.success is True, (
                f"Expected success=True when savings_tracker raises {exc_cls.__name__}, "
                f"got success={result.success}, error={result.error}"
            )
            assert result.resource_id == resource_id, (
                f"Expected resource_id='{resource_id}', got '{result.resource_id}'"
            )

    @given(
        resource_id=fs_safe_resource_ids,
        exc_cls=exception_class_strategy,
        exc_msg=exception_messages,
    )
    @settings(
        max_examples=100,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_warning_logged_with_exception_details(
        self, resource_id, exc_cls, exc_msg
    ):
        """A WARNING-level log message must be emitted containing the exception
        type name and message when savings_tracker.record_run() raises.

        **Validates: Requirements 8.2**
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))
            orch = _setup_orchestrator_for_approval(project_dir, resource_id)

            exc_instance = _build_exception(exc_cls, exc_msg)

            with patch("subprocess.run", side_effect=_mock_subprocess_success), \
                 patch.object(
                     orch._savings_tracker, "record_run",
                     side_effect=exc_instance,
                 ), \
                 patch("orchestrator.logging.getLogger") as mock_get_logger:
                mock_logger = MagicMock()
                mock_get_logger.return_value = mock_logger

                result = orch.approve(f"APPROVE {resource_id}")

            # (b) Verify WARNING was logged
            assert mock_logger.warning.called, (
                f"Expected a WARNING log when savings_tracker raises {exc_cls.__name__}, "
                f"but no warning was logged"
            )

            # Verify the WARNING contains the exception type name
            warning_call_args = mock_logger.warning.call_args
            # The format string uses: "Savings tracking failed (%s): %s"
            # with type(e).__name__ and str(e) as positional args
            formatted_msg = warning_call_args[0][0] % warning_call_args[0][1:]
            assert exc_cls.__name__ in formatted_msg, (
                f"WARNING message should contain exception type '{exc_cls.__name__}', "
                f"got: '{formatted_msg}'"
            )

    @given(
        resource_id=fs_safe_resource_ids,
    )
    @settings(
        max_examples=50,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_result_identical_to_non_exception_case(self, resource_id):
        """The ApprovalResult when an exception occurs must be identical to
        what would be returned when no exception occurs.

        Both cases must return ApprovalResult(success=True, resource_id=<id>)
        with all other fields at their defaults.

        **Validates: Requirements 8.3**
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))

            # --- Case 1: No exception (savings tracker succeeds) ---
            orch_ok = _setup_orchestrator_for_approval(project_dir, resource_id)
            with patch("subprocess.run", side_effect=_mock_subprocess_success), \
                 patch.object(orch_ok._savings_tracker, "record_run", return_value=True):
                result_ok = orch_ok.approve(f"APPROVE {resource_id}")

            # --- Case 2: Exception raised ---
            # Reset gate state by creating a fresh orchestrator in a new dir
            project_dir2 = _make_project_dirs(Path(tmp_dir) / "run2")
            orch_exc = _setup_orchestrator_for_approval(project_dir2, resource_id)
            with patch("subprocess.run", side_effect=_mock_subprocess_success), \
                 patch.object(
                     orch_exc._savings_tracker, "record_run",
                     side_effect=RuntimeError("simulated failure"),
                 ):
                result_exc = orch_exc.approve(f"APPROVE {resource_id}")

            # Both must be successful
            assert result_ok.success is True
            assert result_exc.success is True

            # Both must have the same resource_id
            assert result_ok.resource_id == result_exc.resource_id == resource_id

            # Both must have identical field values (no error, not locked, etc.)
            assert result_ok.error == result_exc.error, (
                f"error field differs: ok={result_ok.error}, exc={result_exc.error}"
            )
            assert result_ok.locked == result_exc.locked, (
                f"locked field differs: ok={result_ok.locked}, exc={result_exc.locked}"
            )
            assert result_ok.expected_format == result_exc.expected_format, (
                f"expected_format differs: ok={result_ok.expected_format}, exc={result_exc.expected_format}"
            )
            assert result_ok.attempts_remaining == result_exc.attempts_remaining, (
                f"attempts_remaining differs: ok={result_ok.attempts_remaining}, exc={result_exc.attempts_remaining}"
            )

    @given(
        resource_id=fs_safe_resource_ids,
        exc_msg=exception_messages,
    )
    @settings(
        max_examples=50,
        deadline=15000,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_custom_exception_subclass_also_caught(self, resource_id, exc_msg):
        """Custom Exception subclasses (not just built-in ones) must also be caught.

        This verifies the except clause uses `Exception` (the broad base class)
        and not a specific list of exception types.
        """
        # Define a custom exception class that would NOT be caught by narrow handlers
        class CustomSavingsError(Exception):
            pass

        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))
            orch = _setup_orchestrator_for_approval(project_dir, resource_id)

            with patch("subprocess.run", side_effect=_mock_subprocess_success), \
                 patch.object(
                     orch._savings_tracker, "record_run",
                     side_effect=CustomSavingsError(exc_msg),
                 ):
                result = orch.approve(f"APPROVE {resource_id}")

            assert result.success is True, (
                f"Custom exception subclass should be caught, "
                f"but got success={result.success}, error={result.error}"
            )
            assert result.resource_id == resource_id
