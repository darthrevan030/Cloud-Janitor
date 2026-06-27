"""Tests for agents.approval_gate module."""

import pytest

from agents.approval_gate import (
    ApprovalGate,
    RollbackGate,
    parse_approval,
    parse_confirm_rollback,
    parse_rollback,
)


# --- parse_approval ---


class TestParseApproval:
    """Tests for the parse_approval function."""

    def test_valid_approval(self):
        result = parse_approval("APPROVE vol-abc123", "vol-abc123")
        assert result == {"valid": True, "resource_id": "vol-abc123"}

    def test_valid_approval_complex_id(self):
        result = parse_approval("APPROVE sg-0a1b2c3d4e5f6g7h8", "sg-0a1b2c3d4e5f6g7h8")
        assert result == {"valid": True, "resource_id": "sg-0a1b2c3d4e5f6g7h8"}

    def test_lowercase_command_rejected(self):
        result = parse_approval("approve vol-abc123", "vol-abc123")
        assert result["valid"] is False
        assert "expected_format" in result

    def test_mixed_case_command_rejected(self):
        result = parse_approval("Approve vol-abc123", "vol-abc123")
        assert result["valid"] is False

    def test_leading_whitespace_rejected(self):
        result = parse_approval(" APPROVE vol-abc123", "vol-abc123")
        assert result["valid"] is False
        assert "whitespace" in result["error"]

    def test_trailing_whitespace_rejected(self):
        result = parse_approval("APPROVE vol-abc123 ", "vol-abc123")
        assert result["valid"] is False
        assert "whitespace" in result["error"]

    def test_double_space_rejected(self):
        result = parse_approval("APPROVE  vol-abc123", "vol-abc123")
        assert result["valid"] is False

    def test_resource_id_mismatch(self):
        result = parse_approval("APPROVE vol-wrong", "vol-abc123")
        assert result["valid"] is False
        assert "mismatch" in result["error"].lower()

    def test_empty_input(self):
        result = parse_approval("", "vol-abc123")
        assert result["valid"] is False

    def test_command_only_no_id(self):
        result = parse_approval("APPROVE", "vol-abc123")
        assert result["valid"] is False

    def test_command_with_space_but_no_id(self):
        result = parse_approval("APPROVE ", "vol-abc123")
        assert result["valid"] is False
        assert "whitespace" in result["error"]

    def test_expected_format_in_error(self):
        result = parse_approval("bad", "vol-abc123")
        assert result["expected_format"] == "APPROVE vol-abc123"


# --- parse_rollback ---


class TestParseRollback:
    """Tests for the parse_rollback function."""

    def test_valid_rollback(self):
        result = parse_rollback("ROLLBACK sg-123", "sg-123")
        assert result == {"valid": True, "resource_id": "sg-123"}

    def test_lowercase_rejected(self):
        result = parse_rollback("rollback sg-123", "sg-123")
        assert result["valid"] is False

    def test_resource_id_mismatch(self):
        result = parse_rollback("ROLLBACK sg-wrong", "sg-123")
        assert result["valid"] is False

    def test_leading_whitespace_rejected(self):
        result = parse_rollback(" ROLLBACK sg-123", "sg-123")
        assert result["valid"] is False

    def test_expected_format_in_error(self):
        result = parse_rollback("bad", "sg-123")
        assert result["expected_format"] == "ROLLBACK sg-123"


# --- parse_confirm_rollback ---


class TestParseConfirmRollback:
    """Tests for the parse_confirm_rollback function."""

    def test_valid_confirm_rollback(self):
        result = parse_confirm_rollback("CONFIRM ROLLBACK sg-123", "sg-123")
        assert result == {"valid": True, "resource_id": "sg-123"}

    def test_lowercase_rejected(self):
        result = parse_confirm_rollback("confirm rollback sg-123", "sg-123")
        assert result["valid"] is False

    def test_partial_lowercase_rejected(self):
        result = parse_confirm_rollback("CONFIRM rollback sg-123", "sg-123")
        assert result["valid"] is False

    def test_resource_id_mismatch(self):
        result = parse_confirm_rollback("CONFIRM ROLLBACK sg-wrong", "sg-123")
        assert result["valid"] is False

    def test_missing_confirm(self):
        result = parse_confirm_rollback("ROLLBACK sg-123", "sg-123")
        assert result["valid"] is False

    def test_expected_format_in_error(self):
        result = parse_confirm_rollback("bad", "sg-123")
        assert result["expected_format"] == "CONFIRM ROLLBACK sg-123"


# --- ApprovalGate class ---


