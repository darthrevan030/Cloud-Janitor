r"""Unit tests for TF_CMD PATH resolution and validation.

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5

Tests cover:
- PATH resolution with mocked shutil.which returning a valid path
- PATH resolution with mocked shutil.which returning None (binary not found)
- Rejection of values containing / or \ path separators
- Rejection of basename not in allowlist
"""

import os
from unittest.mock import patch

import pytest

from orchestrator import _validate_tf_cmd, TF_CMD_ALLOWLIST


class TestTFCMDPathResolution:
    """Tests for PATH resolution via shutil.which."""

    def test_resolves_valid_binary_on_path(self):
        """When shutil.which returns a valid path, _validate_tf_cmd returns it."""
        expected_path = "/usr/local/bin/terraform"
        with patch.dict(os.environ, {"TF_CMD": "terraform"}):
            with patch("shutil.which", return_value=expected_path):
                result = _validate_tf_cmd()
                assert result == expected_path

    def test_resolves_tflocal_on_path(self):
        """When shutil.which resolves tflocal, _validate_tf_cmd returns the resolved path."""
        expected_path = "/home/user/.local/bin/tflocal"
        with patch.dict(os.environ, {"TF_CMD": "tflocal"}):
            with patch("shutil.which", return_value=expected_path):
                result = _validate_tf_cmd()
                assert result == expected_path

    def test_raises_when_binary_not_found_on_path(self):
        """When shutil.which returns None, _validate_tf_cmd raises RuntimeError."""
        with patch.dict(os.environ, {"TF_CMD": "terraform"}):
            with patch("shutil.which", return_value=None):
                with pytest.raises(RuntimeError) as exc_info:
                    _validate_tf_cmd()
                error_msg = str(exc_info.value)
                assert "terraform" in error_msg
                assert "could not be found on PATH" in error_msg

    def test_raises_when_tflocal_not_found_on_path(self):
        """When tflocal is not on PATH, _validate_tf_cmd raises RuntimeError."""
        with patch.dict(os.environ, {"TF_CMD": "tflocal"}):
            with patch("shutil.which", return_value=None):
                with pytest.raises(RuntimeError) as exc_info:
                    _validate_tf_cmd()
                error_msg = str(exc_info.value)
                assert "tflocal" in error_msg
                assert "could not be found on PATH" in error_msg

    def test_default_tf_cmd_is_tflocal(self):
        """When TF_CMD is not set, defaults to 'tflocal'."""
        env = os.environ.copy()
        env.pop("TF_CMD", None)
        expected_path = "/usr/bin/tflocal"
        with patch.dict(os.environ, env, clear=True):
            with patch("shutil.which", return_value=expected_path):
                result = _validate_tf_cmd()
                assert result == expected_path


class TestTFCMDPathSeparatorRejection:
    """Tests for rejection of values containing path separators."""

    def test_rejects_forward_slash(self):
        """TF_CMD with forward slash is rejected."""
        with patch.dict(os.environ, {"TF_CMD": "/usr/bin/terraform"}):
            with pytest.raises(RuntimeError) as exc_info:
                _validate_tf_cmd()
            error_msg = str(exc_info.value)
            assert "path separators" in error_msg
            assert "/usr/bin/terraform" in error_msg

    def test_rejects_backslash(self):
        """TF_CMD with backslash is rejected."""
        with patch.dict(os.environ, {"TF_CMD": "C:\\Program Files\\terraform"}):
            with pytest.raises(RuntimeError) as exc_info:
                _validate_tf_cmd()
            error_msg = str(exc_info.value)
            assert "path separators" in error_msg

    def test_rejects_relative_path_with_forward_slash(self):
        """TF_CMD with relative forward-slash path is rejected."""
        with patch.dict(os.environ, {"TF_CMD": "./bin/terraform"}):
            with pytest.raises(RuntimeError) as exc_info:
                _validate_tf_cmd()
            error_msg = str(exc_info.value)
            assert "path separators" in error_msg
            assert "Only bare binary names are permitted" in error_msg

    def test_rejects_relative_path_with_backslash(self):
        """TF_CMD with relative backslash path is rejected."""
        with patch.dict(os.environ, {"TF_CMD": ".\\bin\\tflocal"}):
            with pytest.raises(RuntimeError) as exc_info:
                _validate_tf_cmd()
            error_msg = str(exc_info.value)
            assert "path separators" in error_msg

    def test_separator_rejection_mentions_permitted_values(self):
        """Error message for separator rejection lists permitted binary names."""
        with patch.dict(os.environ, {"TF_CMD": "/opt/terraform"}):
            with pytest.raises(RuntimeError) as exc_info:
                _validate_tf_cmd()
            error_msg = str(exc_info.value)
            assert "terraform" in error_msg
            assert "tflocal" in error_msg


class TestTFCMDAllowlistRejection:
    """Tests for rejection of basenames not in the allowlist."""

    def test_rejects_evil_binary(self):
        """A binary named 'evil_binary' is rejected."""
        with patch.dict(os.environ, {"TF_CMD": "evil_binary"}):
            with pytest.raises(RuntimeError) as exc_info:
                _validate_tf_cmd()
            error_msg = str(exc_info.value)
            assert "evil_binary" in error_msg
            assert "not in the allowlist" in error_msg

    def test_rejects_arbitrary_name(self):
        """A binary named 'rm' is rejected."""
        with patch.dict(os.environ, {"TF_CMD": "rm"}):
            with pytest.raises(RuntimeError) as exc_info:
                _validate_tf_cmd()
            error_msg = str(exc_info.value)
            assert "rm" in error_msg
            assert "not in the allowlist" in error_msg

    def test_rejects_similar_name_not_in_allowlist(self):
        """A binary named 'terraform2' (not exact match) is rejected."""
        with patch.dict(os.environ, {"TF_CMD": "terraform2"}):
            with pytest.raises(RuntimeError) as exc_info:
                _validate_tf_cmd()
            error_msg = str(exc_info.value)
            assert "terraform2" in error_msg
            assert "not in the allowlist" in error_msg

    def test_rejects_empty_string(self):
        """An empty TF_CMD value is rejected (not in allowlist)."""
        with patch.dict(os.environ, {"TF_CMD": ""}):
            with pytest.raises(RuntimeError) as exc_info:
                _validate_tf_cmd()
            # Empty string is not in the allowlist
            assert "not in the allowlist" in str(exc_info.value)

    def test_allowlist_rejection_mentions_permitted_values(self):
        """Error message for non-allowlist values lists permitted binary names."""
        with patch.dict(os.environ, {"TF_CMD": "bash"}):
            with pytest.raises(RuntimeError) as exc_info:
                _validate_tf_cmd()
            error_msg = str(exc_info.value)
            # Must mention the permitted alternatives
            assert "terraform" in error_msg
            assert "tflocal" in error_msg


class TestTFCMDAllowlistConstant:
    """Tests verifying the allowlist constant is correctly defined."""

    def test_allowlist_contains_terraform(self):
        """Allowlist must contain 'terraform'."""
        assert "terraform" in TF_CMD_ALLOWLIST

    def test_allowlist_contains_tflocal(self):
        """Allowlist must contain 'tflocal'."""
        assert "tflocal" in TF_CMD_ALLOWLIST

    def test_allowlist_is_a_set(self):
        """Allowlist must be a set for O(1) membership checks."""
        assert isinstance(TF_CMD_ALLOWLIST, set)
