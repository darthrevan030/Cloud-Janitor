"""Tests for _extract_resource_id_from_command with allowlist validation.

Validates: Requirements 9.1, 9.2, 9.3
"""

import logging

import pytest

from orchestrator import Orchestrator, _RESOURCE_ID_PATTERN


@pytest.fixture
def orch():
    """Minimal Orchestrator instance for testing extraction method."""
    return Orchestrator.__new__(Orchestrator)


class TestResourceIdPattern:
    """Verify the regex constant is correctly defined."""

    def test_pattern_accepts_alphanumeric(self):
        assert _RESOURCE_ID_PATTERN.match("vol-abc123")

    def test_pattern_accepts_hyphens(self):
        assert _RESOURCE_ID_PATTERN.match("my-resource-id")

    def test_pattern_accepts_underscores(self):
        assert _RESOURCE_ID_PATTERN.match("my_resource_id")

    def test_pattern_accepts_colons(self):
        assert _RESOURCE_ID_PATTERN.match("arn:aws:ec2:us-east-1:123456789012:volume/vol-abc123")

    def test_pattern_accepts_periods(self):
        assert _RESOURCE_ID_PATTERN.match("my.resource.id")

    def test_pattern_accepts_forward_slashes(self):
        assert _RESOURCE_ID_PATTERN.match("path/to/resource")

    def test_pattern_rejects_spaces(self):
        assert _RESOURCE_ID_PATTERN.match("has space") is None

    def test_pattern_rejects_semicolons(self):
        assert _RESOURCE_ID_PATTERN.match("id;rm -rf /") is None

    def test_pattern_rejects_shell_metacharacters(self):
        assert _RESOURCE_ID_PATTERN.match("id$(whoami)") is None

    def test_pattern_rejects_empty_string(self):
        assert _RESOURCE_ID_PATTERN.match("") is None

    def test_pattern_rejects_over_256_chars(self):
        assert _RESOURCE_ID_PATTERN.match("a" * 257) is None

    def test_pattern_accepts_exactly_256_chars(self):
        assert _RESOURCE_ID_PATTERN.match("a" * 256)

    def test_pattern_accepts_single_char(self):
        assert _RESOURCE_ID_PATTERN.match("x")


class TestExtractResourceIdFromCommand:
    """Tests for _extract_resource_id_from_command method."""

    def test_valid_extraction(self, orch):
        result = orch._extract_resource_id_from_command("APPROVE vol-abc123", "APPROVE")
        assert result == "vol-abc123"

    def test_wrong_prefix_returns_none(self, orch):
        result = orch._extract_resource_id_from_command("REJECT vol-abc123", "APPROVE")
        assert result is None

    def test_empty_after_prefix_returns_none(self, orch):
        result = orch._extract_resource_id_from_command("APPROVE ", "APPROVE")
        assert result is None

    def test_whitespace_only_after_prefix_returns_none(self, orch):
        """Req 9.3: whitespace-only candidate returns None."""
        result = orch._extract_resource_id_from_command("APPROVE    ", "APPROVE")
        assert result is None

    def test_tab_whitespace_returns_none(self, orch):
        """Req 9.3: tab characters as whitespace return None."""
        result = orch._extract_resource_id_from_command("APPROVE \t\t", "APPROVE")
        assert result is None

    def test_space_in_resource_id_rejected(self, orch):
        """Req 9.1: spaces are not in the allowlist pattern."""
        result = orch._extract_resource_id_from_command("APPROVE vol abc", "APPROVE")
        assert result is None

    def test_shell_injection_rejected(self, orch):
        """Req 9.1: shell metacharacters are not allowed."""
        result = orch._extract_resource_id_from_command("APPROVE vol;rm -rf /", "APPROVE")
        assert result is None

    def test_command_substitution_rejected(self, orch):
        """Req 9.1: $(cmd) patterns are not allowed."""
        result = orch._extract_resource_id_from_command("APPROVE $(whoami)", "APPROVE")
        assert result is None

    def test_backtick_injection_rejected(self, orch):
        result = orch._extract_resource_id_from_command("APPROVE `id`", "APPROVE")
        assert result is None

    def test_pipe_rejected(self, orch):
        result = orch._extract_resource_id_from_command("APPROVE vol|cat /etc/passwd", "APPROVE")
        assert result is None

    def test_valid_arn_format(self, orch):
        """Req 9.1: colons, hyphens, slashes, periods all allowed."""
        arn = "arn:aws:ec2:us-east-1:123456789012:volume/vol-abc123"
        result = orch._extract_resource_id_from_command(f"APPROVE {arn}", "APPROVE")
        assert result == arn

    def test_over_256_chars_rejected(self, orch):
        """Req 9.1: total length must be 1-256."""
        long_id = "a" * 257
        result = orch._extract_resource_id_from_command(f"APPROVE {long_id}", "APPROVE")
        assert result is None

    def test_exactly_256_chars_accepted(self, orch):
        id_256 = "a" * 256
        result = orch._extract_resource_id_from_command(f"APPROVE {id_256}", "APPROVE")
        assert result == id_256

    def test_debug_log_on_rejection(self, orch, caplog):
        """Req 9.2: rejected values produce DEBUG log with truncated value."""
        with caplog.at_level(logging.DEBUG):
            result = orch._extract_resource_id_from_command("APPROVE bad;value", "APPROVE")
        assert result is None
        assert "Rejected resource ID" in caplog.text
        assert "bad;value" in caplog.text

    def test_debug_log_truncates_to_64_chars(self, orch, caplog):
        """Req 9.2: logged rejected value is truncated to 64 characters."""
        long_invalid = "x" * 100 + "!"  # 101 chars, invalid due to '!'
        with caplog.at_level(logging.DEBUG):
            result = orch._extract_resource_id_from_command(f"APPROVE {long_invalid}", "APPROVE")
        assert result is None
        # The logged value should be truncated to 64 chars
        assert "Rejected resource ID" in caplog.text
        # Verify the full 101-char string is NOT in the log
        assert long_invalid not in caplog.text

    def test_no_log_on_empty_candidate(self, orch, caplog):
        """Req 9.3: empty/whitespace returns None WITHOUT invoking regex (no log)."""
        with caplog.at_level(logging.DEBUG):
            result = orch._extract_resource_id_from_command("APPROVE ", "APPROVE")
        assert result is None
        assert "Rejected resource ID" not in caplog.text

    def test_rollback_prefix(self, orch):
        """Works with ROLLBACK prefix too."""
        result = orch._extract_resource_id_from_command("ROLLBACK vol-xyz789", "ROLLBACK")
        assert result == "vol-xyz789"

    def test_prefix_must_match_exactly(self, orch):
        """Prefix check is exact - 'APPROVE' prefix won't match 'APPROVED'."""
        result = orch._extract_resource_id_from_command("APPROVED vol-abc", "APPROVE")
        # 'APPROVED vol-abc' starts with 'APPROVE ' at index 7, 
        # but the command is 'APPROVED vol-abc' which starts with 'APPROVE ' -> 'D vol-abc'
        # Actually "APPROVED vol-abc".startswith("APPROVE ") is False (8th char is 'D' not ' ')
        assert result is None
