"""Property-based tests for TF_CMD validation logic.

**Validates: Requirements 2.1, 2.2, 2.3**

Uses Hypothesis to verify the validation partition property:
validation passes iff the value has no path separators AND its basename
is in the allowlist {"terraform", "tflocal"}.
"""

import os
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from orchestrator import _validate_tf_cmd, TF_CMD_ALLOWLIST


# --- Strategies ---

# Characters that are valid in env vars: no path separators, no null bytes, no surrogates
_non_separator_chars = st.characters(
    blacklist_characters="/\\\x00",
    blacklist_categories=("Cs",),  # exclude surrogates
)

# Characters safe for env vars (no null bytes, no surrogates)
_safe_chars = st.characters(
    blacklist_characters="\x00",
    blacklist_categories=("Cs",),
)

# Strings guaranteed to contain at least one path separator
_strings_with_separator = st.one_of(
    # Contains forward slash
    st.tuples(
        st.text(alphabet=_safe_chars, min_size=0, max_size=20),
        st.just("/"),
        st.text(alphabet=_safe_chars, min_size=0, max_size=20),
    ).map(lambda t: t[0] + t[1] + t[2]),
    # Contains backslash
    st.tuples(
        st.text(alphabet=_safe_chars, min_size=0, max_size=20),
        st.just("\\"),
        st.text(alphabet=_safe_chars, min_size=0, max_size=20),
    ).map(lambda t: t[0] + t[1] + t[2]),
)

# Strings with no path separators that are NOT in the allowlist
_non_allowlist_no_sep = st.text(
    alphabet=_non_separator_chars, min_size=1, max_size=50
).filter(lambda s: s not in TF_CMD_ALLOWLIST)


# --- Property 2: TF_CMD Validation Partition ---


class TestProperty2TFCMDValidationPartition:
    """Property 2: TF_CMD Validation Partition.

    For any string value of TF_CMD, validation SHALL pass if and only if
    the string contains no path separator characters (/ or \\) AND its
    basename is a member of the allowlist {"terraform", "tflocal"}.
    All other values SHALL raise a RuntimeError identifying the rejected
    value and permitted alternatives.
    """

    @given(binary_name=st.sampled_from(sorted(TF_CMD_ALLOWLIST)))
    @settings(max_examples=200)
    def test_valid_allowlist_members_pass_validation(self, binary_name):
        """Any allowlisted name with no separators SHALL pass validation."""
        with patch.dict(os.environ, {"TF_CMD": binary_name}):
            with patch("shutil.which", return_value=f"/usr/local/bin/{binary_name}"):
                result = _validate_tf_cmd()
                assert result == f"/usr/local/bin/{binary_name}"

    @given(raw=_strings_with_separator)
    @settings(max_examples=200)
    def test_path_separators_raise_runtime_error(self, raw):
        """Any string containing / or \\ SHALL raise RuntimeError."""
        with patch.dict(os.environ, {"TF_CMD": raw}):
            try:
                _validate_tf_cmd()
                raise AssertionError(
                    f"Expected RuntimeError for input with separator: {raw!r}"
                )
            except RuntimeError as e:
                error_msg = str(e)
                # Must identify the rejected value
                assert raw in error_msg or "path separators" in error_msg
                # Must mention permitted alternatives
                assert "terraform" in error_msg or "tflocal" in error_msg

    @given(raw=_non_allowlist_no_sep)
    @settings(max_examples=200)
    def test_non_allowlist_names_raise_runtime_error(self, raw):
        """Any name not in allowlist (without separators) SHALL raise RuntimeError."""
        assume(len(raw.strip()) > 0)  # skip empty/whitespace-only
        with patch.dict(os.environ, {"TF_CMD": raw}):
            try:
                _validate_tf_cmd()
                raise AssertionError(
                    f"Expected RuntimeError for non-allowlist input: {raw!r}"
                )
            except RuntimeError as e:
                error_msg = str(e)
                # Must identify the rejected value
                assert raw in error_msg or "not in the allowlist" in error_msg
                # Must mention permitted values
                assert "terraform" in error_msg or "tflocal" in error_msg
