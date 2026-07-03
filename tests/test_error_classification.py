"""Tests for Orchestrator._classify_error() method (Requirement 12.3).

Validates error classification into exactly one of:
- "terraform_failure" — non-zero exit from TF_CMD contexts
- "io_failure" — filesystem or network I/O errors (OSError hierarchy)
- "validation_failure" — schema, gate, hook, or resource_id validation errors
- "agent_failure" — default fallback for unclassified exceptions
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator import Orchestrator


@pytest.fixture
def tmp_project(tmp_path):
    """Set up a temporary project structure for testing."""
    (tmp_path / "hooks").mkdir(parents=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True)
    (tmp_path / "output" / "logs").mkdir(parents=True)
    (tmp_path / "output" / "policies").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def orchestrator(tmp_project):
    """Create an Orchestrator instance with mocked TF_CMD."""
    with patch("orchestrator._validate_tf_cmd", return_value="/usr/bin/tflocal"):
        return Orchestrator(project_root=tmp_project)


# ─── Terraform failure classification ─────────────────────────────────────


class TestTerraformFailureClassification:
    """context in {"tf_validate", "tf_apply", "tf_plan"} → "terraform_failure"."""

    def test_tf_validate_context(self, orchestrator):
        """context='tf_validate' classifies as terraform_failure."""
        exc = RuntimeError("terraform validate failed with exit code 1")
        result = orchestrator._classify_error(exc, context="tf_validate")
        assert result == "terraform_failure"

    def test_tf_apply_context(self, orchestrator):
        """context='tf_apply' classifies as terraform_failure."""
        exc = RuntimeError("terraform apply timed out")
        result = orchestrator._classify_error(exc, context="tf_apply")
        assert result == "terraform_failure"

    def test_tf_plan_context(self, orchestrator):
        """context='tf_plan' classifies as terraform_failure."""
        exc = ValueError("unexpected plan output")
        result = orchestrator._classify_error(exc, context="tf_plan")
        assert result == "terraform_failure"

    def test_tf_context_takes_priority_over_io_exception(self, orchestrator):
        """Terraform context wins over OSError isinstance check."""
        exc = OSError("disk full during terraform apply")
        result = orchestrator._classify_error(exc, context="tf_apply")
        assert result == "terraform_failure"


# ─── IO failure classification ─────────────────────────────────────────────


class TestIOFailureClassification:
    """isinstance(exc, (OSError, IOError, PermissionError)) → "io_failure"."""

    def test_oserror_classified_as_io_failure(self, orchestrator):
        """OSError without terraform context classifies as io_failure."""
        exc = OSError("No such file or directory")
        result = orchestrator._classify_error(exc, context="")
        assert result == "io_failure"

    def test_ioerror_classified_as_io_failure(self, orchestrator):
        """IOError (alias of OSError) classifies as io_failure."""
        exc = IOError("read failed")
        result = orchestrator._classify_error(exc, context="")
        assert result == "io_failure"

    def test_permission_error_classified_as_io_failure(self, orchestrator):
        """PermissionError (subclass of OSError) classifies as io_failure."""
        exc = PermissionError("access denied to /output/logs/audit.log")
        result = orchestrator._classify_error(exc, context="")
        assert result == "io_failure"

    def test_file_not_found_error_classified_as_io_failure(self, orchestrator):
        """FileNotFoundError (subclass of OSError) classifies as io_failure."""
        exc = FileNotFoundError("findings_store.json not found")
        result = orchestrator._classify_error(exc, context="")
        assert result == "io_failure"

    def test_oserror_with_unknown_context(self, orchestrator):
        """OSError with a non-terraform context still classifies as io_failure."""
        exc = OSError("broken pipe")
        result = orchestrator._classify_error(exc, context="unknown_step")
        assert result == "io_failure"


# ─── Validation failure classification ─────────────────────────────────────


class TestValidationFailureClassification:
    """context in {"schema_check", "gate_check", "hook_validation", "resource_id_check"} → "validation_failure"."""

    def test_schema_check_context(self, orchestrator):
        """context='schema_check' classifies as validation_failure."""
        exc = ValueError("schema_version field is missing")
        result = orchestrator._classify_error(exc, context="schema_check")
        assert result == "validation_failure"

    def test_gate_check_context(self, orchestrator):
        """context='gate_check' classifies as validation_failure."""
        exc = RuntimeError("approval gate locked for vol-001")
        result = orchestrator._classify_error(exc, context="gate_check")
        assert result == "validation_failure"

    def test_hook_validation_context(self, orchestrator):
        """context='hook_validation' classifies as validation_failure."""
        exc = RuntimeError("pre-remediation hook failed for sg-123")
        result = orchestrator._classify_error(exc, context="hook_validation")
        assert result == "validation_failure"

    def test_resource_id_check_context(self, orchestrator):
        """context='resource_id_check' classifies as validation_failure."""
        exc = ValueError("rejected resource ID: ../../../../etc/passwd")
        result = orchestrator._classify_error(exc, context="resource_id_check")
        assert result == "validation_failure"


# ─── Default agent_failure classification ──────────────────────────────────


class TestAgentFailureClassification:
    """Default fallback for unknown context + non-IO exception → "agent_failure"."""

    def test_unknown_context_non_io_exception(self, orchestrator):
        """Unknown context with non-IO exception defaults to agent_failure."""
        exc = RuntimeError("agent crashed unexpectedly")
        result = orchestrator._classify_error(exc, context="unknown_context")
        assert result == "agent_failure"

    def test_empty_context_non_io_exception(self, orchestrator):
        """Empty context with non-IO exception defaults to agent_failure."""
        exc = ValueError("invalid finding format")
        result = orchestrator._classify_error(exc, context="")
        assert result == "agent_failure"

    def test_no_context_non_io_exception(self, orchestrator):
        """No context argument with non-IO exception defaults to agent_failure."""
        exc = KeyError("missing_key")
        result = orchestrator._classify_error(exc)
        assert result == "agent_failure"

    def test_type_error_defaults_to_agent_failure(self, orchestrator):
        """TypeError with empty context defaults to agent_failure."""
        exc = TypeError("expected str, got int")
        result = orchestrator._classify_error(exc, context="")
        assert result == "agent_failure"

    def test_attribute_error_defaults_to_agent_failure(self, orchestrator):
        """AttributeError with no terraform/validation context → agent_failure."""
        exc = AttributeError("'NoneType' object has no attribute 'findings'")
        result = orchestrator._classify_error(exc, context="agent_scan")
        assert result == "agent_failure"


# ─── Priority ordering verification ───────────────────────────────────────


class TestClassificationPriority:
    """Verify that classification priority follows the documented order:
    1. Terraform context (highest)
    2. IO exception isinstance check
    3. Validation context
    4. Default agent_failure (lowest)
    """

    def test_terraform_context_beats_validation_context_overlap(self, orchestrator):
        """If both tf and validation contexts existed, tf wins (not possible in practice)."""
        # Only one context string is passed, but this confirms terraform is checked first
        exc = RuntimeError("overlap scenario")
        assert orchestrator._classify_error(exc, context="tf_validate") == "terraform_failure"
        assert orchestrator._classify_error(exc, context="schema_check") == "validation_failure"

    def test_io_exception_beats_validation_context(self, orchestrator):
        """OSError with validation context: IO check runs before validation context check."""
        exc = OSError("permission denied reading gate file")
        result = orchestrator._classify_error(exc, context="gate_check")
        # IO check (isinstance) runs after terraform context but before validation context
        # Based on implementation: tf context → isinstance → validation context → default
        assert result == "io_failure"

    def test_return_value_is_always_a_valid_category(self, orchestrator):
        """Every return value must be from the valid set of categories."""
        valid_categories = {
            "terraform_failure", "io_failure", "validation_failure", "agent_failure"
        }
        test_cases = [
            (RuntimeError("x"), "tf_validate"),
            (OSError("y"), ""),
            (ValueError("z"), "schema_check"),
            (KeyError("w"), "random"),
            (RuntimeError("a"), "tf_apply"),
            (RuntimeError("b"), "hook_validation"),
        ]
        for exc, context in test_cases:
            result = orchestrator._classify_error(exc, context)
            assert result in valid_categories, (
                f"Got '{result}' for ({type(exc).__name__}, context='{context}') "
                f"which is not in {valid_categories}"
            )
