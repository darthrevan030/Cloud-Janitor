"""Tests for agents.approval_gate — approval/rollback command parsing."""

import pytest

from agents.approval_gate import (
    MAX_APPROVAL_ATTEMPTS,
    ApprovalResult,
    CommandValidationError,
    RollbackResult,
    parse_approval,
    parse_rollback,
    validate_command,
)


class TestValidateCommand:
    """Tests for the validate_command function."""

    def test_valid_approve(self):
        cmd_type, resource_id = validate_command("APPROVE vol-123")
        assert cmd_type == "approve"
        assert resource_id == "vol-123"

    def test_valid_rollback(self):
        cmd_type, resource_id = validate_command("ROLLBACK vol-123")
        assert cmd_type == "rollback"
        assert resource_id == "vol-123"

    def test_valid_confirm_rollback(self):
        cmd_type, resource_id = validate_command("CONFIRM ROLLBACK vol-123")
        assert cmd_type == "confirm_rollback"
        assert resource_id == "vol-123"

    def test_empty_string_rejected(self):
        with pytest.raises(CommandValidationError):
            validate_command("")

    def test_lowercase_approve_rejected(self):
        with pytest.raises(CommandValidationError):
            validate_command("approve vol-123")

    def test_mixed_case_approve_rejected(self):
        with pytest.raises(CommandValidationError):
            validate_command("Approve vol-123")

    def test_extra_space_rejected(self):
        with pytest.raises(CommandValidationError):
            validate_command("APPROVE  vol-123")

    def test_trailing_space_rejected(self):
        with pytest.raises(CommandValidationError):
            validate_command("APPROVE vol-123 ")

    def test_leading_space_rejected(self):
        with pytest.raises(CommandValidationError):
            validate_command(" APPROVE vol-123")

    def test_missing_resource_id_rejected(self):
        with pytest.raises(CommandValidationError):
            validate_command("APPROVE")

    def test_unknown_command_rejected(self):
        with pytest.raises(CommandValidationError):
            validate_command("DELETE vol-123")

    def test_complex_resource_id(self):
        cmd_type, resource_id = validate_command("APPROVE cache-prod-legacy")
        assert cmd_type == "approve"
        assert resource_id == "cache-prod-legacy"

    def test_resource_id_with_numbers(self):
        cmd_type, resource_id = validate_command("APPROVE sg-0abc123def456")
        assert cmd_type == "approve"
        assert resource_id == "sg-0abc123def456"


class TestParseApproval:
    """Tests for the parse_approval function."""

    def test_valid_approval_accepted(self):
        result = parse_approval("APPROVE vol-123", "vol-123")
        assert result.approved is True
        assert result.resource_id == "vol-123"
        assert result.error is None
        assert result.attempts_remaining == MAX_APPROVAL_ATTEMPTS

    def test_case_sensitivity_rejection(self):
        result = parse_approval("approve vol-123", "vol-123")
        assert result.approved is False
        assert result.error is not None

    def test_extra_whitespace_rejection(self):
        result = parse_approval("APPROVE  vol-123", "vol-123")
        assert result.approved is False
        assert result.error is not None

    def test_trailing_whitespace_rejection(self):
        result = parse_approval("APPROVE vol-123 ", "vol-123")
        assert result.approved is False
        assert result.error is not None

    def test_missing_resource_id_rejection(self):
        result = parse_approval("APPROVE", "vol-123")
        assert result.approved is False
        assert result.error is not None

    def test_wrong_resource_id_rejection(self):
        result = parse_approval("APPROVE vol-999", "vol-123")
        assert result.approved is False
        assert result.resource_id == "vol-999"
        assert "mismatch" in result.error.lower()

    def test_empty_string_rejection(self):
        result = parse_approval("", "vol-123")
        assert result.approved is False
        assert result.error is not None

    def test_retry_counting_decrements(self):
        result = parse_approval("bad input", "vol-123", attempts_remaining=3)
        assert result.attempts_remaining == 2

    def test_retry_counting_from_2(self):
        result = parse_approval("bad input", "vol-123", attempts_remaining=2)
        assert result.attempts_remaining == 1

    def test_retry_counting_from_1(self):
        result = parse_approval("bad input", "vol-123", attempts_remaining=1)
        assert result.attempts_remaining == 0

    def test_successful_approval_does_not_decrement(self):
        result = parse_approval("APPROVE vol-123", "vol-123", attempts_remaining=2)
        assert result.approved is True
        assert result.attempts_remaining == 2

    def test_default_attempts_is_max(self):
        result = parse_approval("bad input", "vol-123")
        assert result.attempts_remaining == MAX_APPROVAL_ATTEMPTS - 1

    def test_rollback_command_rejected_for_approval(self):
        result = parse_approval("ROLLBACK vol-123", "vol-123")
        assert result.approved is False
        assert result.error is not None


class TestParseRollback:
    """Tests for the parse_rollback function."""

    def test_valid_rollback_initiation(self):
        result = parse_rollback("ROLLBACK vol-123", "vol-123")
        assert result.confirmed is False
        assert result.resource_id == "vol-123"
        assert result.phase == "initiate"
        assert result.error is None

    def test_valid_rollback_confirmation(self):
        result = parse_rollback("CONFIRM ROLLBACK vol-123", "vol-123")
        assert result.confirmed is True
        assert result.resource_id == "vol-123"
        assert result.phase == "confirm"
        assert result.error is None

    def test_lowercase_rollback_rejected(self):
        result = parse_rollback("rollback vol-123", "vol-123")
        assert result.confirmed is False
        assert result.error is not None

    def test_wrong_resource_id_initiate(self):
        result = parse_rollback("ROLLBACK vol-999", "vol-123")
        assert result.confirmed is False
        assert result.resource_id == "vol-999"
        assert result.phase == "initiate"
        assert "mismatch" in result.error.lower()

    def test_wrong_resource_id_confirm(self):
        result = parse_rollback("CONFIRM ROLLBACK vol-999", "vol-123")
        assert result.confirmed is False
        assert result.resource_id == "vol-999"
        assert result.phase == "confirm"
        assert "mismatch" in result.error.lower()

    def test_empty_string_rejected(self):
        result = parse_rollback("", "vol-123")
        assert result.confirmed is False
        assert result.error is not None

    def test_approve_command_rejected_for_rollback(self):
        result = parse_rollback("APPROVE vol-123", "vol-123")
        assert result.confirmed is False
        assert result.error is not None

    def test_extra_whitespace_rejected(self):
        result = parse_rollback("ROLLBACK  vol-123", "vol-123")
        assert result.confirmed is False
        assert result.error is not None

    def test_trailing_whitespace_rejected(self):
        result = parse_rollback("CONFIRM ROLLBACK vol-123 ", "vol-123")
        assert result.confirmed is False
        assert result.error is not None
