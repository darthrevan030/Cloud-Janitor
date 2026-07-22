"""Property-based tests for core/timeouts.py — timeout validation.

Property 5: Timeout Validation Partition
- Valid positive int → returned as-is (within ceiling)
- Above ceiling (TF/hook vars only) → clamped to 1800
- Invalid/negative/zero → falls back to default
- JANITOR_LLM_TIMEOUT is exempt from ceiling

Validates: Requirements 2.3, 2.4
"""

from __future__ import annotations

import os
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.core.timeouts import _CEILING_SECONDS, _DEFAULTS, get_timeout


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# All timeout var names that are subject to the ceiling
_clamped_names = [
    "JANITOR_TF_INIT_TIMEOUT",
    "JANITOR_TF_APPLY_TIMEOUT",
    "JANITOR_TF_VALIDATE_TIMEOUT",
    "JANITOR_HOOK_TIMEOUT",
]

_all_names = list(_DEFAULTS.keys())

# Strategy for valid positive integers within the ceiling
valid_within_ceiling = st.integers(min_value=1, max_value=_CEILING_SECONDS)

# Strategy for positive integers above the ceiling
above_ceiling = st.integers(min_value=_CEILING_SECONDS + 1, max_value=10_000_000)

# Strategy for invalid string values (non-numeric, won't parse as int)
# Exclude null bytes for Windows compatibility
invalid_strings = st.one_of(
    st.text(
        min_size=1,
        max_size=20,
        alphabet=st.characters(
            whitelist_categories=("L", "P", "Z"),
            blacklist_characters="\x00",
        ),
    ).filter(lambda s: not s.lstrip("-").isdigit()),
    st.just(""),
    st.just(" "),
    st.just("1.5"),
    st.just("10s"),
    st.just("NaN"),
)

# Strategy for zero or negative integers
non_positive_integers = st.integers(min_value=-10_000_000, max_value=0)


# ---------------------------------------------------------------------------
# Property 5: Timeout Validation Partition
# ---------------------------------------------------------------------------


class TestTimeoutValidationPartition:
    """Property 5: get_timeout() partitions inputs into exactly 3 outcomes:
    1. Valid int within ceiling → returned as-is
    2. Valid int above ceiling (TF/hook only) → clamped to 1800
    3. Invalid/zero/negative/unset → default value
    """

    @settings(max_examples=100, deadline=None)
    @given(
        name=st.sampled_from(_all_names),
        value=valid_within_ceiling,
    )
    def test_valid_int_within_ceiling_returned_as_is(self, name, value):
        """A positive integer at or below 1800 is always returned unchanged."""
        with patch.dict(os.environ, {name: str(value)}, clear=False):
            result = get_timeout(name)

        assert result == value

    @settings(max_examples=100, deadline=None)
    @given(
        name=st.sampled_from(_clamped_names),
        value=above_ceiling,
    )
    def test_above_ceiling_clamped_for_tf_hook_vars(self, name, value):
        """For TF/hook vars, values above 1800 are always clamped to 1800."""
        with patch.dict(os.environ, {name: str(value)}, clear=False):
            result = get_timeout(name)

        assert result == _CEILING_SECONDS

    @settings(max_examples=100, deadline=None)
    @given(value=above_ceiling)
    def test_above_ceiling_not_clamped_for_llm_timeout(self, value):
        """JANITOR_LLM_TIMEOUT is exempt from the ceiling — any positive int is returned."""
        with patch.dict(os.environ, {"JANITOR_LLM_TIMEOUT": str(value)}, clear=False):
            result = get_timeout("JANITOR_LLM_TIMEOUT")

        assert result == value

    @settings(max_examples=100, deadline=None)
    @given(
        name=st.sampled_from(_all_names),
        bad_value=invalid_strings,
    )
    def test_non_numeric_falls_back_to_default(self, name, bad_value):
        """Non-numeric strings always fall back to the default."""
        with patch.dict(os.environ, {name: bad_value}, clear=False):
            result = get_timeout(name)

        assert result == _DEFAULTS[name]

    @settings(max_examples=100, deadline=None)
    @given(
        name=st.sampled_from(_all_names),
        value=non_positive_integers,
    )
    def test_zero_or_negative_falls_back_to_default(self, name, value):
        """Zero or negative integers always fall back to the default."""
        with patch.dict(os.environ, {name: str(value)}, clear=False):
            result = get_timeout(name)

        assert result == _DEFAULTS[name]

    @settings(max_examples=50, deadline=None)
    @given(name=st.sampled_from(_all_names))
    def test_unset_env_var_returns_default(self, name):
        """When the env var is not set at all, the default is returned."""
        env_copy = os.environ.copy()
        env_copy.pop(name, None)
        with patch.dict(os.environ, env_copy, clear=True):
            result = get_timeout(name)

        assert result == _DEFAULTS[name]

    @settings(max_examples=50, deadline=None)
    @given(
        name=st.sampled_from(_all_names),
        value=valid_within_ceiling,
    )
    def test_return_type_is_always_int(self, name, value):
        """get_timeout() always returns an int, never a string or float."""
        with patch.dict(os.environ, {name: str(value)}, clear=False):
            result = get_timeout(name)

        assert isinstance(result, int)

    @settings(max_examples=50, deadline=None)
    @given(name=st.sampled_from(_all_names))
    def test_return_is_always_positive(self, name):
        """The returned timeout is always a positive integer (never 0 or negative)."""
        env_copy = os.environ.copy()
        env_copy.pop(name, None)
        with patch.dict(os.environ, env_copy, clear=True):
            result = get_timeout(name)

        assert result > 0
