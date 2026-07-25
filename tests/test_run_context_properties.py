"""Property-based tests for cloud_janitor.core.run_context.

Property 1: Run_ID Uniqueness and Sortability
  - Two calls separated by ≥1 s produce unique, lexicographically ordered strings.
  - Generating many IDs rapidly still yields all-unique values (UUID suffix guarantees).

Property 5: Retention Configuration Partition
  - Valid positive int env value → that value returned.
  - Invalid / zero / negative / unset → DEFAULT_RETENTION (20).
"""

from __future__ import annotations

import os
import time

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from cloud_janitor.core.run_context import generate_run_id, get_retention, DEFAULT_RETENTION


# ──────────────────────────────────────────────────────────────────────────────
# Property 1: Run_ID Uniqueness and Sortability
# ──────────────────────────────────────────────────────────────────────────────


class TestRunIdUniquenessAndSortability:
    """Property: IDs are unique and lexicographically ordered by generation time."""

    def test_two_calls_separated_by_one_second_are_unique_and_sorted(self):
        """Deterministic: 1 s gap guarantees different timestamps → ordered."""
        id1 = generate_run_id()
        time.sleep(1)
        id2 = generate_run_id()

        assert id1 != id2, "Two run IDs must be unique"
        assert id1 < id2, "Earlier ID must sort before later ID"

    @settings(deadline=None, max_examples=50)
    @given(st.integers(min_value=2, max_value=20))
    def test_batch_ids_all_unique(self, n: int):
        """Generating N IDs in rapid succession still yields N unique values."""
        ids = [generate_run_id() for _ in range(n)]
        assert len(set(ids)) == n, f"Expected {n} unique IDs, got {len(set(ids))}"

    def test_ids_are_sortable_strings(self):
        """All generated IDs are plain strings with the expected format."""
        id1 = generate_run_id()
        time.sleep(1)
        id2 = generate_run_id()

        # Format: YYYYMMDDTHHMMSSZ-<8hex>
        assert isinstance(id1, str)
        assert isinstance(id2, str)
        assert "-" in id1
        assert len(id1.split("-")) == 2
        ts_part, hex_part = id1.split("-")
        assert ts_part.endswith("Z")
        assert len(hex_part) == 8
        # Verify hex portion is valid hex
        int(hex_part, 16)

    def test_sorted_batch_matches_generation_order(self):
        """IDs generated over multiple seconds sort in generation order."""
        ids = []
        for _ in range(3):
            ids.append(generate_run_id())
            time.sleep(1)

        assert ids == sorted(ids), "Sorted IDs must match generation order"


# ──────────────────────────────────────────────────────────────────────────────
# Property 5: Retention Configuration Partition
# ──────────────────────────────────────────────────────────────────────────────


class TestRetentionConfigPartition:
    """Property: get_retention partitions input into valid-positive vs fallback-to-default."""

    @settings(deadline=None, max_examples=100)
    @given(st.integers(min_value=1, max_value=10_000))
    def test_valid_positive_int_returns_that_value(self, value: int):
        """Any valid positive integer in env → returned verbatim."""
        os.environ["TEST_RETENTION_PBT"] = str(value)
        try:
            assert get_retention("TEST_RETENTION_PBT") == value
        finally:
            del os.environ["TEST_RETENTION_PBT"]

    @settings(deadline=None, max_examples=50)
    @given(st.integers(max_value=0))
    def test_zero_or_negative_returns_default(self, value: int):
        """Zero or negative → fallback to DEFAULT_RETENTION."""
        os.environ["TEST_RETENTION_PBT"] = str(value)
        try:
            assert get_retention("TEST_RETENTION_PBT") == DEFAULT_RETENTION
        finally:
            del os.environ["TEST_RETENTION_PBT"]

    @settings(deadline=None, max_examples=50)
    @given(
        st.text(
            min_size=1,
            max_size=20,
            alphabet=st.characters(
                blacklist_categories=("Cs",),
                blacklist_characters="\x00",
            ),
        ).filter(lambda s: not s.strip().lstrip("-").isdigit())
    )
    def test_non_numeric_string_returns_default(self, value: str):
        """Non-numeric string → fallback to DEFAULT_RETENTION."""
        assume(value.strip() != "")
        os.environ["TEST_RETENTION_PBT"] = value
        try:
            assert get_retention("TEST_RETENTION_PBT") == DEFAULT_RETENTION
        finally:
            del os.environ["TEST_RETENTION_PBT"]

    def test_unset_returns_default(self):
        """Env var not set → DEFAULT_RETENTION."""
        # Ensure it's not set
        os.environ.pop("TEST_RETENTION_PBT", None)
        assert get_retention("TEST_RETENTION_PBT") == DEFAULT_RETENTION