class TestApprovalGate:
    """Tests for the ApprovalGate class."""

    def test_successful_approval_no_attempt_count(self):
        gate = ApprovalGate(max_attempts=3)
        result = gate.attempt_approval("APPROVE vol-123", "vol-123")
        assert result == {"valid": True, "resource_id": "vol-123"}
        assert gate.attempts == 0
        assert gate.locked is False

    def test_failed_attempt_increments_counter(self):
        gate = ApprovalGate(max_attempts=3)
        result = gate.attempt_approval("bad input", "vol-123")
        assert result["valid"] is False
        assert result["attempts_remaining"] == 2
        assert gate.attempts == 1

    def test_locks_after_max_attempts(self):
        gate = ApprovalGate(max_attempts=3)
        gate.attempt_approval("bad", "vol-123")
        gate.attempt_approval("bad", "vol-123")
        result = gate.attempt_approval("bad", "vol-123")
        assert result == {"valid": False, "error": "Max attempts exceeded", "locked": True}
        assert gate.locked is True

    def test_locked_gate_rejects_valid_input(self):
        gate = ApprovalGate(max_attempts=1)
        gate.attempt_approval("bad", "vol-123")
        result = gate.attempt_approval("APPROVE vol-123", "vol-123")
        assert result["valid"] is False
        assert result["locked"] is True

    def test_reset_unlocks_gate(self):
        gate = ApprovalGate(max_attempts=1)
        gate.attempt_approval("bad", "vol-123")
        assert gate.locked is True
        gate.reset()
        assert gate.locked is False
        assert gate.attempts == 0
        result = gate.attempt_approval("APPROVE vol-123", "vol-123")
        assert result["valid"] is True

    def test_custom_max_attempts(self):
        gate = ApprovalGate(max_attempts=5)
        for _ in range(4):
            result = gate.attempt_approval("bad", "vol-123")
            assert result["valid"] is False
            assert "locked" not in result
        # 5th attempt locks
        result = gate.attempt_approval("bad", "vol-123")
        assert result["locked"] is True

    def test_default_max_attempts_is_three(self):
        gate = ApprovalGate()
        assert gate.max_attempts == 3


# --- RollbackGate class ---


