"""Tests for agents.approval_gate module."""

import pytest

from agents.approval_gate import (
    ApprovalGate,
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