class TestRollbackGate:
    """Tests for the RollbackGate two-step rollback protocol."""

    def test_valid_two_step_flow(self):
        """Full valid flow: ROLLBACK then CONFIRM ROLLBACK."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)
        assert gate.state == "awaiting_rollback"

        # Step 1: ROLLBACK
        result = gate.process_input("ROLLBACK sg-123")
        assert result["valid"] is True
        assert result["state"] == "awaiting_confirmation"
        assert gate.state == "awaiting_confirmation"

        # Step 2: CONFIRM ROLLBACK
        result = gate.process_input("CONFIRM ROLLBACK sg-123")
        assert result["valid"] is True
        assert result["state"] == "confirmed"
        assert result["resource_id"] == "sg-123"
        assert gate.state == "confirmed"

    def test_invalid_rollback_wrong_format(self):
        """Invalid input at step 1 — wrong command format."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)
        result = gate.process_input("rollback sg-123")
        assert result["valid"] is False
        assert result["state"] == "awaiting_rollback"
        assert result["attempts_remaining"] == 2

    def test_invalid_rollback_wrong_id(self):
        """Invalid input at step 1 — wrong resource ID."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)
        result = gate.process_input("ROLLBACK sg-wrong")
        assert result["valid"] is False
        assert "mismatch" in result["error"].lower()
        assert result["state"] == "awaiting_rollback"

    def test_invalid_rollback_case_sensitive(self):
        """Case sensitivity enforced at step 1."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)
        result = gate.process_input("Rollback sg-123")
        assert result["valid"] is False

    def test_invalid_confirm_wrong_format(self):
        """Invalid input at step 2 — wrong command format."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)
        gate.process_input("ROLLBACK sg-123")  # Pass step 1

        result = gate.process_input("confirm rollback sg-123")
        assert result["valid"] is False
        assert result["state"] == "awaiting_confirmation"
        assert result["attempts_remaining"] == 2

    def test_invalid_confirm_wrong_id(self):
        """Invalid input at step 2 — wrong resource ID."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)
        gate.process_input("ROLLBACK sg-123")  # Pass step 1

        result = gate.process_input("CONFIRM ROLLBACK sg-wrong")
        assert result["valid"] is False
        assert "mismatch" in result["error"].lower()
        assert result["state"] == "awaiting_confirmation"

    def test_attempts_tracked_across_steps(self):
        """Failed attempts accumulate across both steps."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)

        # 1 failure in step 1
        gate.process_input("bad input")
        assert gate.attempts == 1

        # Pass step 1
        gate.process_input("ROLLBACK sg-123")
        assert gate.state == "awaiting_confirmation"
        assert gate.attempts == 1  # Successes don't increment

        # 1 failure in step 2
        gate.process_input("bad confirm")
        assert gate.attempts == 2

        # 3rd failure locks
        result = gate.process_input("bad again")
        assert result["valid"] is False
        assert result["locked"] is True
        assert gate.locked is True

    def test_locks_after_max_attempts_step1(self):
        """Gate locks after max_attempts failures all in step 1."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)
        gate.process_input("bad1")
        gate.process_input("bad2")
        result = gate.process_input("bad3")
        assert result == {
            "valid": False,
            "error": "Max attempts exceeded",
            "locked": True,
            "state": "locked",
        }
        assert gate.locked is True

    def test_locks_after_max_attempts_step2(self):
        """Gate locks after max_attempts failures split across steps."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=2)
        gate.process_input("bad")  # 1 failure in step 1
        gate.process_input("ROLLBACK sg-123")  # Pass step 1
        result = gate.process_input("bad confirm")  # 2nd failure in step 2
        assert result["locked"] is True
        assert gate.state == "locked"

    def test_locked_gate_rejects_valid_input(self):
        """Once locked, even valid input is rejected."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=1)
        gate.process_input("bad")  # Lock immediately
        result = gate.process_input("ROLLBACK sg-123")
        assert result["valid"] is False
        assert result["locked"] is True

    def test_confirm_before_rollback_rejected(self):
        """CONFIRM ROLLBACK without prior ROLLBACK is invalid."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)
        # In awaiting_rollback state, "CONFIRM ROLLBACK" doesn't match "ROLLBACK" format
        result = gate.process_input("CONFIRM ROLLBACK sg-123")
        assert result["valid"] is False
        assert result["state"] == "awaiting_rollback"

    def test_reset_restores_initial_state(self):
        """Reset clears state, attempts, and unlocks."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)
        gate.process_input("ROLLBACK sg-123")
        gate.process_input("bad confirm")
        assert gate.state == "awaiting_confirmation"
        assert gate.attempts == 1

        gate.reset()
        assert gate.state == "awaiting_rollback"
        assert gate.attempts == 0
        assert gate.locked is False

    def test_reset_after_lock(self):
        """Reset unlocks a locked gate."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=1)
        gate.process_input("bad")
        assert gate.locked is True

        gate.reset()
        assert gate.locked is False
        result = gate.process_input("ROLLBACK sg-123")
        assert result["valid"] is True

    def test_confirmed_state_is_idempotent(self):
        """Once confirmed, process_input returns confirmed state."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)
        gate.process_input("ROLLBACK sg-123")
        gate.process_input("CONFIRM ROLLBACK sg-123")
        assert gate.state == "confirmed"

        # Calling again returns confirmed
        result = gate.process_input("anything")
        assert result["valid"] is True
        assert result["state"] == "confirmed"

    def test_default_max_attempts_is_three(self):
        """Default max_attempts is 3."""
        gate = RollbackGate(resource_id="sg-123")
        assert gate.max_attempts == 3

    def test_direct_attempt_rollback(self):
        """attempt_rollback() can be called directly."""
        gate = RollbackGate(resource_id="vol-abc", max_attempts=3)
        result = gate.attempt_rollback("ROLLBACK vol-abc")
        assert result["valid"] is True
        assert gate.state == "awaiting_confirmation"

    def test_direct_attempt_confirm(self):
        """attempt_confirm() can be called directly after step 1."""
        gate = RollbackGate(resource_id="vol-abc", max_attempts=3)
        gate.attempt_rollback("ROLLBACK vol-abc")
        result = gate.attempt_confirm("CONFIRM ROLLBACK vol-abc")
        assert result["valid"] is True
        assert gate.state == "confirmed"

    def test_expected_format_in_step1_error(self):
        """Step 1 errors include the expected format."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)
        result = gate.process_input("bad")
        assert result["expected_format"] == "ROLLBACK sg-123"

    def test_expected_format_in_step2_error(self):
        """Step 2 errors include the expected format."""
        gate = RollbackGate(resource_id="sg-123", max_attempts=3)
        gate.process_input("ROLLBACK sg-123")
        result = gate.process_input("bad")
        assert result["expected_format"] == "CONFIRM ROLLBACK sg-123"
